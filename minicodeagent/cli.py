from __future__ import annotations

import argparse
import json
from builtins import input as builtin_input
from pathlib import Path

from .agent import MiniCodeAgent, PromptParseError
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
    parser.add_argument("prompt", nargs="?", help="Task prompt, for example: 'list files'")
    parser.add_argument("--workspace", default=".", help="Workspace directory")
    parser.add_argument("--trace", default="trace.json", help="JSON trace output path")
    parser.add_argument(
        "--list-tools",
        action="store_true",
        help="List registered tools and their schemas",
    )
    parser.add_argument(
        "--show-trace",
        action="store_true",
        help="Print the execution trace after the final response",
    )
    parser.add_argument(
        "--interactive",
        action="store_true",
        help="Start an interactive session",
    )
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
    registry = build_registry()
    if args.list_tools:
        print(format_tools(registry.descriptions(), registry.schemas()))
        return 0

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
        registry=registry,
        permissions=permissions,
        trace=TraceLogger(args.trace),
    )
    if args.interactive:
        return run_interactive(agent, show_trace=args.show_trace)
    if not args.prompt:
        build_parser().error("prompt is required unless --list-tools or --interactive is used")
    try:
        response = agent.run(args.prompt)
        print(response)
        if args.show_trace:
            print("\nTrace:")
            print(agent.trace.to_json())
    except (PermissionDenied, FileNotFoundError, PromptParseError, ValueError, KeyError) as exc:
        print(f"error: {exc}")
        return 1
    return 0


def run_interactive(
    agent: MiniCodeAgent,
    show_trace: bool = False,
    input_fn=builtin_input,
) -> int:
    print("MiniCodeAgent interactive mode. Type 'exit' or 'quit' to leave.")
    while True:
        try:
            prompt = input_fn("MiniCodeAgent> ").strip()
        except EOFError:
            print("Session ended.")
            return 0
        if not prompt:
            continue
        if prompt.lower() == "help":
            print(format_command_reference())
            continue
        if prompt.lower() in {"exit", "quit"}:
            print("Session ended.")
            return 0
        try:
            response = agent.run(prompt)
            print(response)
            if show_trace:
                print("\nTrace:")
                print(agent.trace.to_json())
        except (PermissionDenied, FileNotFoundError, PromptParseError, ValueError, KeyError) as exc:
            print(f"error: {exc}")


def format_tools(
    descriptions: dict[str, str],
    schemas: dict[str, dict[str, object]],
) -> str:
    lines: list[str] = []
    for name, description in descriptions.items():
        lines.append(f"{name}: {description}")
        schema = schemas[name]
        for arg_name, arg_schema in schema.items():
            required = "required" if arg_schema.get("required") else "optional"
            default = (
                f", default={json.dumps(arg_schema['default'])}"
                if "default" in arg_schema
                else ""
            )
            lines.append(
                f"  - {arg_name} ({arg_schema['type']}, {required}{default}): "
                f"{arg_schema['description']}"
            )
    return "\n".join(lines)


def format_command_reference() -> str:
    lines = [
        "Available commands:",
        "  list files",
        "  list <path>",
        "  ls",
        "  read <path>",
        "  search <query> [path]",
        "  write <path> <content>",
        "  python <code>",
        "  help",
        "  exit",
        "  quit",
        "",
        "Flags are passed when starting the program, for example:",
        '  python main.py "write notes.txt hello" --dry-run',
        '  python main.py "search agent" --show-trace',
    ]
    return "\n".join(lines)
