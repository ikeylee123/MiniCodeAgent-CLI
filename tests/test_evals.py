import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import evals.run_eval as run_eval
from evals.run_eval import (
    AttemptResult,
    CaseResult,
    EvalCase,
    calculate_metrics,
    contains_all,
    classify_case,
    create_run_directory,
    current_working_tree_dirty,
    evaluate_trace,
    is_retryable_provider_failure,
    load_cases,
    render_markdown_report,
    retry_delay,
    run_case,
    run_selected_cases,
    write_summary,
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


def test_missing_llm_api_key_is_configuration_failure_before_agent_execution():
    status, provider, note = classify_case(
        case(category="recovery", requires_recovery=True),
        [],
        stdout="error: LLM mode requires a valid OPENAI_API_KEY.",
    )

    assert status == "CONFIG_FAIL"
    assert status != "AGENT_FAIL"
    assert status != "PROVIDER_FAIL"
    assert provider is None
    assert note == "required LLM credential unavailable before agent execution"


def test_missing_key_text_after_meaningful_agent_execution_remains_agent_failure():
    trace = [
        observation(1, "read_file", "unrelated"),
        final("error: LLM mode requires a valid OPENAI_API_KEY.", status="failed"),
    ]

    status, provider, _note = classify_case(case(requires_multi_step=False), trace)

    assert status == "AGENT_FAIL"
    assert provider is None


def test_http_503_remains_provider_unavailable():
    trace = [
        {"type": "error", "data": {"error": {"type": "LLMPlannerError", "message": "LLM API HTTP 503"}}},
        final("error: LLM API HTTP 503", status="failed"),
    ]

    status, provider, note = classify_case(case(), trace)

    assert status == "PROVIDER_FAIL"
    assert provider == "PROVIDER_UNAVAILABLE"
    assert note == "PROVIDER_UNAVAILABLE"


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

    report = render_markdown_report(
        cases, results, "Gemini OpenAI-compatible", "gemini-2.5-flash",
        run_id="20260918T094413Z-ab7d0780",
        artifact_directory=".eval-results/20260918T094413Z-ab7d0780")

    assert "# Agent Evaluation" in report
    assert "## Overall Results" in report
    assert "## Failure Analysis" in report
    assert "## Limitations" in report
    assert "Provider/API Failures" in report
    assert "Provider Failure Attempts" in report
    assert "No API keys or credentials" in report
    assert "20260918T094413Z-ab7d0780" in report
    assert ".eval-results/20260918T094413Z-ab7d0780" in report


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


def test_config_fail_is_not_retried(tmp_path):
    calls = []

    def fake_attempt(_case, _workspace, _results, _planner, _model, _timeout, attempt_number):
        calls.append(attempt_number)
        return result("T1", "CONFIG_FAIL")

    final_result = run_case(
        case(), tmp_path / "workspace", tmp_path / "results", "llm", None, 30.0,
        provider_retries=2, sleep_fn=lambda _: None, run_attempt_fn=fake_attempt)

    assert calls == [1]
    assert final_result.status == "CONFIG_FAIL"
    assert is_retryable_provider_failure(final_result) is False


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


def test_config_fail_is_excluded_from_agent_metric_denominators():
    cases = [
        case(id="A", category="recovery", requires_recovery=True),
        case(id="B", requires_multi_step=False),
        case(id="C", requires_multi_step=False),
    ]
    results = [
        result("A", "CONFIG_FAIL", 0, category="recovery"),
        result("B", "PASS", 1),
        result("C", "AGENT_FAIL", 1),
    ]

    metrics = calculate_metrics(cases, results)

    assert metrics["task_success"] == [1, 2]
    assert metrics["tool_selection_success"] == [2, 2]
    assert metrics["recovery_success"] == [0, 0]
    assert metrics["provider_api_failures"] == 0
    assert metrics["config_failures"] == 1
    assert metrics["average_tool_calls"] == 1.0


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


def test_run_directories_isolate_invocations_and_artifacts(tmp_path):
    results_root = tmp_path / "results"
    historical = results_root / "C1.attempt-2.trace.json"
    results_root.mkdir()
    historical.write_text("historical evidence", encoding="utf-8")
    fixed_now = lambda: datetime(2026, 9, 18, 9, 44, 13, tzinfo=timezone.utc)
    ids = iter(["ab7d0780aaaa", "cd9e1234bbbb"])

    run_id_1, run_dir_1, generated_at_1 = create_run_directory(
        results_root, fixed_now, lambda: next(ids))
    first_trace = run_dir_1 / "T1.attempt-1.trace.json"
    first_trace.write_text("first invocation", encoding="utf-8")

    def first_attempt(_case, _workspace, results, *_args):
        return CaseResult(
            id="T1", category="normal", status="PASS", final_status="completed",
            final_answer="42", tool_sequence=["read_file"], tool_call_count=1,
            trace_path=str(results / "T1.attempt-1.trace.json"))

    first_result = run_case(
        case(), tmp_path / "workspace", run_dir_1, "rule", None, 30.0,
        run_attempt_fn=first_attempt)
    first_summary = write_summary(
        [case()], [first_result], run_dir_1, run_id=run_id_1,
        generated_at=generated_at_1, model="fake-model", provider="fake-provider")
    first_snapshot = {path.name: path.read_bytes() for path in run_dir_1.iterdir()}

    run_id_2, run_dir_2, generated_at_2 = create_run_directory(
        results_root, fixed_now, lambda: next(ids))
    second_trace = run_dir_2 / "T1.attempt-1.trace.json"
    second_trace.write_text("second invocation", encoding="utf-8")

    def second_attempt(_case, _workspace, results, *_args):
        return CaseResult(
            id="T1", category="normal", status="PASS", final_status="completed",
            final_answer="42", tool_sequence=["read_file"], tool_call_count=1,
            trace_path=str(results / "T1.attempt-1.trace.json"))

    second_result = run_case(
        case(), tmp_path / "workspace", run_dir_2, "rule", None, 30.0,
        run_attempt_fn=second_attempt)
    second_summary = write_summary(
        [case()], [second_result], run_dir_2, run_id=run_id_2,
        generated_at=generated_at_2, model="fake-model", provider="fake-provider")

    assert run_id_1 == "20260918T094413Z-ab7d0780"
    assert run_id_2 == "20260918T094413Z-cd9e1234"
    assert run_dir_1 != run_dir_2
    assert first_trace.parent == first_summary.parent == run_dir_1
    assert second_trace.parent == second_summary.parent == run_dir_2
    assert first_trace.name == second_trace.name == "T1.attempt-1.trace.json"
    assert (run_dir_1 / "T1.result.json").exists()
    assert (run_dir_2 / "T1.result.json").exists()
    assert first_snapshot == {path.name: path.read_bytes() for path in run_dir_1.iterdir()}
    assert historical.read_text(encoding="utf-8") == "historical evidence"

    first_result_payload = json.loads((run_dir_1 / "T1.result.json").read_text(encoding="utf-8"))
    second_result_payload = json.loads((run_dir_2 / "T1.result.json").read_text(encoding="utf-8"))
    first_summary_payload = json.loads(first_summary.read_text(encoding="utf-8"))
    second_summary_payload = json.loads(second_summary.read_text(encoding="utf-8"))
    assert first_result_payload["run_id"] == run_id_1
    assert second_result_payload["run_id"] == run_id_2
    assert first_summary_payload["run_id"] == run_id_1
    assert second_summary_payload["run_id"] == run_id_2
    assert first_summary_payload["artifact_directory"] == str(run_dir_1)
    assert second_summary_payload["artifact_directory"] == str(run_dir_2)
    assert first_summary_payload["selected_case_ids"] == ["T1"]
    assert first_summary_payload["model"] == "fake-model"
    assert first_summary_payload["provider"] == "fake-provider"


def test_run_directory_collision_generates_a_new_id(tmp_path):
    fixed_now = lambda: datetime(2026, 9, 18, 9, 44, 13, tzinfo=timezone.utc)
    ids = iter(["duplicate0000", "duplicate0000", "unique990000"])
    first_id, first_dir, _ = create_run_directory(tmp_path, fixed_now, lambda: next(ids))
    second_id, second_dir, _ = create_run_directory(tmp_path, fixed_now, lambda: next(ids))

    assert first_id == "20260918T094413Z-duplicat"
    assert second_id == "20260918T094413Z-unique99"
    assert first_dir.exists()
    assert second_dir.exists()


def test_case_result_trace_path_stays_in_invocation_directory(tmp_path):
    run_dir = tmp_path / "20260918T094413Z-ab7d0780"

    item = run_case(
        case(), tmp_path / "workspace", run_dir, "rule", None, 30.0,
        run_attempt_fn=lambda _case, _workspace, results, *_args: CaseResult(
            id="T1", category="normal", status="PASS", final_status="completed",
            final_answer="42", tool_sequence=["read_file"], tool_call_count=1,
            trace_path=str(results / "T1.attempt-1.trace.json")))

    payload = json.loads((run_dir / "T1.result.json").read_text(encoding="utf-8"))
    assert Path(item.trace_path).parent == run_dir
    assert Path(payload["trace_path"]).parent == run_dir
    assert payload["run_id"] == run_dir.name


def test_working_tree_dirty_detects_clean_and_dirty_states():
    calls = []

    def fake_run(_cmd, **_kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=0, stdout="")

    assert current_working_tree_dirty(fake_run) is False

    def fake_dirty_run(_cmd, **_kwargs):
        calls.append(1)
        return SimpleNamespace(returncode=0, stdout=" M evals/run_eval.py\n")

    assert current_working_tree_dirty(fake_dirty_run) is True
    assert len(calls) == 2


def test_working_tree_dirty_handles_unavailable_git_state():
    assert current_working_tree_dirty(
        lambda _cmd, **_kwargs: SimpleNamespace(returncode=1, stdout="")) is None

    def unavailable_run(_cmd, **_kwargs):
        raise OSError("git unavailable")

    assert current_working_tree_dirty(unavailable_run) is None


def test_provenance_is_recorded_in_result_and_summary(tmp_path, monkeypatch):
    run_dir = tmp_path / "20260920T050000Z-12345678"
    item = run_case(
        case(), tmp_path / "workspace", run_dir, "rule", None, 30.0,
        run_attempt_fn=lambda *_args: result("T1", "PASS"),
        working_tree_dirty=False,
    )
    monkeypatch.setattr(run_eval, "current_commit", lambda: "abc123")
    summary_path = write_summary(
        [case()], [item], run_dir,
        run_id=run_dir.name,
        generated_at="2026-09-20T05:00:00+00:00",
        model="fake-model",
        provider="fake-provider",
        working_tree_dirty=False,
    )

    result_payload = json.loads(
        (run_dir / "T1.result.json").read_text(encoding="utf-8"))
    summary_payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert result_payload["working_tree_dirty"] is False
    assert summary_payload["working_tree_dirty"] is False
    assert summary_payload["commit"] == "abc123"


def test_unknown_repository_state_is_recorded_as_null(tmp_path, monkeypatch):
    run_dir = tmp_path / "20260920T050000Z-unknown0"
    run_dir.mkdir()
    monkeypatch.setattr(run_eval, "current_commit", lambda: "unknown")

    summary_path = write_summary(
        [case()], [result("T1", "PASS")], run_dir,
        working_tree_dirty=None,
    )

    payload = json.loads(summary_path.read_text(encoding="utf-8"))
    assert payload["working_tree_dirty"] is None


def test_repository_state_is_determined_once_per_invocation(tmp_path, monkeypatch):
    calls = []
    captured = {}
    run_dir = tmp_path / "20260920T050000Z-once0001"
    run_dir.mkdir()

    monkeypatch.setattr(run_eval, "load_cases", lambda: [case()])
    monkeypatch.setattr(run_eval, "create_workspace", lambda _path: None)
    monkeypatch.setattr(
        run_eval,
        "create_run_directory",
        lambda _root: (
            run_dir.name,
            run_dir,
            "2026-09-20T05:00:00+00:00",
        ),
    )

    def fake_repository_state():
        calls.append(1)
        return True

    def fake_run_selected(*_args, **kwargs):
        captured["run_case_fn"] = kwargs["run_case_fn"]
        return [result("T1", "PASS")]

    def fake_write_summary(*_args, **kwargs):
        captured["summary_dirty"] = kwargs["working_tree_dirty"]
        return run_dir / "summary.json"

    monkeypatch.setattr(run_eval, "current_working_tree_dirty", fake_repository_state)
    monkeypatch.setattr(run_eval, "run_selected_cases", fake_run_selected)
    monkeypatch.setattr(run_eval, "write_summary", fake_write_summary)

    exit_code = run_eval.main([
        "--case", "T1",
        "--planner", "rule",
        "--workspace", str(tmp_path / "workspace"),
        "--results", str(tmp_path / "results"),
    ])

    assert exit_code == 0
    assert calls == [1]
    assert captured["summary_dirty"] is True
    assert captured["run_case_fn"].keywords["working_tree_dirty"] is True
