from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .permissions import PermissionController
from .registry import ToolRegistry
from .trace import TraceLogger


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
        tokens = shlex.split(prompt)
        lowered = prompt.lower().strip()

        if lowered in {"list", "list files", "ls"} or lowered.startswith("list "):
            path = tokens[-1] if len(tokens) > 2 else "."
            return ToolCall("list_files", {"path": path})

        if lowered.startswith("read ") and len(tokens) >= 2:
            return ToolCall("read_file", {"path": tokens[1]})

        if lowered.startswith("search ") and len(tokens) >= 2:
            return ToolCall("search_text", {"query": tokens[1], "path": tokens[2] if len(tokens) > 2 else "."})

        if lowered.startswith("write ") and len(tokens) >= 3:
            return ToolCall("write_file", {"path": tokens[1], "content": " ".join(tokens[2:])})

        if lowered.startswith("python ") and len(prompt.split(" ", 1)) == 2:
            return ToolCall("run_python", {"code": prompt.split(" ", 1)[1]})

        return ToolCall("list_files", {"path": "."})

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
