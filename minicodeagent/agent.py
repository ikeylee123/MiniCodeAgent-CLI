from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .permissions import PermissionController
from .registry import ToolRegistry
from .trace import TraceLogger


class PromptParseError(ValueError):
    pass


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


class MiniCodeAgent:
    def __init__(
        self,
        workspace: Path,
        registry: ToolRegistry,
        permissions: PermissionController,
        trace: TraceLogger,
    ) -> None:
        self.workspace = workspace
        self.registry = registry
        self.permissions = permissions
        self.trace = trace

    def run(self, prompt: str) -> str:
        self.trace.record("prompt", text=prompt)
        plan = self.plan(prompt)
        self.trace.record("plan", tool=plan.name, args=plan.args)
        result = self.execute(plan)
        self.trace.record("observation", tool=plan.name, result=result)
        response = self.respond(plan, result)
        self.trace.record("final", text=response)
        self.trace.flush()
        return response

    def plan(self, prompt: str) -> ToolCall:
        try:
            tokens = shlex.split(prompt)
        except ValueError as exc:
            raise PromptParseError(f"Could not parse prompt: {exc}") from exc
        lowered = prompt.lower().strip()

        if lowered in {"list", "list files", "ls"} or lowered.startswith("list "):
            path = tokens[-1] if len(tokens) > 2 else "."
            return ToolCall("list_files", {"path": path})

        if lowered == "read":
            raise PromptParseError("Usage: read <path>")
        if lowered.startswith("read "):
            if len(tokens) < 2:
                raise PromptParseError("Usage: read <path>")
            return ToolCall("read_file", {"path": tokens[1]})

        if lowered == "search":
            raise PromptParseError("Usage: search <query> [path]")
        if lowered.startswith("search "):
            if len(tokens) < 2:
                raise PromptParseError("Usage: search <query> [path]")
            return ToolCall("search_text", {"query": tokens[1], "path": tokens[2] if len(tokens) > 2 else "."})

        if lowered == "write":
            raise PromptParseError("Usage: write <path> <content>")
        if lowered.startswith("write "):
            if len(tokens) < 3:
                raise PromptParseError("Usage: write <path> <content>")
            return ToolCall("write_file", {"path": tokens[1], "content": " ".join(tokens[2:])})

        if lowered == "python":
            raise PromptParseError("Usage: python <code>")
        if lowered.startswith("python ") and len(prompt.split(" ", 1)) == 2:
            return ToolCall("run_python", {"code": prompt.split(" ", 1)[1]})

        raise PromptParseError(
            "Unknown command. Type 'help' in interactive mode or use --list-tools for available commands."
        )

    def execute(self, call: ToolCall) -> Any:
        tool = self.registry.get(call.name)
        self.permissions.check_tool(tool.name, tool.mutates_files)
        if call.name in {"list_files", "read_file", "search_text"}:
            return tool.handler(self.workspace, **call.args)
        if call.name == "write_file":
            return tool.handler(
                self.workspace,
                **call.args,
                dry_run=self.permissions.config.dry_run,
            )
        if call.name == "run_python":
            return tool.handler(call.args["code"], self.permissions)
        raise KeyError(f"Unsupported tool call: {call.name}")

    def respond(self, call: ToolCall, result: Any) -> str:
        if call.name == "list_files":
            if not result:
                return "No files found."
            return "\n".join(result)
        if call.name == "read_file":
            return str(result)
        if call.name == "search_text":
            if not result:
                return "No matches found."
            return "\n".join(
                f"{item['path']}:{item['line']}: {item['text']}" for item in result
            )
        if call.name == "write_file":
            mode = "Dry run" if result["dry_run"] else "Wrote"
            return f"{mode}: {result['path']}"
        if call.name == "run_python":
            return result["stdout"].rstrip() or "Python completed with no output."
        return str(result)
