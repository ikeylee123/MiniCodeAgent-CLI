from minicodeagent.agent import MiniCodeAgent
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
