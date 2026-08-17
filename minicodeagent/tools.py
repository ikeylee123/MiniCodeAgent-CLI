from __future__ import annotations

import contextlib
import io
from pathlib import Path
from typing import Any

from .permissions import PermissionController
from .registry import Tool, ToolRegistry


def _safe_path(workspace: Path, target: str) -> Path:
    workspace = workspace.resolve()
    path = (workspace / target).resolve()
    if path != workspace and workspace not in path.parents:
        raise ValueError(f"Path escapes workspace: {target}")
    return path


def list_files(workspace: Path, path: str = ".") -> list[str]:
    root = _safe_path(workspace, path)
    if not root.exists():
        raise FileNotFoundError(path)
    if root.is_file():
        return [str(root.relative_to(workspace.resolve()))]
    return sorted(
        str(item.relative_to(workspace.resolve()))
        for item in root.rglob("*")
        if item.is_file()
    )


def read_file(workspace: Path, path: str) -> str:
    file_path = _safe_path(workspace, path)
    return file_path.read_text(encoding="utf-8")


def write_file(
    workspace: Path,
    path: str,
    content: str,
    dry_run: bool = False,
) -> dict[str, Any]:
    file_path = _safe_path(workspace, path)
    if dry_run:
        return {"path": str(file_path.relative_to(workspace.resolve())), "dry_run": True}
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    return {"path": str(file_path.relative_to(workspace.resolve())), "dry_run": False}


def search_text(workspace: Path, query: str, path: str = ".") -> list[dict[str, Any]]:
    root = _safe_path(workspace, path)
    files = [root] if root.is_file() else [item for item in root.rglob("*") if item.is_file()]
    matches: list[dict[str, Any]] = []
    for file_path in files:
        try:
            lines = file_path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            continue
        for line_no, line in enumerate(lines, start=1):
            if query.lower() in line.lower():
                matches.append(
                    {
                        "path": str(file_path.relative_to(workspace.resolve())),
                        "line": line_no,
                        "text": line,
                    }
                )
    return matches


def run_python(code: str, permissions: PermissionController) -> dict[str, str]:
    permissions.check_python(code)
    stdout = io.StringIO()
    safe_globals = {"__builtins__": {"print": print, "len": len, "range": range, "sum": sum}}
    with contextlib.redirect_stdout(stdout):
        exec(code, safe_globals, {})
    return {"stdout": stdout.getvalue()}


def build_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(Tool("list_files", "List files in the workspace.", list_files))
    registry.register(Tool("read_file", "Read a UTF-8 text file.", read_file))
    registry.register(
        Tool("write_file", "Write a UTF-8 text file.", write_file, mutates_files=True)
    )
    registry.register(Tool("search_text", "Search text files for a query.", search_text))
    registry.register(Tool("run_python", "Run restricted Python code.", run_python))
    return registry
