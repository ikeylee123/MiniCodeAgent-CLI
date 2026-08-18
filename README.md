# MiniCodeAgent CLI

[![Tests](https://github.com/ikeylee123/MiniCodeAgent-CLI/actions/workflows/tests.yml/badge.svg)](https://github.com/ikeylee123/MiniCodeAgent-CLI/actions/workflows/tests.yml)

MiniCodeAgent CLI is a lightweight tool-calling coding agent prototype. It is
designed as a small resume project that demonstrates an agent loop, a modular
tool registry, permission checks, execution tracing, and pytest coverage without
requiring an LLM API key.

## Features

- Command-line entry point through `main.py` or the `minicodeagent` script.
- Rule-based agent loop with planning, tool selection, tool execution,
  observation handling, and final response generation.
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
- Pytest tests for registry behavior, permissions, trace logging, and simple
  agent flow.

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
- Prompt commands are parsed by the rule-based planner into tool calls.

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

The planner currently recognizes these patterns:

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

- `--interactive`: start a multi-step session
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
- Prompt commands are simple rule-based patterns, not free-form natural language understanding.

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
main.py
  -> minicodeagent.cli
      -> MiniCodeAgent
          -> plan prompt into a ToolCall
          -> check permissions
          -> execute selected tool from ToolRegistry
          -> record observation
          -> generate final response
      -> TraceLogger writes JSON events
```

Key modules:

- `minicodeagent/agent.py`: agent loop and rule-based planner.
- `minicodeagent/cli.py`: command-line argument parsing and wiring.
- `minicodeagent/registry.py`: tool metadata and lookup.
- `minicodeagent/tools.py`: built-in tool implementations.
- `minicodeagent/permissions.py`: allowlist, write, and unsafe code checks.
- `minicodeagent/trace.py`: JSON trace events.

Each registered tool carries a lightweight schema describing expected arguments,
required fields, defaults, and human-readable descriptions. This keeps the
rule-based planner simple while leaving a clear path to an LLM planner later.

## Testing

```bash
pytest
```

## Limitations

- The planner is rule-based, not LLM-powered.
- `run_python` uses a restricted execution environment and simple pattern
  blocking. It is suitable for a demo, not for running untrusted code securely.
- Tools are synchronous and local-only.
- File reads and writes are limited to the selected workspace.
- Write confirmation is represented by explicit CLI flags instead of an
  interactive prompt.

## Future Improvements

- Add an LLM planner behind the existing `ToolCall` interface.
- Add interactive confirmation prompts for writes.
- Stream trace events during long-running tasks.
- Add richer tool schemas and argument validation.
- Package release workflow and CI.
