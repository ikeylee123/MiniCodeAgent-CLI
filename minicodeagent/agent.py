from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict
from pathlib import Path
from typing import Any
from uuid import uuid4

from .permissions import PermissionController
from .planner import Planner, PromptParseError, RuleBasedPlanner
from .registry import ToolRegistry
from .state import AgentDecision, AgentError, AgentState, AgentStep, ToolCall
from .trace import TraceLogger


class MiniCodeAgent:
    def __init__(self, workspace: Path, registry: ToolRegistry,
                 permissions: PermissionController, trace: TraceLogger,
                 planner: Planner | None = None, max_steps: int = 6) -> None:
        if isinstance(max_steps, bool) or not isinstance(max_steps, int) or max_steps < 1:
            raise ValueError("max_steps must be a positive integer")
        self.workspace = workspace
        self.registry = registry
        self.permissions = permissions
        self.trace = trace
        self.planner = planner if planner is not None else RuleBasedPlanner()
        self.max_steps = max_steps
        self.state: AgentState | None = None

    def plan(self, state: AgentState) -> AgentDecision:
        return self.planner.plan(state)

    def run(self, prompt: str) -> str:
        state = AgentState(prompt, self.max_steps, deepcopy(self.registry.schemas()))
        self.state = state
        run_id = uuid4().hex

        def record(event_type: str, **data: Any) -> None:
            self.trace.record(event_type, run_id=run_id, **deepcopy(data))

        record("prompt", text=prompt, max_steps=state.max_steps)
        try:
            while True:
                decision = self.plan(deepcopy(state))
                if not isinstance(decision, AgentDecision):
                    raise ValueError("Planner must return AgentDecision")
                record("plan", step_number=state.current_step_count + 1,
                       decision=decision.decision_type, tool=decision.tool_name,
                       args=decision.tool_arguments, final_answer=decision.final_answer)
                if decision.decision_type == "FINAL":
                    if not isinstance(decision.final_answer, str):
                        raise ValueError("FINAL decision requires a string final_answer")
                    state.final_answer = decision.final_answer
                    if decision.final_status not in {"completed", "failed"}:
                        raise ValueError("Invalid final status")
                    state.status = decision.final_status
                    break
                if decision.decision_type != "TOOL":
                    raise ValueError("Unknown planner decision type")
                # Permit a final planning pass after the last allowed tool operation.
                if state.current_step_count >= state.max_steps:
                    state.status = "max_steps"
                    state.final_answer = f"error: Maximum steps reached ({state.max_steps})."
                    break
                call = ToolCall(decision.tool_name, deepcopy(decision.tool_arguments))
                # Each identical call is allowed once per run.
                if any(step.selected_tool == call.name and step.tool_arguments == call.args
                       for step in state.steps):
                    state.status = "repeated_action"
                    state.final_answer = f"error: Repeated action blocked: {call.name}."
                    break
                step = AgentStep(state.current_step_count + 1, call.name, deepcopy(call.args))
                try:
                    step.observation = self.execute(call)
                except Exception as exc:
                    step.error = AgentError(type(exc).__name__, str(exc))
                    step.observation = {"error": asdict(step.error)}
                state.steps.append(step)
                record("observation", step_number=step.step_number, tool=call.name,
                       args=call.args, result=step.observation,
                       error=asdict(step.error) if step.error else None)
        except Exception as exc:
            state.status = "failed"
            state.final_answer = f"error: {exc}"
            record("error", error={"type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            record("final", text=state.final_answer, status=state.status,
                   step_count=state.current_step_count)
            self.trace.flush()
        return state.final_answer

    def execute(self, call: ToolCall) -> Any:
        if not isinstance(call.name, str) or not call.name:
            raise ValueError("Tool name must be a non-empty string")
        tool = self.registry.get(call.name)
        self.registry.validate(call.name, call.args)
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
        return RuleBasedPlanner().respond(call, result)
