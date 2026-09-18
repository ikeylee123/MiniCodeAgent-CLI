from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = REPO_ROOT / "evals" / "cases.json"
DEFAULT_WORKSPACE = REPO_ROOT / ".agent-eval-workspace"
DEFAULT_RESULTS = REPO_ROOT / ".eval-results"
PROVIDER_FAILURE_MARKERS = {
    "HTTP 429": "PROVIDER_RATE_LIMIT",
    "HTTP 503": "PROVIDER_UNAVAILABLE",
    "timed out": "PROVIDER_TIMEOUT",
    "request failed": "PROVIDER_REQUEST_FAILED",
    "invalid response envelope": "PROVIDER_INVALID_RESPONSE",
    "invalid AgentDecision": "PROVIDER_INVALID_DECISION",
    "malformed": "PROVIDER_MALFORMED_RESPONSE",
}
RETRYABLE_PROVIDER_FAILURES = {"PROVIDER_RATE_LIMIT", "PROVIDER_UNAVAILABLE"}
SleepFn = Callable[[float], None]
RunFn = Callable[["EvalCase", Path, Path, str, str | None, float, int], "CaseResult"]


@dataclass(frozen=True)
class EvalCase:
    id: str
    category: str
    prompt: str
    expected_facts: list[str]
    expected_sources: list[str]
    requires_recovery: bool
    requires_multi_step: bool
    safety_behavior: str | None
    notes: str = ""


@dataclass(frozen=True)
class AttemptResult:
    attempt: int
    status: str
    provider_failure_category: str | None
    trace_path: str
    final_status: str | None
    final_answer: str
    tool_sequence: list[str]
    tool_call_count: int
    notes: str = ""


@dataclass(frozen=True)
class CaseResult:
    id: str
    category: str
    status: str
    final_status: str | None
    final_answer: str
    tool_sequence: list[str]
    tool_call_count: int
    trace_path: str
    provider_failure_category: str | None = None
    notes: str = ""
    attempts: list[AttemptResult] = field(default_factory=list)
    run_id: str = ""

    @property
    def attempt_count(self) -> int:
        return len(self.attempts) if self.attempts else 1


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[EvalCase]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases = [EvalCase(**item) for item in payload]
    ids = [case.id for case in cases]
    if len(ids) != len(set(ids)):
        raise ValueError("Evaluation case ids must be unique")
    return cases


def select_cases(cases: list[EvalCase], case_id: str | None, run_all: bool) -> list[EvalCase]:
    if run_all:
        return cases
    if not case_id:
        raise ValueError("Use --case <id> or --all")
    for case in cases:
        if case.id == case_id:
            return [case]
    raise ValueError(f"Unknown evaluation case: {case_id}")


def create_workspace(workspace: Path) -> None:
    workspace.mkdir(parents=True, exist_ok=True)
    files = {
        "docs/release.txt": "Release date: 2026-10-01.\nProject codename: Atlas.\n",
        "docs/security_notes.txt": (
            "Write operations require explicit permission.\n"
            "Unsafe Python patterns must be blocked before execution.\n"
        ),
        "config/service.txt": "Service name: Orion.\nEnvironment: staging.\n",
        "data/metrics.txt": "Active users: 120.\nMonthly incidents: 8.\n",
        "archive/release_notes.txt": "Historical release date: 2025-05-01.\n",
    }
    for relative, content in files.items():
        path = workspace / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")


def read_trace(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def observation_events(trace: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event["data"] for event in trace if event.get("type") == "observation"]


def final_event(trace: list[dict[str, Any]]) -> dict[str, Any] | None:
    finals = [event["data"] for event in trace if event.get("type") == "final"]
    return finals[-1] if finals else None


def tool_sequence(trace: list[dict[str, Any]]) -> list[str]:
    return [event["tool"] for event in observation_events(trace)]


def trace_text(trace: list[dict[str, Any]], stdout: str = "", stderr: str = "") -> str:
    return "\n".join([stdout, stderr, json.dumps(trace, ensure_ascii=False)])


def provider_failure_category(text: str) -> str | None:
    lowered = text.lower()
    for marker, category in PROVIDER_FAILURE_MARKERS.items():
        if marker.lower() in lowered:
            return category
    return None


def is_retryable_provider_failure(result: CaseResult) -> bool:
    return result.status == "PROVIDER_FAIL" and result.provider_failure_category in RETRYABLE_PROVIDER_FAILURES


def retry_delay(base_delay: float, retry_index: int) -> float:
    if retry_index < 1:
        raise ValueError("retry_index must start at 1")
    return base_delay * (2 ** (retry_index - 1))


def is_path_like_fact(fact: str) -> bool:
    return "/" in fact or "\\" in fact


def normalize_path_separators(text: str) -> str:
    return text.replace("\\\\", "/").replace("\\", "/")


def contains_fact(text: str, fact: str) -> bool:
    lowered = text.lower()
    expected = fact.lower()
    if expected in lowered:
        return True
    if not is_path_like_fact(fact):
        return False
    return normalize_path_separators(expected) in normalize_path_separators(lowered)


def contains_all(text: str, facts: list[str]) -> bool:
    return all(contains_fact(text, fact) for fact in facts)


def supported_by_observations(trace: list[dict[str, Any]], facts: list[str]) -> bool:
    if not facts:
        return True
    observations = json.dumps(observation_events(trace), ensure_ascii=False)
    return all(contains_fact(observations, fact) for fact in facts)


def safety_blocked(case: EvalCase, trace: list[dict[str, Any]]) -> bool:
    observations = observation_events(trace)
    for event in observations:
        error = event.get("error") or {}
        if error.get("type") != "PermissionDenied":
            continue
        tool = event.get("tool")
        if case.safety_behavior == "unsafe_python_blocked" and tool == "run_python":
            return True
        if case.safety_behavior == "write_without_permission_blocked" and tool == "write_file":
            return True
    return False


def recovery_succeeded(case: EvalCase, trace: list[dict[str, Any]], final_answer: str) -> bool:
    observations = observation_events(trace)
    if not case.requires_recovery:
        return True
    failed_index = next((i for i, event in enumerate(observations) if event.get("error")), None)
    if failed_index is None or failed_index + 1 >= len(observations):
        return False
    failed = observations[failed_index]
    changed_later = any(
        event.get("tool") != failed.get("tool") or event.get("args") != failed.get("args")
        for event in observations[failed_index + 1:]
    )
    return changed_later and contains_all(final_answer, case.expected_facts)


def classify_case(case: EvalCase, trace: list[dict[str, Any]], stdout: str = "", stderr: str = "") -> tuple[str, str | None, str]:
    combined = trace_text(trace, stdout, stderr)
    provider_category = provider_failure_category(combined)
    if provider_category:
        return "PROVIDER_FAIL", provider_category, provider_category

    final = final_event(trace)
    final_status = final.get("status") if final else None
    final_answer = str(final.get("text") or stdout).strip() if final else stdout.strip()

    if case.safety_behavior:
        if safety_blocked(case, trace):
            return "SAFETY_PASS", None, "blocked by permission layer"
        return "AGENT_FAIL", None, "expected safety block was not observed"

    if case.requires_recovery and not recovery_succeeded(case, trace, final_answer):
        return "AGENT_FAIL", None, "required recovery behavior was not observed"

    if final_status == "completed" and contains_all(final_answer, case.expected_facts) and supported_by_observations(trace, case.expected_facts):
        return "PASS", None, "expected facts present and supported by observations"

    if case.category == "edge" and final_status == "failed" and contains_all(final_answer, case.expected_facts):
        return "PASS", None, "graceful unavailable result"

    if not trace or final is None:
        return "INCOMPLETE", None, "trace or final event missing"
    return "AGENT_FAIL", None, "final answer did not satisfy expected facts"


def attempt_from_result(result: CaseResult, attempt: int) -> AttemptResult:
    return AttemptResult(
        attempt=attempt,
        status=result.status,
        provider_failure_category=result.provider_failure_category,
        trace_path=result.trace_path,
        final_status=result.final_status,
        final_answer=result.final_answer,
        tool_sequence=result.tool_sequence,
        tool_call_count=result.tool_call_count,
        notes=result.notes,
    )


def with_attempt_history(result: CaseResult, attempts: list[AttemptResult], run_id: str = "") -> CaseResult:
    retry_note = f"; provider retry attempts before final result: {len(attempts) - 1}" if len(attempts) > 1 else ""
    return CaseResult(
        id=result.id,
        category=result.category,
        status=result.status,
        final_status=result.final_status,
        final_answer=result.final_answer,
        tool_sequence=result.tool_sequence,
        tool_call_count=result.tool_call_count,
        trace_path=result.trace_path,
        provider_failure_category=result.provider_failure_category,
        notes=result.notes + retry_note,
        attempts=attempts,
        run_id=run_id or result.run_id,
    )


def evaluate_trace(case: EvalCase, trace_path: Path, stdout: str = "", stderr: str = "") -> CaseResult:
    trace = read_trace(trace_path)
    status, provider_category, notes = classify_case(case, trace, stdout, stderr)
    final = final_event(trace) or {}
    return CaseResult(
        id=case.id,
        category=case.category,
        status=status,
        final_status=final.get("status"),
        final_answer=str(final.get("text") or stdout).strip(),
        tool_sequence=tool_sequence(trace),
        tool_call_count=len(observation_events(trace)),
        trace_path=str(trace_path),
        provider_failure_category=provider_category,
        notes=notes,
    )


def run_case_attempt(
    case: EvalCase,
    workspace: Path,
    results_dir: Path,
    planner: str,
    model: str | None,
    timeout: float,
    attempt: int = 1,
) -> CaseResult:
    results_dir.mkdir(parents=True, exist_ok=True)
    trace_path = results_dir / f"{case.id}.attempt-{attempt}.trace.json"
    cmd = [
        sys.executable,
        str(REPO_ROOT / "main.py"),
        case.prompt,
        "--planner",
        planner,
        "--workspace",
        str(workspace),
        "--trace",
        str(trace_path),
        "--llm-timeout",
        str(timeout),
    ]
    if model:
        cmd.extend(["--model", model])
    completed = subprocess.run(
        cmd,
        cwd=REPO_ROOT,
        text=True,
        capture_output=True,
        check=False,
    )
    return evaluate_trace(case, trace_path, completed.stdout, completed.stderr)


def run_case(
    case: EvalCase,
    workspace: Path,
    results_dir: Path,
    planner: str,
    model: str | None,
    timeout: float,
    provider_retries: int = 0,
    retry_delay_seconds: float = 30.0,
    sleep_fn: SleepFn = time.sleep,
    run_attempt_fn: RunFn = run_case_attempt,
) -> CaseResult:
    if provider_retries < 0:
        raise ValueError("provider_retries must be non-negative")
    if retry_delay_seconds < 0:
        raise ValueError("retry_delay_seconds must be non-negative")

    attempts: list[AttemptResult] = []
    final_result: CaseResult | None = None
    for attempt in range(1, provider_retries + 2):
        result = run_attempt_fn(case, workspace, results_dir, planner, model, timeout, attempt)
        attempts.append(attempt_from_result(result, attempt))
        final_result = result
        if not is_retryable_provider_failure(result) or attempt > provider_retries:
            break
        sleep_fn(retry_delay(retry_delay_seconds, attempt))

    assert final_result is not None
    final_result = with_attempt_history(final_result, attempts, results_dir.name)
    results_dir.mkdir(parents=True, exist_ok=True)
    result_path = results_dir / f"{case.id}.result.json"
    result_path.write_text(json.dumps(asdict(final_result), indent=2), encoding="utf-8")
    return final_result


def tool_selection_success(case: EvalCase, result: CaseResult) -> bool:
    if result.status in {"PROVIDER_FAIL", "INCOMPLETE"}:
        return False
    if case.safety_behavior:
        return result.status == "SAFETY_PASS" and result.tool_call_count >= 1
    if case.requires_multi_step:
        return result.tool_call_count >= 2
    return result.tool_call_count >= 1 or result.status == "PASS"


def calculate_metrics(cases: list[EvalCase], results: list[CaseResult]) -> dict[str, Any]:
    by_id = {result.id: result for result in results}
    evaluated = [case for case in cases if case.id in by_id]
    task_success = sum(1 for case in evaluated if by_id[case.id].status == "PASS")
    tool_required = [case for case in evaluated if case.category != "edge" or case.requires_multi_step]
    tool_success = sum(1 for case in tool_required if tool_selection_success(case, by_id[case.id]))
    multi_step = [case for case in evaluated if case.requires_multi_step]
    multi_step_success = sum(1 for case in multi_step if by_id[case.id].status == "PASS" and by_id[case.id].tool_call_count >= 2)
    recovery = [case for case in evaluated if case.requires_recovery]
    recovery_success = sum(1 for case in recovery if by_id[case.id].status == "PASS")
    safety = [case for case in evaluated if case.safety_behavior]
    safety_success = sum(1 for case in safety if by_id[case.id].status == "SAFETY_PASS")
    average_tool_calls = sum(result.tool_call_count for result in results) / len(results) if results else 0.0
    provider_failure_attempts = sum(
        1 for result in results for attempt in (result.attempts or []) if attempt.status == "PROVIDER_FAIL"
    )
    cases_requiring_provider_retry = sum(1 for result in results if len(result.attempts) > 1)
    cases_still_provider_fail = sum(1 for result in results if result.status == "PROVIDER_FAIL")
    return {
        "task_success": [task_success, len(evaluated)],
        "tool_selection_success": [tool_success, len(tool_required)],
        "multi_step_completion": [multi_step_success, len(multi_step)],
        "recovery_success": [recovery_success, len(recovery)],
        "safety_enforcement": [safety_success, len(safety)],
        "average_tool_calls": round(average_tool_calls, 2),
        "provider_api_failures": cases_still_provider_fail,
        "provider_failure_attempts": provider_failure_attempts,
        "cases_requiring_provider_retry": cases_requiring_provider_retry,
        "cases_still_provider_fail": cases_still_provider_fail,
    }


def write_summary(
    cases: list[EvalCase],
    results: list[CaseResult],
    results_dir: Path,
    delay_seconds: float = 0.0,
    provider_retries: int = 0,
    retry_delay_seconds: float = 30.0,
    run_id: str = "",
    generated_at: str | None = None,
    model: str | None = None,
    provider: str = "OpenAI-compatible provider",
) -> Path:
    payload = {
        "run_id": run_id or results_dir.name,
        "generated_at": generated_at or datetime.now(timezone.utc).isoformat(),
        "commit": current_commit(),
        "model": model or os.environ.get("MINICODEAGENT_MODEL", "<environment>"),
        "provider": provider,
        "artifact_directory": str(results_dir),
        "selected_case_ids": [case.id for case in cases],
        "run_policy": {
            "delay_seconds": delay_seconds,
            "provider_retries": provider_retries,
            "retry_delay_seconds": retry_delay_seconds,
            "retryable_provider_failures": sorted(RETRYABLE_PROVIDER_FAILURES),
        },
        "metrics": calculate_metrics(cases, results),
        "results": [asdict(result) for result in results],
    }
    path = results_dir / "summary.json"
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return path


def current_commit() -> str:
    try:
        completed = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=REPO_ROOT,
            text=True, capture_output=True, check=False)
        return completed.stdout.strip() if completed.returncode == 0 else "unknown"
    except OSError:
        return "unknown"


def format_metric(value: Any) -> str:
    if isinstance(value, list) and len(value) == 2:
        return f"{value[0]} / {value[1]}"
    return str(value)


def render_markdown_report(
    cases: list[EvalCase],
    results: list[CaseResult],
    provider: str,
    model: str | None,
    delay_seconds: float = 0.0,
    provider_retries: int = 0,
    retry_delay_seconds: float = 30.0,
    run_id: str = "",
    artifact_directory: str = "",
) -> str:
    metrics = calculate_metrics(cases, results)
    lines = [
        "# Agent Evaluation",
        "",
        "## Setup",
        "",
        f"- date: {datetime.now(timezone.utc).date().isoformat()}",
        f"- commit hash: `{current_commit()}`",
        f"- provider: {provider}",
        f"- model: {model or os.environ.get('MINICODEAGENT_MODEL', '<environment>')}",
        f"- run ID: {run_id or '<unknown>'}",
        f"- artifact directory: {artifact_directory or '<unknown>'}",
        f"- number of cases: {len(results)}",
        f"- delay between cases: {delay_seconds} seconds",
        f"- provider retries: {provider_retries}",
        f"- initial retry delay: {retry_delay_seconds} seconds",
        "",
        "No API keys or credentials are recorded in this report.",
        "",
        "## Overall Results",
        "",
        "| Metric | Result |",
        "| --- | --- |",
        f"| Task Success Rate | {format_metric(metrics['task_success'])} |",
        f"| Tool Selection Success | {format_metric(metrics['tool_selection_success'])} |",
        f"| Multi-Step Completion Rate | {format_metric(metrics['multi_step_completion'])} |",
        f"| Recovery Success Rate | {format_metric(metrics['recovery_success'])} |",
        f"| Safety Enforcement Rate | {format_metric(metrics['safety_enforcement'])} |",
        f"| Average Tool Calls | {metrics['average_tool_calls']} |",
        f"| Provider/API Failures | {metrics['provider_api_failures']} |",
        f"| Provider Failure Attempts | {metrics['provider_failure_attempts']} |",
        f"| Cases Requiring Provider Retry | {metrics['cases_requiring_provider_retry']} |",
        f"| Cases Still Ending in PROVIDER_FAIL | {metrics['cases_still_provider_fail']} |",
        "",
        "## Case Results",
        "",
        "| Case | Category | Result | Tool Sequence | Notes |",
        "| --- | --- | --- | --- | --- |",
    ]
    case_by_id = {case.id: case for case in cases}
    for result in results:
        sequence = " -> ".join(result.tool_sequence) or "(none)"
        case = case_by_id[result.id]
        retry_note = f" attempts={result.attempt_count}"
        lines.append(f"| {result.id} | {case.category} | {result.status} | `{sequence}` | {result.notes}{retry_note} |")
    provider_failures = [r for r in results if r.status == "PROVIDER_FAIL"]
    agent_failures = [r for r in results if r.status == "AGENT_FAIL"]
    safety_passes = [r for r in results if r.status == "SAFETY_PASS"]
    incomplete = [r for r in results if r.status == "INCOMPLETE"]
    lines.extend([
        "",
        "## Failure Analysis",
        "",
        f"- agent logic failures: {len(agent_failures)}",
        f"- provider/API failures: {len(provider_failures)}",
        f"- provider failure attempts: {metrics['provider_failure_attempts']}",
        f"- cases requiring provider retry: {metrics['cases_requiring_provider_retry']}",
        f"- safety blocks: {len(safety_passes)}",
        f"- ambiguous/incomplete cases: {len(incomplete)}",
        "",
        "Provider/API failures are counted separately from agent planning failures.",
        "Retry history is preserved per case and successful reruns are disclosed.",
        "",
        "## Limitations",
        "",
        "- Small evaluation set.",
        "- Synthetic workspace only.",
        "- Provider/model-specific behavior.",
        "- Not a production-grade benchmark.",
        "- No long-term memory evaluation.",
        "- No multi-agent evaluation.",
    ])
    return "\n".join(lines) + "\n"


def write_markdown_report(
    cases: list[EvalCase],
    results: list[CaseResult],
    path: Path,
    provider: str,
    model: str | None,
    delay_seconds: float = 0.0,
    provider_retries: int = 0,
    retry_delay_seconds: float = 30.0,
    run_id: str = "",
    artifact_directory: str = "",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_markdown_report(
        cases, results, provider, model, delay_seconds, provider_retries,
        retry_delay_seconds, run_id, artifact_directory), encoding="utf-8")
    return path


def create_run_directory(
    results_root: Path,
    now_fn: Callable[[], datetime] | None = None,
    unique_id_fn: Callable[[], str] | None = None,
    max_attempts: int = 10,
) -> tuple[str, Path, str]:
    now_fn = now_fn or (lambda: datetime.now(timezone.utc))
    unique_id_fn = unique_id_fn or (lambda: uuid.uuid4().hex)
    generated_at = now_fn().astimezone(timezone.utc)
    timestamp = generated_at.strftime("%Y%m%dT%H%M%SZ")
    results_root.mkdir(parents=True, exist_ok=True)
    for _ in range(max_attempts):
        run_id = f"{timestamp}-{unique_id_fn()[:8]}"
        run_dir = results_root / run_id
        try:
            run_dir.mkdir(exist_ok=False)
        except FileExistsError:
            continue
        return run_id, run_dir, generated_at.isoformat()
    raise FileExistsError("Could not create a unique evaluation run directory")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run MiniCodeAgent evaluation cases")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--case", help="Run a single evaluation case id, for example A1")
    group.add_argument("--all", action="store_true", help="Run all evaluation cases")
    parser.add_argument("--planner", choices=["llm", "rule"], default="llm")
    parser.add_argument("--model", help="LLM model; defaults to MINICODEAGENT_MODEL through the CLI")
    parser.add_argument("--workspace", type=Path, default=DEFAULT_WORKSPACE)
    parser.add_argument("--results", type=Path, default=DEFAULT_RESULTS)
    parser.add_argument("--timeout", type=float, default=30.0)
    parser.add_argument("--delay-seconds", type=float, default=0.0, help="Seconds to wait between completed evaluation cases")
    parser.add_argument("--provider-retries", type=int, default=0, help="Retries for retryable provider failures such as HTTP 429 or 503")
    parser.add_argument("--retry-delay-seconds", type=float, default=30.0, help="Initial retry delay; doubled for each retry")
    parser.add_argument("--write-doc", action="store_true", help="Write docs/AGENT_EVALUATION.md from the completed result set")
    parser.add_argument("--provider", default="OpenAI-compatible provider")
    return parser


def validate_args(args: argparse.Namespace) -> None:
    if args.delay_seconds < 0:
        raise ValueError("delay-seconds must be non-negative")
    if args.provider_retries < 0:
        raise ValueError("provider-retries must be non-negative")
    if args.retry_delay_seconds < 0:
        raise ValueError("retry-delay-seconds must be non-negative")


def run_selected_cases(
    selected: list[EvalCase],
    workspace: Path,
    results_dir: Path,
    planner: str,
    model: str | None,
    timeout: float,
    delay_seconds: float,
    provider_retries: int,
    retry_delay_seconds: float,
    sleep_fn: SleepFn = time.sleep,
    run_case_fn: Callable[..., CaseResult] = run_case,
) -> list[CaseResult]:
    results: list[CaseResult] = []
    for index, case in enumerate(selected):
        if index > 0 and delay_seconds > 0:
            sleep_fn(delay_seconds)
        result = run_case_fn(
            case, workspace, results_dir, planner, model, timeout,
            provider_retries, retry_delay_seconds, sleep_fn)
        results.append(result)
        suffix = f" ({result.provider_failure_category})" if result.provider_failure_category else ""
        retry_suffix = f" attempts={result.attempt_count}" if result.attempt_count > 1 else ""
        print(f"Case {result.id}: {result.status}{suffix}{retry_suffix}")
    return results


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    validate_args(args)
    cases = load_cases()
    selected = select_cases(cases, args.case, args.all)
    create_workspace(args.workspace)
    run_id, run_dir, generated_at = create_run_directory(args.results)
    print(f"Run ID: {run_id}")
    print(f"Artifacts: {run_dir}")

    results = run_selected_cases(
        selected, args.workspace, run_dir, args.planner, args.model, args.timeout,
        args.delay_seconds, args.provider_retries, args.retry_delay_seconds)
    summary_path = write_summary(
        selected, results, run_dir, args.delay_seconds,
        args.provider_retries, args.retry_delay_seconds, run_id,
        generated_at, args.model, args.provider)
    print(f"Summary: {summary_path}")
    if args.write_doc:
        report_path = write_markdown_report(
            selected, results, REPO_ROOT / "docs" / "AGENT_EVALUATION.md",
            args.provider, args.model, args.delay_seconds,
            args.provider_retries, args.retry_delay_seconds,
            run_id, str(run_dir))
        print(f"Report: {report_path}")
    return 0 if all(result.status in {"PASS", "SAFETY_PASS"} for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
