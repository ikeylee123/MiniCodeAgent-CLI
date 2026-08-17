from __future__ import annotations

import argparse
from pathlib import Path

from .agent import MiniCodeAgent
from .permissions import PermissionConfig, PermissionController, PermissionDenied
from .tools import build_registry
from .trace import TraceLogger


DEFAULT_ALLOWED_TOOLS = {
    "list_files",
    "read_file",
    "write_file",
    "search_text",
    "run_python",
}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="MiniCodeAgent CLI")
    parser.add_argument("prompt", help="Task prompt, for example: 'list files'")
    parser.add_argument("--workspace", default=".", help="Workspace directory")
    parser.add_argument("--trace", default="trace.json", help="JSON trace output path")
    parser.add_argument(
        "--allow-tool",
        action="append",
        dest="allowed_tools",
        help="Allow a tool by name. Can be passed multiple times.",
    )
    parser.add_argument("--allow-write", action="store_true", help="Allow file writes")
    parser.add_argument("--dry-run", action="store_true", help="Preview writes only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    allowed_tools = set(args.allowed_tools) if args.allowed_tools else DEFAULT_ALLOWED_TOOLS
    permissions = PermissionController(
        PermissionConfig(
            allowed_tools=allowed_tools,
            allow_write=args.allow_write,
            dry_run=args.dry_run,
        )
    )
    agent = MiniCodeAgent(
        workspace=Path(args.workspace),
        registry=build_registry(),
        permissions=permissions,
        trace=TraceLogger(args.trace),
    )
    try:
        print(agent.run(args.prompt))
    except (PermissionDenied, FileNotFoundError, ValueError, KeyError) as exc:
        print(f"error: {exc}")
        return 1
    return 0
