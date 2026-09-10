# MiniCodeAgent CLI

[![Tests](https://github.com/ikeylee123/MiniCodeAgent-CLI/actions/workflows/tests.yml/badge.svg)](https://github.com/ikeylee123/MiniCodeAgent-CLI/actions/workflows/tests.yml)

MiniCodeAgent CLI is a lightweight tool-calling coding agent prototype. It is
designed as a small resume project that demonstrates an agent loop, a modular
tool registry, permission checks, execution tracing, and pytest coverage without
requiring an LLM API key.

## Features

- Command-line entry point through `main.py` or the `minicodeagent` script.
- A bounded single-agent loop that plans again after each tool observation,
  including failures, using an injectable planner and typed run state.
- Optional OpenAI-compatible LLM planner using strict structured decisions and
  the Python standard library; default operation remains offline.
- Modular `ToolRegistry` for adding and looking up tools.
- Built-in tools:
  - `list_files`
  - `read_file`
  - `write_file`
  - `search_text`
  - `run_python`
- Permission controls:
  - allowlisted tools
  - blocked unsafe Python patterns
  - write protection with `--allow-write` or `--dry-run`
- JSON execution trace logging.
- Deterministic pytest coverage for sequential workflows, recovery, loop
  protection, registry behavior, permissions, tracing, and CLI sessions.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e ".[dev]"
```

On macOS or Linux, activate the virtual environment with:

```bash
source .venv/bin/activate
```

## Usage

MiniCodeAgent has two layers of interaction:

- CLI flags control mode, permissions, and trace behavior.
- By default, prompt commands are parsed by the rule-based planner into tool calls.
- Explicit `--planner llm` selection uses the configured remote model instead.

## Command Reference

### One-shot mode

Run a single prompt:

```bash
python main.py "<prompt>"
```

Examples:

- `python main.py "list files"`
- `python main.py "read README.md"`
- `python main.py "search agent"`
- `python main.py "write notes.txt hello world" --dry-run`
- `python main.py "python print(sum(range(5)))"`

### Interactive mode

Start a session:

```bash
python main.py --interactive
```

Inside the session, enter prompt commands one at a time:

- `list files`
- `read README.md`
- `search permission`
- `write notes.txt hello world`
- `python print(sum(range(5)))`
- `help`
- `history`
- `last`
- `clear`

Exit commands:

- `exit`
- `quit`

### Supported prompt patterns

The default rule-based planner recognizes these patterns:

| Prompt pattern | Tool call |
| --- | --- |
| `list files` | `list_files(path=".")` |
| `list <path>` | `list_files(path="<path>")` |
| `ls` | `list_files(path=".")` |
| `read <path>` | `read_file(path="<path>")` |
| `search <query> [path]` | `search_text(query="<query>", path="[path or .]")` |
| `write <path> <content>` | `write_file(path="<path>", content="<content>")` |
| `python <code>` | `run_python(code="<code>")` |

If a prompt does not match a known pattern, the current fallback behavior is to
return a friendly error message.

### CLI flags

These flags are passed when starting the program, not typed inside the
interactive prompt:

- `--interactive`: start a session of independent agent runs
- `--max-steps <n>`: maximum tool attempts per run (positive integer, default 6)
- `--list-tools`: print available tools and schemas
- `--show-trace`: print the JSON trace after each run
- `--trace <path>`: write JSON trace events to a custom file
- `--allow-write`: allow real file writes
- `--dry-run`: preview writes without changing files
- `--allow-tool <name>`: restrict execution to explicitly allowlisted tools
- `--workspace <path>`: choose a workspace directory

### Important behavior notes

- `write` requires either `--allow-write` or `--dry-run`.
- In interactive mode, `help` prints the supported commands and examples.
- In interactive mode, `history` shows all executed prompts in the current session.
- In interactive mode, `last` shows only the most recent executed prompt.
- In interactive mode, `clear` resets the current session history.
- `--show-trace` works in both one-shot and interactive mode.
- In default rule mode, prompts are simple command patterns rather than free-form goals.

List files:

```bash
python main.py "list files"
```

Read a file:

```bash
python main.py "read README.md"
```

Search text:

```bash
python main.py "search agent"
```

Preview a write without changing files:

```bash
python main.py "write notes.txt hello from MiniCodeAgent" --dry-run
```

Allow a real write:

```bash
python main.py "write notes.txt hello from MiniCodeAgent" --allow-write
```

Run restricted Python:

```bash
python main.py "python print(sum(range(5)))"
```

Write a trace file to a custom location:

```bash
python main.py "list files" --trace logs/trace.json
```

Restrict tools to a specific allowlist:

```bash
python main.py "read README.md" --allow-tool read_file
```

List registered tools and their argument schemas:

```bash
python main.py --list-tools
```

Print the execution trace after the final response:

```bash
python main.py "search agent" --show-trace
```

Start an interactive agent session:

```bash
python main.py --interactive
```

## Reproducible multi-step demonstrations

```bash
python -m examples.demo_flows
```

See [three real-tool scenarios](docs/DEMONSTRATIONS.md) for successful search/read,
missing-path recovery, and blocked Python execution. See the
[final architecture and claims audit](docs/FINAL_AUDIT.md) for evidence and limits.

## Demo

List workspace files:

```text
$ python main.py "list files"
README.md
main.py
minicodeagent/__init__.py
minicodeagent/agent.py
minicodeagent/cli.py
minicodeagent/permissions.py
minicodeagent/registry.py
minicodeagent/tools.py
minicodeagent/trace.py
pyproject.toml
tests/test_agent_flow.py
tests/test_permissions.py
tests/test_registry.py
tests/test_trace.py
```

Run a restricted Python snippet:

```text
$ python main.py "python print(sum(range(5)))"
10
```

Preview a write without changing files:

```text
$ python main.py "write notes.txt hello from MiniCodeAgent" --dry-run
Dry run: notes.txt
```

Block a write unless it is explicitly allowed:

```text
$ python main.py "write notes.txt hello"
error: write_file requires --allow-write or --dry-run before writing files
```

Inspect available tools:

```text
$ python main.py --list-tools
list_files: List files in the workspace.
  - path (string, optional, default="."): Directory or file path relative to the workspace.
read_file: Read a UTF-8 text file.
  - path (string, required): File path relative to the workspace.
run_python: Run restricted Python code.
  - code (string, required): Restricted Python code to execute.
search_text: Search text files for a query.
  - query (string, required): Case-insensitive text query.
  - path (string, optional, default="."): Directory or file path relative to the workspace.
write_file: Write a UTF-8 text file.
  - path (string, required): File path relative to the workspace.
  - content (string, required): Content to write to the file.
```

Run a multi-step session:

```text
$ python main.py --interactive
MiniCodeAgent interactive mode. Type 'exit' or 'quit' to leave.
MiniCodeAgent> list files
README.md
main.py
minicodeagent/__init__.py
...
MiniCodeAgent> read README.md
# MiniCodeAgent CLI
...
MiniCodeAgent> help
Available commands:
  list files
  list <path>
  ls
  read <path>
  search <query> [path]
  write <path> <content>
  python <code>
  help
  history
  last
  clear
  exit
  quit
MiniCodeAgent> last
Last session entry:
prompt: read README.md
tool: read_file
response: # MiniCodeAgent CLI
...
MiniCodeAgent> history
Session history:
1. prompt: list files
   tool: list_files
   response: README.md
...
2. prompt: read README.md
   tool: read_file
   response: # MiniCodeAgent CLI
...
MiniCodeAgent> clear
Session history cleared.
MiniCodeAgent> history
No session history yet.
MiniCodeAgent> exit
Session ended.
```

## Architecture

```text
main.py -> cli.py (one-shot and interactive share agent.run)
  -> initialize AgentState
  -> Planner.plan(state) -> AgentDecision
       FINAL -> return answer
       TOOL  -> validate registry schema -> check permissions -> execute
             -> append AgentStep with result or structured error
             -> plan again with updated state
  -> flush existing TraceLogger JSON events, including final status
```

Key modules:

- `state.py`: typed decisions, steps, errors, and run state. Tool calls,
  observations, errors, and step count are derived from steps to avoid duplicate state.
- `planner.py`: the `Planner` protocol and command-based `RuleBasedPlanner`.
- `llm_planner.py`: optional strict Chat Completions adapter and response validation.
- `agent.py`: orchestration, limits, permission-checked dispatch, and trace events.
- `cli.py`: command-line parsing, interactive history, and shared run wiring.
- `registry.py`: tool metadata, lookup, required/string argument validation,
  and rejection of unknown arguments.
- `tools.py` and `permissions.py`: existing implementations and permission controls.
- `trace.py`: existing timestamped JSON event logger.

### Planning and recovery

`Planner.plan(state: AgentState) -> AgentDecision` receives a snapshot containing
both the original goal and all prior observations/errors, plus registered tool
schemas. Inject a planner with `MiniCodeAgent(..., planner=my_planner)`; the default
is `RuleBasedPlanner`. The optional `LLMPlanner` implements the same protocol
without changing the execution loop. No external framework or runtime dependency
is required.

Existing commands keep their output format. If `read wrong/note.txt` fails with
`FileNotFoundError`, the default planner requests `list_files`, then reads the
unique file whose basename is `note.txt`. Zero or multiple matches produce a
failure answer. This uses the existing file listing tool; there is no new
`search_files` tool. Other failures are reported without an automatic retry.
These are explicit rules, not ReAct or general autonomous reasoning.

For example, if `docs/note.txt` exists:

```bash
python main.py "read wrong/note.txt" --max-steps 6 --show-trace
```

The tool sequence is `read_file` (error) -> `list_files` -> `read_file` (correct
path) -> FINAL. Tests also demonstrate injected deterministic planners selecting
`search_text` -> `read_file`, and a three-tool chain where each observation supplies
the next tool argument. The default command parser does not accept arbitrary
multi-action natural-language goals.

### Termination and errors

`agent.run()` returns a string; `agent.state` exposes the latest run. Each run starts
fresh, including in interactive mode. Every attempted tool call, including an
invalid or denied call, consumes one step. The planner gets one final decision
opportunity after the last permitted tool attempt; another TOOL decision stops
with `max_steps` without executing it. Identical tool/argument pairs are blocked
across the entire run, including successful calls, with `repeated_action` status.
This conservative policy can prevent an intentional reread after a file changes.

Tool exceptions become `{ "error": { "type": "...", "message": "..." } }`
observations and typed step errors. Planners may choose another permitted action.
FINAL decisions default to `final_status="completed"`; a planner reporting an
unresolved failure should set `final_status="failed"`. The CLI returns exit code 1
for failed or limited runs. Command parsing/planner exceptions are traced and
re-raised; existing command usage errors retain their CLI handling.

### Traces

The existing JSON array and `prompt`, `plan`, `observation`, `final` event names
remain. Agent events add a `run_id` so accumulated interactive traces can be grouped
by run. Plans record the decision, proposed step number, tool, arguments, and final
answer; observations record the executed step number, result, and error. Final
events record status, answer, and attempted step count. Plans stopped by a limit
have no corresponding observation because execution did not occur. Traces flush
on success, tool failures, limits, and planner exceptions.

## Optional LLM planner

Rule mode remains the default and requires neither a key nor network access. LLM
mode uses a standard-library HTTP client, so there is no extra package to install.
Set the environment variables in your shell (PowerShell example):

```powershell
$env:OPENAI_API_KEY = "<your-key>"
$env:MINICODEAGENT_MODEL = "<model-supported-by-your-provider>"
# Optional: $env:OPENAI_BASE_URL = "https://your-provider.example/v1"
python main.py "Find the release date and read the matching document" --planner llm --show-trace
```

Do not put real keys in the repository. Keys are read only from `OPENAI_API_KEY`,
never from a command-line key flag. `--model` overrides `MINICODEAGENT_MODEL`;
`--base-url` overrides `OPENAI_BASE_URL` (default `https://api.openai.com/v1`). The
adapter appends `/chat/completions`, so provide a base URL, not a full endpoint.
`--llm-timeout` defaults to 30 seconds. Both one-shot and interactive modes support
`--planner llm`. There is no automatic fallback to a different planner.

The provider/model must support Chat Completions `response_format` with strict
`json_schema`, including nested `anyOf`. Generic OpenAI compatibility is not enough.
The adapter uses an object wrapper with one decision; all schema properties are
required and extra properties are disallowed, following the
[official Structured Outputs constraints](https://developers.openai.com/api/docs/guides/structured-outputs).
Optional tool arguments must be filled using their documented defaults.

Example wire responses (no reasoning field):

```json
{"decision":{"type":"TOOL","tool_name":"read_file","arguments":{"path":"README.md"}}}
```

```json
{"decision":{"type":"FINAL","answer":"The document says ...","status":"completed"}}
```

FINAL `status` is `completed` or `failed`, mapping to the existing `AgentDecision`
outcome. The adapter validates decision shape locally; the registry then checks
tool names and arguments and the permission controller still gates execution.
A provider returning an unknown tool gets a structured error observation in the
normal agent loop. Invalid response envelopes, refusals, truncated output, or
network errors terminate the run with a safe error and trace. No automatic retries,
free-text parsing fallback, or private chain-of-thought requests are implemented.

The model receives only the goal, registered tool schemas, and prior steps
(actions, observations, errors), plus fixed decision instructions. Configuration,
headers, API keys, interactive history, and raw provider responses are not added
to traces or model context. Model-proposed actions are not trusted for permission
changes. The tool list describes registered capabilities, not permission grants.

Enabling LLM mode sends observed file content to the configured provider. Existing
traces also retain goal/tool/result content; this is not automatic sensitive-file
filtering or general secret redaction. Transport error messages omit raw server
bodies and credential-bearing URLs. HTTPS is required except for loopback HTTP;
redirects are rejected. A local server still requires a nonempty API-key value
(use its expected key or a placeholder if it does not authenticate).

Context is limited to 64 KiB before sending, and responses to 1 MiB while reading.
Oversized context fails explicitly instead of silently dropping observations.
The HTTP timeout is a socket-operation timeout, not a whole-run deadline. No token
budget, context compaction, streaming, provider retry policy, or live-provider
quality guarantee is provided. All API tests use mocks; no paid API was called.

## Testing

```bash
pytest
```

## Limitations

- Rule mode is deterministic; optional LLM behavior depends on the selected provider.
- Live-provider compatibility and task quality have not been tested in this change.
- `run_python` uses a restricted execution environment and simple pattern
  blocking. It is suitable for a demo, not for running untrusted code securely.
- Tools are synchronous and local-only; the optional planner makes HTTP requests.
- File reads and writes are limited to the selected workspace.
- Write confirmation is represented by explicit CLI flags instead of an
  interactive prompt.

## Future Improvements

- Evaluate live-provider compatibility and task success on a reproducible task set.
- Add interactive confirmation prompts for writes.
- Stream trace events during long-running tasks.
- Extend schema validation beyond the current built-in string arguments.
- Add a package release workflow (pytest CI already exists).
