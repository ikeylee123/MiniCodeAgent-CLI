from __future__ import annotations

from dataclasses import dataclass, field


class PermissionDenied(RuntimeError):
    pass


UNSAFE_PYTHON_PATTERNS = [
    "import os",
    "from os",
    "import subprocess",
    "from subprocess",
    "import shutil",
    "from shutil",
    "open(",
    "eval(",
    "exec(",
    "__import__",
    "socket",
    "requests",
    "urllib",
]


@dataclass(frozen=True)
class PermissionConfig:
    allowed_tools: set[str] = field(default_factory=set)
    allow_write: bool = False
    dry_run: bool = False


class PermissionController:
    def __init__(self, config: PermissionConfig) -> None:
        self.config = config

    def check_tool(self, tool_name: str, mutates_files: bool = False) -> None:
        if self.config.allowed_tools and tool_name not in self.config.allowed_tools:
            raise PermissionDenied(f"Tool is not allowlisted: {tool_name}")
        if mutates_files and not (self.config.allow_write or self.config.dry_run):
            raise PermissionDenied(
                f"{tool_name} requires --allow-write or --dry-run before writing files"
            )

    def check_python(self, code: str) -> None:
        normalized = code.replace(" ", "").lower()
        for pattern in UNSAFE_PYTHON_PATTERNS:
            compact_pattern = pattern.replace(" ", "").lower()
            if compact_pattern in normalized:
                raise PermissionDenied(f"Unsafe Python pattern blocked: {pattern}")
