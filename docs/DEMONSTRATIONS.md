# Demonstration scenarios

Run from the repository root, with no API key and no network:

```bash
python -m examples.demo_flows
```

On Windows where `python` is a Store alias, use `py -3.13 -m examples.demo_flows`.
The runner creates disposable workspaces with a small release document and writes
persistent JSON traces under `logs/demos/`. These are explicit demo fixtures, not
facts inferred about the repository. Temporary fixture files are removed when each
demo finishes. The runner prints `deterministic, offline`; it does not pretend to
use an LLM. Rerunning replaces only its three named demo trace files.

| Demo | Actual tool sequence | Expected result |
| --- | --- | --- |
| A: successful task | `search_text(query="release date")` -> `read_file(path=<search result>)` -> FINAL | `Document contents: Release date: 2026-10-01.` |
| B: recovery | `read_file("wrong/release.txt")` -> FileNotFoundError -> `list_files` -> `read_file("docs/release.txt")` -> FINAL | `Release date: 2026-10-01.` |
| C: unsafe action | `run_python("import os")` -> PermissionDenied -> FINAL | `error: Unsafe Python pattern blocked: import os` |

Demo A injects `SearchReadDemoPlanner`, a small rule whose next path and answer
come from actual observations. Demo B uses the default `RuleBasedPlanner` and its
unique-basename recovery rule. Demo C uses the same default planner and existing
permission controller. No tool output is mocked in these demonstrations.

Inspect `logs/demos/demo-A.json`, `demo-B.json`, and `demo-C.json`. B records its
first error and subsequent successful steps in one run. C records the attempted
code, `PermissionDenied`, and final `failed` status; the blocked code is never
executed. C failing is the expected security demonstration, so the overall demo
runner completes successfully. Trace files contain goal and tool content and are
not a general secret-redaction facility; use these harmless fixtures for sharing.

## Optional live variation

After explicitly configuring the optional planner as described in the README,
you can run against your chosen disposable workspace:

```bash
python main.py "Search for release date, read the matching document, and report its contents" --planner llm --workspace demo-workspace --trace logs/live-demo.json --show-trace
```

Create your own `demo-workspace` and document first. This command sends the goal
and subsequent observations to the configured provider. It is a live usage example,
not part of the automated demonstration or test results. Exact model actions and
answers are not guaranteed. The offline demo runner is the reproducible evidence.
