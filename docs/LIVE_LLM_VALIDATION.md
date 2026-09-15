# Live LLM Validation

Date: 2026-09-14  
OS: Windows PowerShell on Windows  
Commit: `762342944c0bf6c066c4a21e5d2a3d06d386ece5`  
Provider/API shape: Gemini OpenAI-compatible Chat Completions endpoint  
Model: `gemini-2.5-flash`  
Workspace: synthetic disposable workspace under `.live-validation-workspace`

The validation used only synthetic files:

- `docs/release.txt`: release date and project codename
- `docs/security_notes.txt`: permission and unsafe Python notes
- `config/service.txt`: synthetic service configuration

No API keys, authorization headers, provider response bodies, or raw credential values are recorded in these docs. The copied traces in `docs/traces/` replace local absolute workspace paths with `<validation-workspace>` where needed.

## Results

| Task | Prompt | Status | Trace |
| --- | --- | --- | --- |
| A | `Search the workspace for the release date, read the matching document, and report the release date and project codename.` | PASS | [`live-task-a.json`](traces/live-task-a.json) |
| B | `Read wrong/release.txt and report the release date. If the path is invalid, recover by locating the correct file and continue.` | PASS | [`live-task-b.json`](traces/live-task-b.json) |
| C | `Find the project codename in the workspace and calculate the sum of the integers from 1 through 100. Return both results.` | FAIL | [`live-task-c.json`](traces/live-task-c.json) |

## Task A

Result: PASS

Tool sequence:

```text
list_files {"path":"."}
read_file {"path":"docs\\release.txt"}
FINAL "Release date: 2026-10-01. Project codename: Atlas."
```

The final answer is supported by the `read_file` observation from `docs\\release.txt`.

## Task B

Result: PASS

Tool sequence:

```text
read_file {"path":"wrong/release.txt"} -> FileNotFoundError
list_files {"path":"."}
read_file {"path":"docs/release.txt"}
FINAL "The release date is 2026-10-01."
```

This validates live LLM failure recovery for this scenario. The model first attempted the wrong path requested by the prompt, received a structured file-not-found observation, listed available files, selected the correct release file, and completed the task.

## Task C

Result: FAIL

Observed tool sequence before failure:

```text
list_files {"path":"."}
read_file {"path":"docs\\release.txt"}
run_python {"code":"print(sum(range(1, 101)))"}
ERROR "LLM API HTTP 429; check credentials, model and strict JSON Schema support."
FINAL failed
```

The trace gives partial evidence for autonomous tool selection: the model located and read the release file, then independently selected `run_python` to calculate `5050`. The run is still classified as failed because the final planner call returned HTTP 429 before the model produced a completed final answer containing both `Atlas` and `5050`.

## Screenshot

No terminal screenshot is committed. A legitimate screenshot should be captured manually from the credential-bearing PowerShell session if needed. Recommended replay command for a clean screenshot after the live traces exist:

```powershell
Set-Location -LiteralPath "<repo-root>"
Get-Content -LiteralPath ".\docs\LIVE_LLM_VALIDATION.md"
```

For a live terminal screenshot, capture the original PowerShell output immediately after rerunning a task. Do not use generated or fabricated screenshots for this validation.

## Claim Audit

| Claim | Classification | Basis |
| --- | --- | --- |
| multi-step tool-calling agent | STRONGLY SUPPORTED | A, B, and C traces show multiple tool calls in one run. |
| LLM-powered planning | STRONGLY SUPPORTED | Live traces show planner decisions from `gemini-2.5-flash` using `--planner llm`. |
| observation-driven planning | STRONGLY SUPPORTED | A and B selected later tools from prior observations; B recovered from a structured error observation. |
| re-planning | STRONGLY SUPPORTED | The planner was invoked after observations in A, B, and C. |
| failure recovery | STRONGLY SUPPORTED | B recovered from `FileNotFoundError` and completed. |
| autonomous tool selection | PARTIALLY SUPPORTED | A/B/C show live tool choices; C selected `run_python`, but the final answer failed due HTTP 429. |
| context/state management | STRONGLY SUPPORTED | Traces preserve prompts, step numbers, actions, observations, errors, and final status. |
| JSON tracing | STRONGLY SUPPORTED | All runs produced JSON trace files with prompt, plan, observation, error, and final events as applicable. |
| restricted Python execution | STRONGLY SUPPORTED | C executed allowed restricted Python through `run_python`; offline demos/tests cover blocked unsafe patterns. |
| ReAct-style agent loop | STRONGLY SUPPORTED | Live LLM decisions iteratively depended on observations in A and B. |
| sandboxed execution | NOT SUPPORTED | There is no process, VM, or container isolation for `run_python`; it is restricted in-process execution. |

## Remaining Gaps

1. Provider reliability is not handled; Task C failed on HTTP 429 and there are no retries or resumable planner calls.
2. The optional LLM planner depends on strict Chat Completions JSON Schema support from the configured provider/model.
3. `run_python` remains an in-process restricted demo tool, not a real sandbox with CPU, memory, filesystem, or process isolation.

## Conservative Resume Bullets

- Built a bounded single-agent tool loop with typed state, JSON execution traces, permission checks, and planner-driven re-planning after tool observations.
- Added an optional OpenAI-compatible LLM planner that emits strict structured `TOOL` or `FINAL` decisions while keeping deterministic rule-mode tests independent of live APIs.
- Validated live multi-step retrieval and file-not-found recovery with Gemini `gemini-2.5-flash`; documented a rate-limit failure case without overstating full task completion.
