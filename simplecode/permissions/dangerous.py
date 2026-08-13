"""Hard-coded dangerous command detection and conservative safe-command allow-list."""

from __future__ import annotations

import re
from collections.abc import Iterable

_DANGEROUS_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\brm\s+(?:-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\s+"
            r"(?:--no-preserve-root\s+)?/(?:\s|$|\*)",
            re.IGNORECASE,
        ),
        "递归强制删除根目录",
    ),
    (re.compile(r"\bmkfs(?:\.[a-z0-9_+-]+)?\b", re.IGNORECASE), "格式化磁盘设备"),
    (
        re.compile(r"\bdd\b[^\n]*\bof\s*=\s*/dev/", re.IGNORECASE),
        "使用 dd 覆写磁盘设备",
    ),
    (
        re.compile(r"\bchmod\s+-R\s+777\s+/(?:\s|$|\*)", re.IGNORECASE),
        "递归开放根目录权限",
    ),
    (re.compile(r":\s*\(\s*\)\s*\{\s*:\s*\|\s*:\s*&\s*\}\s*;\s*:"), "fork bomb"),
    (
        re.compile(r"\bcurl\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b", re.IGNORECASE),
        "通过 curl 下载并执行远程脚本",
    ),
    (
        re.compile(r"\bwget\b[^|\n]*\|\s*(?:sudo\s+)?(?:ba|z|k)?sh\b", re.IGNORECASE),
        "通过 wget 下载并执行远程脚本",
    ),
    (
        re.compile(r">\s*/dev/(?:sd[a-z]|nvme\d+n\d+|vd[a-z])", re.IGNORECASE),
        "直接重定向写入磁盘设备",
    ),
]

_SAFE_COMMANDS = frozenset(
    {
        "arch",
        "basename",
        "bat --version",
        "bun --version",
        "cargo metadata",
        "cargo tree",
        "cargo version",
        "cat",
        "clang --version",
        "cmake --version",
        "composer --version",
        "cut",
        "date",
        "deno --version",
        "dirname",
        "docker images",
        "docker info",
        "docker ps",
        "dotnet --info",
        "dotnet --version",
        "du",
        "env",
        "file",
        "git branch",
        "git diff",
        "git grep",
        "git log",
        "git ls-files",
        "git remote",
        "git rev-parse",
        "git show",
        "git status",
        "go env",
        "go list",
        "go version",
        "grep",
        "head",
        "hostname",
        "java -version",
        "jq",
        "ls",
        "node --version",
        "npm -v",
        "npm --version",
        "npm list",
        "npm view",
        "npx --version",
        "pip --version",
        "pip list",
        "pip show",
        "pnpm --version",
        "printf",
        "pwd",
        "python --version",
        "python3 --version",
        "rg",
        "ruby --version",
        "rustc --version",
        "sed -n",
        "sort",
        "tail",
        "tree",
        "tsc --version",
        "type",
        "uname",
        "uniq",
        "uv --version",
        "wc",
        "which",
        "where",
        "whoami",
        "yarn --version",
    }
)

_UNSAFE_SHELL_TOKENS = ("|", ";", "&&", ">", "$(", "`", "\n", "\r")


def is_safe_command(command: str) -> bool:
    stripped = command.strip()
    if not stripped or any(token in stripped for token in _UNSAFE_SHELL_TOKENS):
        return False
    if re.search(r"(?:^|\s)&(?:\s|$)", stripped):
        return False
    return any(stripped == safe or stripped.startswith(f"{safe} ") for safe in _SAFE_COMMANDS)


class DangerousCommandDetector:
    def __init__(
        self,
        extra_patterns: Iterable[tuple[str | re.Pattern[str], str]] | None = None,
    ) -> None:
        self._patterns = list(_DANGEROUS_PATTERNS)
        for pattern, reason in extra_patterns or ():
            compiled = re.compile(pattern, re.IGNORECASE) if isinstance(pattern, str) else pattern
            self._patterns.append((compiled, reason))

    def detect(self, command: str) -> tuple[bool, str]:
        for pattern, reason in self._patterns:
            if pattern.search(command):
                return True, reason
        return False, ""


__all__ = [
    "DangerousCommandDetector",
    "_DANGEROUS_PATTERNS",
    "_SAFE_COMMANDS",
    "is_safe_command",
]
