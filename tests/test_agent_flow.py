from minicodeagent.agent import MiniCodeAgent
from minicodeagent.cli import run_interactive
from minicodeagent.permissions import PermissionConfig, PermissionController
from minicodeagent.tools import build_registry
from minicodeagent.trace import TraceLogger


def make_agent(tmp_path, dry_run=False):
    return MiniCodeAgent(
        workspace=tmp_path,
        registry=build_registry(),
        permissions=PermissionController(
            PermissionConfig(
                allowed_tools={
                    "list_files",
                    "read_file",
                    "write_file",
                    "search_text",
                    "run_python",
                },
                allow_write=False,
                dry_run=dry_run,
            )
        ),
        trace=TraceLogger(tmp_path / "trace.json"),
    )


def test_agent_reads_file(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
    agent = make_agent(tmp_path)

    assert agent.run("read hello.txt") == "hello world"


def test_agent_searches_text(tmp_path):
    (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
    agent = make_agent(tmp_path)

    assert agent.run("search world") == "hello.txt:1: hello world"


def test_agent_write_dry_run(tmp_path):
    agent = make_agent(tmp_path, dry_run=True)

    assert agent.run("write note.txt hello") == "Dry run: note.txt"
    assert not (tmp_path / "note.txt").exists()


def test_agent_search_skips_git_directory(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("secret agent", encoding="utf-8")
    (tmp_path / "visible.txt").write_text("public agent", encoding="utf-8")
    agent = make_agent(tmp_path)

    assert agent.run("search agent") == "visible.txt:1: public agent"


def test_agent_reports_missing_read_path(tmp_path):
    agent = make_agent(tmp_path)

    try:
        agent.run("read")
    except ValueError as exc:
        assert str(exc) == "Usage: read <path>"
    else:
        raise AssertionError("Expected a usage error for missing read path")


def test_agent_reports_unknown_command(tmp_path):
    agent = make_agent(tmp_path)

    try:
        agent.run("summarize repo")
    except ValueError as exc:
        assert "Unknown command." in str(exc)
    else:
        raise AssertionError("Expected an error for unknown commands")


def test_interactive_session_runs_multiple_commands(tmp_path, capsys):
    (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
    prompts = iter(["read hello.txt", "search world", "exit"])
    agent = make_agent(tmp_path)

    result = run_interactive(agent, input_fn=lambda _: next(prompts))

    captured = capsys.readouterr().out
    assert result == 0
    assert "MiniCodeAgent interactive mode." in captured
    assert "hello world" in captured
    assert "hello.txt:1: hello world" in captured
    assert "Session ended." in captured


def test_interactive_session_handles_permission_errors(tmp_path, capsys):
    prompts = iter(["write note.txt hello", "quit"])
    agent = make_agent(tmp_path)

    result = run_interactive(agent, input_fn=lambda _: next(prompts))

    captured = capsys.readouterr().out
    assert result == 0
    assert "error: write_file requires --allow-write or --dry-run before writing files" in captured


def test_interactive_session_shows_help_and_usage_error(tmp_path, capsys):
    prompts = iter(["help", "write note.txt", "quit"])
    agent = make_agent(tmp_path)

    result = run_interactive(agent, input_fn=lambda _: next(prompts))

    captured = capsys.readouterr().out
    assert result == 0
    assert "Available commands:" in captured
    assert "search <query> [path]" in captured
    assert "error: Usage: write <path> <content>" in captured


def test_interactive_session_history_and_last(tmp_path, capsys):
    (tmp_path / "hello.txt").write_text("hello world", encoding="utf-8")
    prompts = iter(["read hello.txt", "search world", "last", "history", "quit"])
    agent = make_agent(tmp_path)

    result = run_interactive(agent, input_fn=lambda _: next(prompts))

    captured = capsys.readouterr().out
    assert result == 0
    assert "Last session entry:" in captured
    assert "prompt: search world" in captured
    assert "tool: search_text" in captured
    assert "Session history:" in captured
    assert "1. prompt: read hello.txt" in captured
    assert "2. prompt: search world" in captured


def test_interactive_session_history_and_last_when_empty(tmp_path, capsys):
    prompts = iter(["last", "history", "quit"])
    agent = make_agent(tmp_path)

    result = run_interactive(agent, input_fn=lambda _: next(prompts))

    captured = capsys.readouterr().out
    assert result == 0
    assert "No previous session entry yet." in captured
    assert "No session history yet." in captured
