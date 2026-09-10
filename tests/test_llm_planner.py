import json
from io import BytesIO
from http.client import IncompleteRead
from urllib.error import HTTPError, URLError
from unittest.mock import Mock

import pytest

from minicodeagent.agent import MiniCodeAgent
from minicodeagent.cli import main, run_interactive
from minicodeagent.llm_planner import LLMPlanner, LLMPlannerError, _NoRedirects, parse_decision
from minicodeagent.permissions import PermissionConfig, PermissionController
from minicodeagent.state import AgentState
from minicodeagent.tools import build_registry
from minicodeagent.trace import TraceLogger


def completion(decision, **overrides):
    choice = {"finish_reason": "stop", "message": {"content": json.dumps({"decision": decision})}}
    choice.update(overrides)
    return BytesIO(json.dumps({"choices": [choice]}).encode())


def tool(name, **args):
    return {"type": "TOOL", "tool_name": name, "arguments": args}


def final(answer="done", status="completed"):
    return {"type": "FINAL", "answer": answer, "status": status}


@pytest.fixture(autouse=True)
def block_real_network(monkeypatch):
    opener = Mock()
    opener.open.side_effect = AssertionError("Tests must not call a live API")
    monkeypatch.setattr("minicodeagent.llm_planner.build_opener", lambda *args: opener)


def planner_with(*responses):
    planner = LLMPlanner(model="test-model", api_key="test-secret")
    planner._opener.open.side_effect = list(responses)
    return planner


def agent_with(tmp_path, planner, **permissions):
    return MiniCodeAgent(tmp_path, build_registry(),
        PermissionController(PermissionConfig(**permissions)),
        TraceLogger(tmp_path / "logs/trace.json"), planner=planner)


def test_llm_search_read_final_sends_only_run_context(tmp_path):
    (tmp_path / "doc.txt").write_text("release date is October", encoding="utf-8")
    planner = planner_with(completion(tool("search_text", query="release date", path=".")),
        completion(tool("read_file", path="doc.txt")), completion(final("October")))
    agent = agent_with(tmp_path, planner)
    assert agent.run("Find release date") == "October"
    calls = planner._opener.open.call_args_list
    assert len(calls) == 3
    payloads = [json.loads(call.args[0].data) for call in calls]
    contexts = [json.loads(payload["messages"][1]["content"]) for payload in payloads]
    assert set(contexts[0]) == {"goal", "available_tools", "steps"}
    assert contexts[0]["steps"] == []
    assert contexts[1]["steps"][0]["observation"][0]["path"] == "doc.txt"
    assert contexts[2]["steps"][1]["observation"] == "release date is October"
    assert payloads[0]["response_format"]["json_schema"]["strict"] is True
    schema = payloads[0]["response_format"]["json_schema"]["schema"]
    assert schema["type"] == "object"
    assert "anyOf" in schema["properties"]["decision"]
    assert calls[0].args[0].full_url == "https://api.openai.com/v1/chat/completions"
    assert calls[0].args[0].get_header("Authorization") == "Bearer test-secret"
    assert calls[0].kwargs == {"timeout": 30.0}
    assert "test-secret" not in json.dumps(payloads)
    assert "test-secret" not in agent.trace.to_json()
    assert "reasoning" not in schema["properties"]


def test_llm_recovery_uses_error_context(tmp_path):
    (tmp_path / "right.txt").write_text("answer", encoding="utf-8")
    planner = planner_with(completion(tool("read_file", path="wrong.txt")),
        completion(tool("list_files", path=".")),
        completion(tool("read_file", path="right.txt")), completion(final("answer")))
    agent = agent_with(tmp_path, planner)
    assert agent.run("recover") == "answer"
    request = planner._opener.open.call_args_list[1].args[0]
    context = json.loads(json.loads(request.data)["messages"][1]["content"])
    assert context["steps"][0]["error"]["type"] == "FileNotFoundError"
    assert agent.state.current_step_count == 3


@pytest.mark.parametrize("action,permissions", [
    (tool("run_python", code="import os"), {}),
    (tool("write_file", path="blocked", content="no"), {}),
    (tool("read_file", path="anything"), {"allowed_tools": {"list_files"}}),
])
def test_llm_cannot_bypass_permissions(tmp_path, action, permissions):
    planner = planner_with(completion(action), completion(final("Blocked", "failed")))
    agent = agent_with(tmp_path, planner, **permissions)
    assert agent.run("unsafe") == "Blocked"
    assert agent.state.errors[0].type == "PermissionDenied"
    assert agent.state.status == "failed"
    assert not (tmp_path / "blocked").exists()
    assert "PermissionDenied" in agent.trace.to_json()


@pytest.mark.parametrize("action", [tool("unknown"), tool("read_file", path=42)])
def test_untrusted_tool_selection_still_validated_by_registry(tmp_path, action):
    planner = planner_with(completion(action), completion(final("invalid", "failed")))
    agent = agent_with(tmp_path, planner)
    agent.run("invalid")
    assert agent.state.errors
    assert agent.state.current_step_count == 1


@pytest.mark.parametrize("content", [
    "not json", "[]", '{}', '{"decision": null}',
    json.dumps({"decision": {"type": "OTHER"}}),
    json.dumps({"decision": {"type": "TOOL", "tool_name": "read_file", "arguments": []}}),
    json.dumps({"decision": {"type": "FINAL", "answer": 4, "status": "completed"}}),
    json.dumps({"decision": {**final(), "reasoning": "private"}}),
    json.dumps({"decision": {**final(), "status": "unknown"}}),
])
def test_malformed_decisions_rejected_without_echo(content):
    with pytest.raises(LLMPlannerError, match="invalid AgentDecision") as exc:
        parse_decision(content)
    assert "private" not in str(exc.value)


@pytest.mark.parametrize("body,message", [
    (b"not json", "invalid response envelope"),
    (b'{"choices": []}', "invalid response envelope"),
    (b'{"choices": [{"message": null}]}', "invalid response envelope"),
])
def test_malformed_envelope(body, message):
    planner = planner_with(BytesIO(body))
    with pytest.raises(LLMPlannerError, match=message):
        planner.plan(AgentState("goal"))


@pytest.mark.parametrize("override,message", [
    ({"finish_reason": "length"}, "incomplete"),
    ({"message": {"refusal": "sensitive text"}}, "refused"),
])
def test_refusal_and_truncation(override, message):
    planner = planner_with(completion(final(), **override))
    with pytest.raises(LLMPlannerError, match=message) as exc:
        planner.plan(AgentState("goal"))
    assert "sensitive text" not in str(exc.value)


@pytest.mark.parametrize("error", [
    HTTPError("https://secret.example", 401, "test-secret", {}, BytesIO(b"test-secret")),
    HTTPError("https://secret.example", 429, "test-secret", {}, BytesIO(b"test-secret")),
    URLError("test-secret"), TimeoutError("test-secret"), IncompleteRead(b"test-secret"),
])
def test_transport_errors_sanitized_and_traced(tmp_path, error):
    planner = planner_with(error)
    agent = agent_with(tmp_path, planner)
    with pytest.raises(LLMPlannerError):
        agent.run("goal")
    assert agent.state.status == "failed"
    assert "test-secret" not in agent.trace.to_json()
    assert "secret.example" not in agent.trace.to_json()
    assert agent.trace.path.exists()
    assert planner._opener.open.call_count == 1


@pytest.mark.parametrize("base_url", [
    "http://remote.example/v1", "file:///tmp/api", "https://u:password@host/v1",
    "https://host/v1?key=secret", "https://host/v1#fragment", "https://host:bad/v1",
])
def test_bad_endpoint_rejected(base_url):
    with pytest.raises(LLMPlannerError):
        LLMPlanner(model="test", api_key="secret", base_url=base_url)


def test_loopback_endpoint_and_redirect_policy():
    planner = LLMPlanner(model="test", api_key="local-placeholder", base_url="http://127.0.0.1:8000/v1/")
    assert planner._endpoint == "http://127.0.0.1:8000/v1/chat/completions"
    assert _NoRedirects().redirect_request(None, None, 307, "", {}, "https://other") is None


@pytest.mark.parametrize("timeout", [0, -1, float("nan"), float("inf")])
def test_invalid_timeout(timeout):
    with pytest.raises(LLMPlannerError):
        LLMPlanner(model="test", api_key="secret", timeout=timeout)


def test_context_limit_fails_before_network():
    planner = planner_with()
    with pytest.raises(LLMPlannerError, match="context exceeds"):
        planner.plan(AgentState("x" * 65537))
    planner._opener.open.assert_not_called()


def test_response_size_limit():
    planner = planner_with(BytesIO(b"x" * (LLMPlanner.MAX_RESPONSE_BYTES + 1)))
    with pytest.raises(LLMPlannerError, match="response exceeds"):
        planner.plan(AgentState("goal"))


def test_default_cli_does_not_need_llm_configuration(tmp_path, monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["python print(1)", "--trace", str(tmp_path / "trace.json")]) == 0


def test_cli_missing_configuration_is_friendly(monkeypatch, capsys):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert main(["goal", "--planner", "llm", "--model", "test"]) == 1
    assert "OPENAI_API_KEY" in capsys.readouterr().out


def test_cli_llm_uses_environment_and_handles_api_failure(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", "test-secret")
    monkeypatch.setenv("MINICODEAGENT_MODEL", "test-model")
    monkeypatch.setenv("OPENAI_BASE_URL", "http://localhost:8000/v1")
    opener = Mock()
    opener.open.side_effect = URLError("test-secret")
    monkeypatch.setattr("minicodeagent.llm_planner.build_opener", lambda *args: opener)
    assert main(["goal", "--planner", "llm", "--show-trace",
                 "--trace", str(tmp_path / "trace.json")]) == 1
    output = capsys.readouterr().out
    assert "test-secret" not in output
    assert '"status": "failed"' in output
    request = opener.open.call_args.args[0]
    assert request.full_url == "http://localhost:8000/v1/chat/completions"
    assert json.loads(request.data)["model"] == "test-model"


def test_malformed_model_content_executes_no_tool_and_is_not_traced(tmp_path):
    planner = planner_with(completion(final(), message={"content": "private response"}))
    agent = agent_with(tmp_path, planner)
    with pytest.raises(LLMPlannerError):
        agent.run("goal")
    assert agent.state.steps == []
    assert agent.state.status == "failed"
    assert "private response" not in agent.trace.to_json()


def test_interactive_llm_failure_does_not_end_session(tmp_path, capsys):
    planner = planner_with(URLError("test-secret"), completion(final("second succeeded")))
    agent = agent_with(tmp_path, planner)
    prompts = iter(["first", "second", "quit"])
    assert run_interactive(agent, show_trace=True, input_fn=lambda _: next(prompts)) == 0
    output = capsys.readouterr().out
    assert "test-secret" not in output
    assert "second succeeded" in output
    assert '"status": "failed"' in output
