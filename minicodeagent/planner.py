from __future__ import annotations

import shlex
from pathlib import Path
from typing import Any, Protocol

from .state import AgentDecision, AgentState, ToolCall


class PromptParseError(ValueError):
    pass


class Planner(Protocol):
    def plan(self, state: AgentState) -> AgentDecision: ...


class RuleBasedPlanner:
    """Command rules with bounded, observation-driven missing-file recovery."""

    def plan(self, state: AgentState) -> AgentDecision:
        if not state.steps:
            call = self.parse(state.original_goal)
            return AgentDecision("TOOL", call.name, call.args)
        last = state.steps[-1]
        if last.error:
            if (len(state.steps) == 1 and last.selected_tool == "read_file"
                    and last.error.type == "FileNotFoundError"
                    and "list_files" in state.available_tools):
                return AgentDecision("TOOL", "list_files", {"path": "."})
            return AgentDecision("FINAL", final_answer=f"error: {last.error.message}", final_status="failed")
        if (len(state.steps) == 2 and state.steps[0].selected_tool == "read_file"
                and state.steps[0].error and last.selected_tool == "list_files"):
            wanted = Path(state.steps[0].tool_arguments["path"]).name
            matches = [path for path in last.observation if Path(path).name == wanted]
            if len(matches) == 1:
                return AgentDecision("TOOL", "read_file", {"path": matches[0]})
            return AgentDecision("FINAL", final_answer="error: Could not locate a unique matching file.", final_status="failed")
        return AgentDecision("FINAL", final_answer=self.respond(
            ToolCall(last.selected_tool, last.tool_arguments), last.observation))

    def parse(self, prompt: str) -> ToolCall:
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
