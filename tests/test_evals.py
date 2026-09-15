import json
from pathlib import Path

from evals.run_eval import (
    AttemptResult,
    CaseResult,
    EvalCase,
    calculate_metrics,
    contains_all,
    classify_case,
    evaluate_trace,
    is_retryable_provider_failure,
    load_cases,
    render_markdown_report,
    retry_delay,
    run_case,
    run_selected_cases,
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


def attempt(attempt_number, status, provider=None):
    return AttemptResult(
        attempt=attempt_number,
        status=status,
        provider_failure_category=provider,
        trace_path=f"T1.attempt-{attempt_number}.trace.json",
        final_status="completed" if status == "PASS" else "failed",
        final_answer="answer",
        tool_sequence=["read_file"],
        tool_call_count=1,
        notes=status,
    )


def result(case_id, status, tool_calls=1, category="normal", provider=None, attempts=None):
    return CaseResult(
        id=case_id,
        category=category,
        status=status,
        final_status="completed" if status in {"PASS", "SAFETY_PASS"} else "failed",
        final_answer="answer",
        tool_sequence=["read_file"] * tool_calls,
        tool_call_count=tool_calls,
        trace_path=f"{case_id}.trace.json",
        provider_failure_category=provider,
        attempts=attempts or [attempt(1, status, provider)],
    )


def provider_result(category="PROVIDER_RATE_LIMIT"):
    return result("T1", "PROVIDER_FAIL", provider=category, attempts=[attempt(1, "PROVIDER_FAIL", category)])


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
        result("B", "PROVIDER_FAIL", 3, category="recovery", provider="PROVIDER_RATE_LIMIT"),
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
    assert "Provider Failure Attempts" in report
    assert "No API keys or credentials" in report


def test_default_delay_is_zero_in_help_parser():
    from evals.run_eval import build_parser

    args = build_parser().parse_args(["--case", "A1"])

    assert args.delay_seconds == 0.0


def test_configured_inter_case_delay_between_cases_only(tmp_path):
    selected = [case(id="A"), case(id="B"), case(id="C")]
    sleeps = []
    calls = []

    def fake_run(item, *_args):
        calls.append(item.id)
        return result(item.id, "PASS")

    run_selected_cases(
        selected, tmp_path / "workspace", tmp_path / "results", "llm", "model", 30.0,
        45.0, 0, 30.0, sleep_fn=sleeps.append, run_case_fn=fake_run)

    assert calls == ["A", "B", "C"]
    assert sleeps == [45.0, 45.0]


def test_no_inter_case_delay_for_single_case(tmp_path):
    sleeps = []

    run_selected_cases(
        [case(id="A")], tmp_path / "workspace", tmp_path / "results", "llm", "model", 30.0,
        45.0, 0, 30.0, sleep_fn=sleeps.append,
        run_case_fn=lambda item, *_args: result(item.id, "PASS"))

    assert sleeps == []


def test_http_429_is_retryable_provider_failure():
    item = provider_result("PROVIDER_RATE_LIMIT")

    assert is_retryable_provider_failure(item) is True


def test_http_503_is_retryable_provider_failure():
    item = provider_result("PROVIDER_UNAVAILABLE")

    assert is_retryable_provider_failure(item) is True


def test_agent_fail_is_not_retried(tmp_path):
    calls = []

    def fake_attempt(_case, _workspace, _results, _planner, _model, _timeout, attempt_number):
        calls.append(attempt_number)
        return result("T1", "AGENT_FAIL")

    final_result = run_case(case(), tmp_path / "workspace", tmp_path / "results", "llm", None, 30.0,
                            provider_retries=2, sleep_fn=lambda _: None, run_attempt_fn=fake_attempt)

    assert calls == [1]
    assert final_result.status == "AGENT_FAIL"


def test_pass_is_not_retried(tmp_path):
    calls = []

    def fake_attempt(_case, _workspace, _results, _planner, _model, _timeout, attempt_number):
        calls.append(attempt_number)
        return result("T1", "PASS")

    final_result = run_case(case(), tmp_path / "workspace", tmp_path / "results", "llm", None, 30.0,
                            provider_retries=2, sleep_fn=lambda _: None, run_attempt_fn=fake_attempt)

    assert calls == [1]
    assert final_result.status == "PASS"


def test_retry_attempt_count_and_exponential_delay(tmp_path):
    sleeps = []
    outcomes = [provider_result("PROVIDER_RATE_LIMIT"), provider_result("PROVIDER_UNAVAILABLE"), result("T1", "PASS")]

    def fake_attempt(_case, _workspace, _results, _planner, _model, _timeout, attempt_number):
        return outcomes[attempt_number - 1]

    final_result = run_case(case(), tmp_path / "workspace", tmp_path / "results", "llm", None, 30.0,
                            provider_retries=2, retry_delay_seconds=30.0,
                            sleep_fn=sleeps.append, run_attempt_fn=fake_attempt)

    assert final_result.status == "PASS"
    assert final_result.attempt_count == 3
    assert sleeps == [30.0, 60.0]
    assert retry_delay(30.0, 1) == 30.0
    assert retry_delay(30.0, 2) == 60.0


def test_final_pass_after_initial_provider_failure_preserves_history(tmp_path):
    outcomes = [provider_result("PROVIDER_RATE_LIMIT"), result("T1", "PASS")]

    def fake_attempt(_case, _workspace, _results, _planner, _model, _timeout, attempt_number):
        return outcomes[attempt_number - 1]

    final_result = run_case(case(), tmp_path / "workspace", tmp_path / "results", "llm", None, 30.0,
                            provider_retries=1, retry_delay_seconds=0,
                            sleep_fn=lambda _: None, run_attempt_fn=fake_attempt)

    assert final_result.status == "PASS"
    assert [item.status for item in final_result.attempts] == ["PROVIDER_FAIL", "PASS"]
    assert final_result.attempts[0].provider_failure_category == "PROVIDER_RATE_LIMIT"


def test_final_provider_fail_after_retry_exhaustion(tmp_path):
    def fake_attempt(_case, _workspace, _results, _planner, _model, _timeout, attempt_number):
        return provider_result("PROVIDER_RATE_LIMIT")

    final_result = run_case(case(), tmp_path / "workspace", tmp_path / "results", "llm", None, 30.0,
                            provider_retries=2, retry_delay_seconds=0,
                            sleep_fn=lambda _: None, run_attempt_fn=fake_attempt)

    assert final_result.status == "PROVIDER_FAIL"
    assert final_result.attempt_count == 3
    assert [item.status for item in final_result.attempts] == ["PROVIDER_FAIL", "PROVIDER_FAIL", "PROVIDER_FAIL"]


def test_provider_retry_metrics_count_attempts_and_cases():
    cases = [case(id="A"), case(id="B"), case(id="C")]
    results = [
        result("A", "PASS", attempts=[attempt(1, "PROVIDER_FAIL", "PROVIDER_RATE_LIMIT"), attempt(2, "PASS")]),
        result("B", "PROVIDER_FAIL", provider="PROVIDER_UNAVAILABLE", attempts=[attempt(1, "PROVIDER_FAIL", "PROVIDER_UNAVAILABLE"), attempt(2, "PROVIDER_FAIL", "PROVIDER_UNAVAILABLE")]),
        result("C", "PASS"),
    ]

    metrics = calculate_metrics(cases, results)

    assert metrics["provider_failure_attempts"] == 3
    assert metrics["cases_requiring_provider_retry"] == 2
    assert metrics["cases_still_provider_fail"] == 1
    assert metrics["task_success"] == [2, 3]

def test_posix_expected_path_matches_windows_actual_path():
    assert contains_all("source docs\\release.txt", ["docs/release.txt"])


def test_windows_expected_path_matches_posix_actual_path():
    assert contains_all("source docs/release.txt", ["docs\\release.txt"])


def test_different_path_does_not_match_after_normalization():
    assert not contains_all("source docs/release_notes.txt", ["docs/release.txt"])


def test_non_path_fact_preserves_existing_matching_behavior():
    assert contains_all("Project codename: Atlas", ["Atlas"])
    assert not contains_all("Project codename: Atla", ["Atlas"])


def test_a2_style_answer_satisfies_codename_and_posix_expected_path():
    answer = "The project codename is Atlas, and it came from the document docs\\release.txt."

    assert contains_all(answer, ["Atlas", "docs/release.txt"])
