import json

import pytest

from minicodeagent.agent import MiniCodeAgent
from minicodeagent.cli import main, run_interactive
from minicodeagent.permissions import PermissionConfig, PermissionController
from minicodeagent.state import AgentDecision
from minicodeagent.tools import build_registry
from minicodeagent.trace import TraceLogger


def tool(name, **args):
    return AgentDecision("TOOL", name, args)


def final(answer="done"):
    return AgentDecision("FINAL", final_answer=answer)


class ScriptedPlanner:
    def __init__(self, *decisions):
        self.decisions = iter(decisions)
        self.states = []

    def plan(self, state):
        self.states.append(state)
        decision = next(self.decisions)
        return decision(state) if callable(decision) else decision


def make_agent(tmp_path, planner=None, max_steps=6, **permissions):
    return MiniCodeAgent(tmp_path, build_registry(),
                         PermissionController(PermissionConfig(**permissions)),
                         TraceLogger(tmp_path / "logs" / "trace.json"),
                         planner=planner, max_steps=max_steps)


def test_search_then_read_uses_observation(tmp_path):
    (tmp_path / "found.txt").write_text("needle and context", encoding="utf-8")
    planner = ScriptedPlanner(
        tool("search_text", query="needle"),
        lambda state: tool("read_file", path=state.observations[-1][0]["path"]),
        lambda state: final(state.observations[-1]),
    )
    agent = make_agent(tmp_path, planner)
    assert agent.run("find and read needle") == "needle and context"
    assert [call.name for call in agent.state.tool_calls] == ["search_text", "read_file"]
    assert planner.states[1].original_goal == "find and read needle"
    assert "read_file" in planner.states[1].available_tools
    assert planner.states[0].steps == []


def test_three_tools_use_each_previous_result(tmp_path):
    (tmp_path / "index.txt").write_text("target.txt", encoding="utf-8")
    (tmp_path / "target.txt").write_text("answer", encoding="utf-8")
    planner = ScriptedPlanner(
        tool("search_text", query="target.txt"),
        lambda state: tool("read_file", path=state.observations[-1][0]["path"]),
        lambda state: tool("read_file", path=state.observations[-1]),
        lambda state: final(state.observations[-1]),
    )
    agent = make_agent(tmp_path, planner)
    assert agent.run("follow index") == "answer"
    assert agent.state.current_step_count == 3


def test_error_observation_drives_mock_recovery(tmp_path):
    (tmp_path / "correct.txt").write_text("needle", encoding="utf-8")

    def recover(state):
        assert state.errors[-1].type == "FileNotFoundError"
        assert state.observations[-1]["error"]["type"] == "FileNotFoundError"
        return tool("search_text", query="needle")

    planner = ScriptedPlanner(tool("read_file", path="wrong.txt"), recover,
        lambda state: tool("read_file", path=state.observations[-1][0]["path"]),
        lambda state: final(state.observations[-1]))
    agent = make_agent(tmp_path, planner)
    assert agent.run("recover") == "needle"
    assert agent.state.status == "completed"
    assert len(agent.state.errors) == 1


def test_rule_planner_recovers_missing_directory(tmp_path):
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "note.txt").write_text("recovered", encoding="utf-8")
    agent = make_agent(tmp_path)
    assert agent.run("read wrong/note.txt") == "recovered"
    assert [call.name for call in agent.state.tool_calls] == ["read_file", "list_files", "read_file"]


@pytest.mark.parametrize("ambiguous", [False, True])
def test_rule_recovery_does_not_guess(tmp_path, ambiguous):
    if ambiguous:
        for directory in ["a", "b"]:
            (tmp_path / directory).mkdir()
            (tmp_path / directory / "note.txt").write_text("content", encoding="utf-8")
    agent = make_agent(tmp_path)
    assert "unique matching file" in agent.run("read wrong/note.txt")
    assert agent.state.status == "failed"
    assert agent.state.current_step_count == 2


@pytest.mark.parametrize("decision,error_type", [
    (tool("unknown"), "KeyError"),
    (tool("read_file"), "ValueError"),
    (tool("read_file", path=12), "ValueError"),
    (tool("write_file", path="x", content="y", dry_run=False), "ValueError"),
    (AgentDecision("TOOL", "read_file", []), "ValueError"),
    (AgentDecision("TOOL"), "ValueError"),
])
def test_invalid_calls_become_observations(tmp_path, decision, error_type):
    def recover(state):
        assert state.errors[-1].type == error_type
        return tool("run_python", code="print(42)")
    agent = make_agent(tmp_path, ScriptedPlanner(decision, recover,
        lambda state: final(state.observations[-1]["stdout"].strip())))
    assert agent.run("recover invalid call") == "42"
    assert not (tmp_path / "x").exists()


@pytest.mark.parametrize("command,permissions", [
    ("python import os", {}),
    ("write blocked.txt content", {}),
    ("read anything", {"allowed_tools": {"list_files"}}),
    ("read ../outside.txt", {}),
])
def test_loop_preserves_permissions_and_paths(tmp_path, command, permissions):
    agent = make_agent(tmp_path, **permissions)
    assert agent.run(command).startswith("error:")
    assert agent.state.status == "failed"
    assert agent.state.current_step_count == 1
    assert not (tmp_path / "blocked.txt").exists()


@pytest.mark.parametrize("call", [tool("read_file", path="missing"), tool("run_python", code="print(1)")])
def test_repeated_action_stops_before_second_execution(tmp_path, call):
    agent = make_agent(tmp_path, ScriptedPlanner(call, call))
    assert "Repeated action" in agent.run("loop")
    assert agent.state.status == "repeated_action"
    assert agent.state.current_step_count == 1


def test_nonconsecutive_failure_is_not_retried(tmp_path):
    call = tool("read_file", path="missing")
    agent = make_agent(tmp_path, ScriptedPlanner(call, tool("list_files"), call))
    agent.run("loop")
    assert agent.state.status == "repeated_action"
    assert agent.state.current_step_count == 2


def test_max_steps_prevents_extra_execution(tmp_path):
    planner = ScriptedPlanner(tool("run_python", code="print(1)"),
                              tool("write_file", path="extra", content="no"))
    agent = make_agent(tmp_path, planner, max_steps=1, allow_write=True)
    assert "Maximum steps" in agent.run("bounded")
    assert agent.state.status == "max_steps"
    assert agent.state.current_step_count == 1
    assert not (tmp_path / "extra").exists()
    assert planner.states[-1].observations == [{"stdout": "1\n"}]


def test_final_allowed_at_step_limit(tmp_path):
    agent = make_agent(tmp_path, max_steps=1)
    assert agent.run("python print(2)") == "2"
    assert agent.state.status == "completed"


def test_trace_orders_steps_and_records_recovery(tmp_path):
    agent = make_agent(tmp_path, ScriptedPlanner(tool("unknown"),
        tool("run_python", code="print(2)"), final("2")))
    agent.run("trace goal")
    events = json.loads(agent.trace.path.read_text(encoding="utf-8"))
    assert events[0]["data"]["text"] == "trace goal"
    observations = [event["data"] for event in events if event["type"] == "observation"]
    assert [item["step_number"] for item in observations] == [1, 2]
    assert observations[0]["error"]["type"] == "KeyError"
    assert observations[1]["args"] == {"code": "print(2)"}
    assert observations[1]["result"] == {"stdout": "2\n"}
    assert events[-2]["data"]["decision"] == "FINAL"
    assert events[-1]["data"]["status"] == "completed"
    assert events[-1]["data"]["text"] == "2"
    assert len({event["data"]["run_id"] for event in events}) == 1


def test_parse_error_flushes_trace(tmp_path):
    agent = make_agent(tmp_path)
    with pytest.raises(ValueError):
        agent.run("read")
    events = json.loads(agent.trace.path.read_text(encoding="utf-8"))
    assert events[-1]["data"]["status"] == "failed"


def test_runs_reset_state_and_have_distinct_trace_ids(tmp_path):
    agent = make_agent(tmp_path)
    agent.run("python print(1)")
    first = agent.state
    agent.run("python print(2)")
    assert first.observations == [{"stdout": "1\n"}]
    assert agent.state.current_step_count == 1
    assert len({event.data["run_id"] for event in agent.trace.events}) == 2


def test_interactive_uses_multistep_loop(tmp_path, capsys):
    agent = make_agent(tmp_path, ScriptedPlanner(tool("list_files"),
        tool("run_python", code="print(3)"), final("three")))
    prompts = iter(["task", "history", "quit"])
    assert run_interactive(agent, input_fn=lambda _: next(prompts)) == 0
    output = capsys.readouterr().out
    assert "tool: list_files -> run_python" in output
    assert "three" in output
    assert agent.state.current_step_count == 2


def test_cli_exit_status_and_content_starting_error(tmp_path, capsys):
    (tmp_path / "text").write_text("error: sample content", encoding="utf-8")
    args = ["--workspace", str(tmp_path), "--trace", str(tmp_path / "logs" / "cli.json")]
    assert main(["read text", *args]) == 0
    assert main(["write blocked.txt no", *args, "--show-trace"]) == 1
    assert '"status": "failed"' in capsys.readouterr().out


@pytest.mark.parametrize("limit", [0, -1, True, 1.5])
def test_invalid_step_limit(tmp_path, limit):
    with pytest.raises(ValueError):
        make_agent(tmp_path, max_steps=limit)
