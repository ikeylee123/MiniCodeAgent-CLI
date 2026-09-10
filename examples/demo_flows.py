"""Offline demonstrations using real tools and explicitly deterministic planners.

Run from the repository root: python -m examples.demo_flows
"""
from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from minicodeagent.agent import MiniCodeAgent
from minicodeagent.permissions import PermissionConfig, PermissionController
from minicodeagent.planner import RuleBasedPlanner
from minicodeagent.state import AgentDecision, AgentState
from minicodeagent.tools import build_registry
from minicodeagent.trace import TraceLogger


class SearchReadDemoPlanner:
    """A small demonstration rule, not an LLM or general natural-language planner."""

    def plan(self, state: AgentState) -> AgentDecision:
        if not state.steps:
            return AgentDecision("TOOL", "search_text", {"query": "release date", "path": "."})
        last = state.steps[-1]
        if last.error:
            return AgentDecision("FINAL", final_answer=f"error: {last.error.message}",
                                 final_status="failed")
        if last.selected_tool == "search_text":
            if not last.observation:
                return AgentDecision("FINAL", final_answer="error: No release document found.",
                                     final_status="failed")
            return AgentDecision("TOOL", "read_file", {"path": last.observation[0]["path"]})
        return AgentDecision("FINAL", final_answer=f"Document contents: {last.observation.strip()}")


def run_demo(name: str, workspace: Path, trace_path: Path) -> MiniCodeAgent:
    """The caller supplies a disposable workspace; traces can outlive the fixture."""
    if name not in {"A", "B", "C"}:
        raise ValueError("Choose demo A, B or C")
    (workspace / "docs").mkdir(parents=True, exist_ok=True)
    (workspace / "docs" / "release.txt").write_text(
        "Release date: 2026-10-01.\n", encoding="utf-8")
    goals = {
        "A": "Find the release date document, read it, and report its contents.",
        "B": "read wrong/release.txt",
        "C": "python import os",
    }
    agent = MiniCodeAgent(
        workspace, build_registry(),
        PermissionController(PermissionConfig(allowed_tools={
            "list_files", "search_text", "read_file", "run_python"})),
        TraceLogger(trace_path),
        planner=SearchReadDemoPlanner() if name == "A" else RuleBasedPlanner(),
    )
    agent.run(goals[name])
    return agent


def main() -> None:
    output = Path("logs/demos")
    output.mkdir(parents=True, exist_ok=True)
    for name in ("A", "B", "C"):
        trace_path = output / f"demo-{name}.json"
        with TemporaryDirectory(prefix=f"demo-{name}-", dir=output) as directory:
            agent = run_demo(name, Path(directory), trace_path)
            print(f"Demo {name} (deterministic, offline)")
            for step in agent.state.steps:
                outcome = step.error.type if step.error else "ok"
                print(f"  {step.step_number}. {step.selected_tool}: {outcome}")
            print(f"  status: {agent.state.status}")
            print(f"  answer: {agent.state.final_answer}")
            print(f"  trace: {trace_path}")


if __name__ == "__main__":
    main()
