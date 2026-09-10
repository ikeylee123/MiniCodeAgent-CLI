from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass(frozen=True)
class AgentDecision:
    decision_type: Literal["TOOL", "FINAL"]
    tool_name: str | None = None
    tool_arguments: dict[str, Any] = field(default_factory=dict)
    final_answer: str | None = None
    final_status: Literal["completed", "failed"] = "completed"


@dataclass(frozen=True)
class AgentError:
    type: str
    message: str


@dataclass
class AgentStep:
    step_number: int
    selected_tool: str
    tool_arguments: dict[str, Any]
    observation: Any = None
    error: AgentError | None = None


@dataclass
class AgentState:
    original_goal: str
    max_steps: int = 6
    available_tools: dict[str, dict[str, Any]] = field(default_factory=dict)
    steps: list[AgentStep] = field(default_factory=list)
    status: Literal["running", "completed", "failed", "max_steps", "repeated_action"] = "running"
    final_answer: str | None = None

    @property
    def current_step_count(self) -> int:
        return len(self.steps)

    @property
    def tool_calls(self) -> list[ToolCall]:
        return [ToolCall(step.selected_tool, step.tool_arguments) for step in self.steps]

    @property
    def observations(self) -> list[Any]:
        return [step.observation for step in self.steps]

    @property
    def errors(self) -> list[AgentError]:
        return [step.error for step in self.steps if step.error is not None]
