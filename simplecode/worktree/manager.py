"""Git worktree lifecycle manager with persistent active-session state."""

from __future__ import annotations

import asyncio
import shutil
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from simplecode.worktree.changes import inspect_worktree_changes, run_git
from simplecode.worktree.models import (
    CleanupResult,
    SetupReport,
    Worktree,
    WorktreeChanges,
    WorktreeSession,
)
from simplecode.worktree.session import (
    load_session,
    read_worktree_head,
    read_worktree_head_sha,
    save_session,
)
from simplecode.worktree.setup import perform_post_creation_setup
from simplecode.worktree.slug import branch_for_worktree, validate_worktree_name


class WorktreeError(RuntimeError):
    """Base lifecycle error returned to UI/tools."""


class WorktreeInUseError(WorktreeError):
    """Raised when an operation conflicts with the currently entered worktree."""


class WorktreeHasChangesError(WorktreeError):
    """Raised when safe removal would discard work."""

    def __init__(self, name: str, changes: WorktreeChanges) -> None:
        self.name = name
        self.changes = changes
        files = changes.uncommitted_files
        commits = max(changes.commits_ahead, changes.unpushed_commits)
        details: list[str] = []
        if files:
            details.append(f"{files} uncommitted {'file' if files == 1 else 'files'}")
        if commits:
            details.append(f"{commits} {'commit' if commits == 1 else 'commits'}")
        if changes.check_failed:
            details.append(changes.error or "Git change check failed")
        super().__init__(
            f"worktree {name!r} has protected changes: " + ", ".join(details or ["unknown"])
        )


class WorktreeManager:
    """Own worktree creation/removal without changing process-global CWD."""

    def __init__(
        self,
        repo_root: str | Path,
        *,
        worktree_dir: str | Path | None = None,
        file_cache: Any | None = None,
        symlink_directories: tuple[str, ...] | list[str] = (),
        session_file: str | Path | None = None,
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.worktree_dir = (
            Path(worktree_dir).resolve()
            if worktree_dir is not None
            else self.repo_root / ".simplecode" / "worktrees"
        )
        self.session_file = (
            Path(session_file).resolve()
            if session_file is not None
            else self.repo_root / ".simplecode" / "worktree_session.json"
        )
        self.file_cache = file_cache
        self.symlink_directories = tuple(symlink_directories)
        self.active: dict[str, Worktree] = {}
        self.current_session: WorktreeSession | None = None
        self.last_setup_report = SetupReport()
        self._lock = asyncio.Lock()
        self._cache_clear_callbacks: list[Callable[[], None]] = []
        self._work_dir_callbacks: list[Callable[[Path], None]] = []

    def add_cache_clear_callback(self, callback: Callable[[], None]) -> None:
        self._cache_clear_callbacks.append(callback)

    def add_work_dir_callback(self, callback: Callable[[Path], None]) -> None:
        self._work_dir_callbacks.append(callback)

    def _clear_all_caches(self) -> None:
        clear = getattr(self.file_cache, "clear", None)
        if callable(clear):
            clear()
        for callback in tuple(self._cache_clear_callbacks):
            try:
                callback()
            except Exception:
                continue

    def _notify_work_dir(self, path: Path) -> None:
        self._clear_all_caches()
        for callback in tuple(self._work_dir_callbacks):
            try:
                callback(path.resolve())
            except Exception:
                continue

    def _target_path(self, name: str) -> Path:
        validated = validate_worktree_name(name)
        target = (self.worktree_dir / Path(validated)).resolve()
        try:
            target.relative_to(self.worktree_dir.resolve())
        except ValueError as exc:
            raise WorktreeError("resolved worktree path escaped the managed directory") from exc
        return target

    def is_git_repository(self) -> bool:
        result = run_git(self.repo_root, "rev-parse", "--show-toplevel")
        return result.returncode == 0

    @staticmethod
    def read_worktree_head_sha(worktree_path: str | Path) -> str | None:
        return read_worktree_head_sha(worktree_path)

    def _run_git(self, *args: str, cwd: str | Path | None = None) -> Any:
        return run_git(cwd or self.repo_root, *args, timeout=60.0)

    def _resolve_base_head(self, base: str) -> str:
        result = run_git(self.repo_root, "rev-parse", "--verify", f"{base}^{{commit}}")
        if result.returncode != 0:
            raise WorktreeError(result.stderr.strip() or f"unknown Git base: {base}")
        return result.stdout.strip()

    async def create(self, name: str, base: str = "HEAD") -> Worktree:
        """Create a branch worktree or recover an existing valid directory without Git."""

        validated = validate_worktree_name(name)
        async with self._lock:
            existing = self.active.get(validated)
            if existing is not None:
                return existing
            target = self._target_path(validated)
            branch = branch_for_worktree(validated)
            if target.exists():
                recovered = read_worktree_head(target)
                if recovered is None:
                    raise WorktreeError(
                        f"existing path is not a recoverable Git worktree: {target}"
                    )
                head, recovered_branch = recovered
                base_head = read_worktree_head_sha(self.repo_root) or head
                worktree = Worktree(
                    validated,
                    target,
                    recovered_branch or branch,
                    base,
                    base_head,
                    datetime.fromtimestamp(target.stat().st_mtime, tz=UTC),
                )
                self.active[validated] = worktree
                return worktree

            base_head = await asyncio.to_thread(self._resolve_base_head, base)
            self.worktree_dir.mkdir(parents=True, exist_ok=True)
            target.parent.mkdir(parents=True, exist_ok=True)
            result = await asyncio.to_thread(
                run_git,
                self.repo_root,
                "worktree",
                "add",
                "-B",
                branch,
                str(target),
                base_head,
            )
            if result.returncode != 0:
                if target.exists():
                    shutil.rmtree(target, ignore_errors=True)
                raise WorktreeError(result.stderr.strip() or "git worktree add failed")
            worktree = Worktree(validated, target, branch, base, base_head)
            self.active[validated] = worktree
            self.last_setup_report = await asyncio.to_thread(
                perform_post_creation_setup,
                self.repo_root,
                target,
                symlink_directories=self.symlink_directories,
            )
            return worktree

    def list_worktrees(self) -> list[Worktree]:
        return sorted(self.active.values(), key=lambda item: item.name.casefold())

    async def enter(self, name: str) -> Worktree:
        validated = validate_worktree_name(name)
        if self.current_session is not None:
            if self.current_session.worktree_name == validated:
                return self.active.get(validated, self.current_session.worktree)
            raise WorktreeInUseError(
                f"already inside {self.current_session.worktree_name!r}; exit it first"
            )
        worktree = self.active.get(validated)
        if worktree is None:
            target = self._target_path(validated)
            recovered = read_worktree_head(target)
            if recovered is None:
                raise WorktreeError(f"unknown worktree: {validated}")
            head, branch = recovered
            worktree = Worktree(
                validated, target, branch or branch_for_worktree(validated), "HEAD", head
            )
            self.active[validated] = worktree
        branch_result = await asyncio.to_thread(
            run_git,
            self.repo_root,
            "branch",
            "--show-current",
        )
        head_result = await asyncio.to_thread(run_git, self.repo_root, "rev-parse", "HEAD")
        session = WorktreeSession(
            original_cwd=self.repo_root,
            worktree_path=worktree.path,
            worktree_name=worktree.name,
            original_branch=branch_result.stdout.strip() or "HEAD",
            original_head_commit=head_result.stdout.strip() or worktree.head_commit,
        )
        self.current_session = session
        save_session(self.session_file, session)
        self._notify_work_dir(worktree.path)
        return worktree

    def status(self) -> WorktreeSession | None:
        return self.current_session

    async def _remove_worktree(self, worktree: Worktree) -> None:
        result = await asyncio.to_thread(
            run_git,
            self.repo_root,
            "worktree",
            "remove",
            "--force",
            str(worktree.path),
        )
        if result.returncode != 0 and worktree.path.exists():
            raise WorktreeError(result.stderr.strip() or "git worktree remove failed")
        await asyncio.sleep(0.1)
        branch_result = await asyncio.to_thread(
            run_git,
            self.repo_root,
            "branch",
            "-D",
            worktree.branch,
        )
        if branch_result.returncode != 0 and "not found" not in branch_result.stderr.casefold():
            raise WorktreeError(branch_result.stderr.strip() or "git branch cleanup failed")
        self.active.pop(worktree.name, None)

    async def remove(self, name: str, *, discard: bool = False) -> None:
        validated = validate_worktree_name(name)
        if self.current_session and self.current_session.worktree_name == validated:
            raise WorktreeInUseError("exit the current worktree before removing it")
        worktree = self.active.get(validated)
        if worktree is None:
            target = self._target_path(validated)
            recovered = read_worktree_head(target)
            if recovered is None:
                raise WorktreeError(f"unknown worktree: {validated}")
            head, branch = recovered
            worktree = Worktree(
                validated, target, branch or branch_for_worktree(validated), "HEAD", head
            )
        if not discard:
            changes = await asyncio.to_thread(
                inspect_worktree_changes,
                worktree.path,
                worktree.head_commit,
            )
            if changes.has_changes:
                raise WorktreeHasChangesError(validated, changes)
        async with self._lock:
            await self._remove_worktree(worktree)

    async def exit(
        self,
        name: str | None = None,
        action: str | None = None,
        discard_changes: bool = False,
        *,
        remove: bool | None = None,
        discard: bool | None = None,
    ) -> Worktree:
        session = self.current_session
        if session is None:
            raise WorktreeError("not inside a managed worktree")
        if name is not None and validate_worktree_name(name) != session.worktree_name:
            raise WorktreeError(f"active worktree is {session.worktree_name!r}, not {name!r}")
        if action not in {None, "keep", "remove"}:
            raise WorktreeError("action must be 'keep' or 'remove'")
        should_remove = action == "remove" if remove is None else remove
        should_discard = discard_changes if discard is None else discard
        worktree = self.active.get(session.worktree_name, session.worktree)
        changes = await asyncio.to_thread(
            inspect_worktree_changes,
            worktree.path,
            worktree.head_commit,
        )
        if should_remove and changes.has_changes and not should_discard:
            raise WorktreeHasChangesError(worktree.name, changes)
        self.current_session = None
        save_session(self.session_file, None)
        self._notify_work_dir(session.original_cwd)
        if should_remove:
            async with self._lock:
                await self._remove_worktree(worktree)
        return worktree

    async def auto_cleanup(
        self,
        worktree: Worktree | str,
        head_commit: str | None = None,
    ) -> CleanupResult:
        """Remove a clean isolated-agent worktree; preserve any observable work."""

        if isinstance(worktree, str):
            name = validate_worktree_name(worktree)
            record = self.active.get(name)
            if record is None:
                target = self._target_path(name)
                recovered = read_worktree_head(target)
                if recovered is None:
                    raise WorktreeError(f"unknown worktree: {name}")
                current_head, branch = recovered
                record = Worktree(
                    name,
                    target,
                    branch or branch_for_worktree(name),
                    "HEAD",
                    head_commit or current_head,
                )
            worktree = record
        base_head = head_commit or worktree.head_commit
        changes = await asyncio.to_thread(
            inspect_worktree_changes,
            worktree.path,
            base_head,
        )
        if changes.has_changes:
            return CleanupResult(True, worktree.path, worktree.branch)
        async with self._lock:
            await self._remove_worktree(worktree)
        return CleanupResult(False)

    def get_current_session(self) -> WorktreeSession | None:
        return self.current_session

    def restore_session(self) -> WorktreeSession | None:
        session = load_session(self.session_file)
        if session is None:
            return None
        actual_head = read_worktree_head_sha(session.worktree_path)
        if actual_head is None:
            save_session(self.session_file, None)
            return None
        recovered = read_worktree_head(session.worktree_path)
        branch = recovered[1] if recovered is not None else ""
        worktree = Worktree(
            session.worktree_name,
            session.worktree_path,
            branch or branch_for_worktree(session.worktree_name),
            session.original_branch,
            session.original_head_commit,
        )
        self.active[worktree.name] = worktree
        self.current_session = session
        self._notify_work_dir(session.worktree_path)
        return session


__all__ = [
    "WorktreeError",
    "WorktreeHasChangesError",
    "WorktreeInUseError",
    "WorktreeManager",
]
