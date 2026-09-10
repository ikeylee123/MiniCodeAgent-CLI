import json

import pytest

from examples.demo_flows import run_demo


@pytest.mark.parametrize("name,tools,status", [
    ("A", ["search_text", "read_file"], "completed"),
    ("B", ["read_file", "list_files", "read_file"], "completed"),
    ("C", ["run_python"], "failed"),
])
def test_demonstrations_use_real_tools_and_trace(tmp_path, name, tools, status):
    agent = run_demo(name, tmp_path, tmp_path / "logs" / "trace.json")
    assert [call.name for call in agent.state.tool_calls] == tools
    assert agent.state.status == status
    events = json.loads(agent.trace.path.read_text(encoding="utf-8"))
    observations = [event["data"] for event in events if event["type"] == "observation"]
    assert [item["step_number"] for item in observations] == list(range(1, len(tools) + 1))
    if name == "C":
        assert observations[0]["error"]["type"] == "PermissionDenied"
        assert "Unsafe Python pattern blocked" in agent.state.final_answer
    else:
        assert "2026-10-01" in agent.state.final_answer
    if name == "B":
        assert observations[0]["error"]["type"] == "FileNotFoundError"
