import json

from evals.run_eval import (
    CaseResult,
    EvalCase,
    calculate_metrics,
    classify_case,
    evaluate_trace,
    load_cases,
    render_markdown_report,
)


def case(**overrides):
    data = {
        "id": "T1",
        "category": "normal",
        "prompt": "Find answer",
        "expected_facts": ["42"],
        "expected_sources": ["answer.txt"],
        "requires_recovery": False,
        "requires_multi_step": True,
        "safety_behavior": None,
        "notes": "",
    }
    data.update(overrides)
    return EvalCase(**data)


def observation(step, tool, result=None, error=None, args=None):
    return {
        "type": "observation",
        "data": {
            "step_number": step,
            "tool": tool,
            "args": args or {},
            "result": result,
            "error": error,
        },
        "timestamp": "2026-01-01T00:00:00+00:00",
    }


def final(text, status="completed", steps=1):
    return {
        "type": "final",
        "data": {"text": text, "status": status, "step_count": steps},
        "timestamp": "2026-01-01T00:00:00+00:00",
    }


def result(case_id, status, tool_calls=1, category="normal"):
    return CaseResult(
        id=case_id,
        category=category,
        status=status,
        final_status="completed",
        final_answer="answer",
        tool_sequence=["read_file"] * tool_calls,
        tool_call_count=tool_calls,
        trace_path=f"{case_id}.trace.json",
    )


def test_load_cases_defines_twelve_unique_cases():
    cases = load_cases()

    assert len(cases) == 12
    assert len({item.id for item in cases}) == 12
    assert {item.category for item in cases} == {"normal", "tool_selection", "recovery", "safety", "edge"}
    assert sum(1 for item in cases if item.requires_recovery) == 3
    assert sum(1 for item in cases if item.safety_behavior) == 2


def test_classifies_success_when_answer_is_supported_by_observation():
    trace = [
        observation(1, "read_file", "The answer is 42."),
        final("The answer is 42.", steps=1),
    ]

    status, provider, note = classify_case(case(requires_multi_step=False), trace)

    assert status == "PASS"
    assert provider is None
    assert "supported" in note


def test_provider_failure_is_not_agent_failure():
    trace = [
        observation(1, "read_file", "The answer is 42."),
        {"type": "error", "data": {"error": {"type": "LLMPlannerError", "message": "LLM API HTTP 429; check credentials, model and strict JSON Schema support."}}},
        final("error: LLM API HTTP 429; check credentials, model and strict JSON Schema support.", status="failed"),
    ]

    status, provider, note = classify_case(case(), trace)

    assert status == "PROVIDER_FAIL"
    assert provider == "PROVIDER_RATE_LIMIT"
    assert note == "PROVIDER_RATE_LIMIT"


def test_safety_pass_for_blocked_python():
    trace = [
        observation(
            1,
            "run_python",
            {"error": {"type": "PermissionDenied", "message": "Unsafe Python pattern blocked"}},
            {"type": "PermissionDenied", "message": "Unsafe Python pattern blocked"},
            {"code": "import os"},
        ),
        final("Blocked", status="failed"),
    ]

    status, provider, note = classify_case(
        case(category="safety", expected_facts=[], requires_multi_step=False, safety_behavior="unsafe_python_blocked"),
        trace,
    )

    assert status == "SAFETY_PASS"
    assert provider is None
    assert note == "blocked by permission layer"


def test_recovery_requires_error_then_changed_action_and_correct_answer():
    trace = [
        observation(1, "read_file", {"error": {"type": "FileNotFoundError", "message": "missing"}}, {"type": "FileNotFoundError", "message": "missing"}, {"path": "wrong/answer.txt"}),
        observation(2, "list_files", ["answer.txt"], args={"path": "."}),
        observation(3, "read_file", "The answer is 42.", args={"path": "answer.txt"}),
        final("The answer is 42.", steps=3),
    ]

    status, provider, note = classify_case(case(requires_recovery=True), trace)

    assert status == "PASS"
    assert provider is None


def test_evaluate_trace_reads_tool_sequence_and_counts_calls(tmp_path):
    trace_path = tmp_path / "trace.json"
    trace_path.write_text(json.dumps([
        observation(1, "search_text", [{"path": "answer.txt", "text": "42"}]),
        observation(2, "read_file", "42"),
        final("42", steps=2),
    ]), encoding="utf-8")

    item = evaluate_trace(case(), trace_path)

    assert item.status == "PASS"
    assert item.tool_sequence == ["search_text", "read_file"]
    assert item.tool_call_count == 2


def test_metrics_include_average_tool_calls_and_separate_safety():
    cases = [
        case(id="A", requires_multi_step=True),
        case(id="B", category="recovery", requires_recovery=True, requires_multi_step=True),
        case(id="C", category="safety", expected_facts=[], requires_multi_step=False, safety_behavior="write_without_permission_blocked"),
        case(id="D", category="normal", requires_multi_step=False),
    ]
    results = [
        result("A", "PASS", 2),
        result("B", "PROVIDER_FAIL", 3, category="recovery"),
        result("C", "SAFETY_PASS", 1, category="safety"),
        result("D", "AGENT_FAIL", 0),
    ]

    metrics = calculate_metrics(cases, results)

    assert metrics["task_success"] == [1, 4]
    assert metrics["multi_step_completion"] == [1, 2]
    assert metrics["recovery_success"] == [0, 1]
    assert metrics["safety_enforcement"] == [1, 1]
    assert metrics["provider_api_failures"] == 1
    assert metrics["average_tool_calls"] == 1.5


def test_markdown_report_contains_required_sections():
    cases = [case(id="A1")]
    results = [result("A1", "PASS", 2)]

    report = render_markdown_report(cases, results, "Gemini OpenAI-compatible", "gemini-2.5-flash")

    assert "# Agent Evaluation" in report
    assert "## Overall Results" in report
    assert "## Failure Analysis" in report
    assert "## Limitations" in report
    assert "Provider/API Failures" in report
    assert "No API keys or credentials" in report
