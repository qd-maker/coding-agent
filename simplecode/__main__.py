"""`python -m simplecode` entry point."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from simplecode import __version__
from simplecode.agents.tool_filter import TEAMMATE_COORDINATION_TOOLS
from simplecode.app import SimpleCodeApp
from simplecode.client import AuthenticationError, create_client
from simplecode.config import ConfigurationError, load_config
from simplecode.hooks import HookConfigError, HookEngine, load_hooks
from simplecode.permissions import PermissionMode
from simplecode.teams.transcript import save_transcript
from simplecode.tools import ToolRegistry
from simplecode.tools.agent_tool import TEAMMATE_ADDENDUM


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Simple Code terminal AI assistant")
    parser.add_argument("--config", help="Path to a Simple Code YAML configuration")
    parser.add_argument("-p", "--print", action="store_true", dest="print_mode")
    parser.add_argument("--work-dir", help="Working directory for non-interactive teammate mode")
    parser.add_argument("--agent-type", default="", help="Agent definition for teammate mode")
    parser.add_argument("--model", default="", help="Model override for teammate mode")
    parser.add_argument("prompt", nargs="?", default="")
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Restore an entered Git worktree session after an interrupted process",
    )
    parser.add_argument("--version", action="version", version=f"Simple Code {__version__}")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    team_name = os.getenv("SIMPLECODE_TEAM_NAME") or None
    if args.print_mode and not args.prompt and not team_name:
        print("A prompt is required with -p.", file=sys.stderr)
        return 2
    try:
        config = load_config(args.config)
        hook_engine = HookEngine(load_hooks(config.raw_hooks))
        if args.work_dir:
            os.chdir(Path(args.work_dir).expanduser().resolve())
        app = SimpleCodeApp(
            config,
            hook_engine=hook_engine,
            resume_worktree=args.resume,
            teammate_mode=config.teammate_mode,
            enable_coordinator_mode=config.enable_coordinator_mode,
            team_name=team_name,
        )
    except (ConfigurationError, AuthenticationError, HookConfigError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 2
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Startup error: {exc}", file=sys.stderr)
        return 2
    if args.print_mode:
        if team_name:
            definition = app.agent_loader.get(args.agent_type) if args.agent_type else None
            if args.agent_type and definition is None:
                asyncio.run(app.shutdown_headless(run_hooks=False))
                print(f"Unknown Agent definition: {args.agent_type}", file=sys.stderr)
                return 2
            if definition is not None:
                app.agent.system = f"{definition.system_prompt}\n\n{TEAMMATE_ADDENDUM}"
                app.agent.max_iterations = definition.max_turns
                allowed = set(definition.tools) if definition.tools else None
                denied = set(definition.disallowed_tools)
                filtered = ToolRegistry()
                for tool in app.agent.registry.list_tools():
                    coordination = tool.name in TEAMMATE_COORDINATION_TOOLS
                    if tool.name in denied and not coordination:
                        continue
                    if allowed is not None and tool.name not in allowed and not coordination:
                        continue
                    filtered.register(tool)
                app.registry = filtered
                app.agent.registry = filtered
                app.agent.tools = filtered
            selected_model = args.model or (
                definition.model if definition is not None else "inherit"
            )
            if selected_model not in {"", "inherit"}:
                aliases = {
                    "haiku": "claude-haiku-4-5",
                    "sonnet": "claude-sonnet-4-6",
                    "opus": "claude-opus-4-6",
                }
                app.agent.client = create_client(
                    config.provider.model_copy(
                        update={"model": aliases.get(selected_model, selected_model)}
                    )
                )
            # `-p` has no permission-dialog consumer. DONT_ASK keeps benign operations
            # non-interactive while denying requests that would otherwise require approval.
            app.agent.set_permission_mode(PermissionMode.DONT_ASK)

        async def run_headless() -> str:
            try:
                await app.start_headless()
                result = await app.agent.run_to_completion(args.prompt)
                if team_name:
                    save_transcript(
                        team_name,
                        app.agent.agent_id,
                        app.agent.conversation,
                        app.team_manager.teams_root,
                    )
                    await app.team_manager.on_teammate_completed(app.agent.agent_id)
                return result
            finally:
                await app.shutdown_headless()

        try:
            output = asyncio.run(run_headless())
        except Exception as exc:  # noqa: BLE001 - CLI must report headless failures
            print(f"Agent error: {exc}", file=sys.stderr)
            return 1
        if output:
            print(output)
        return 0
    app.run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
