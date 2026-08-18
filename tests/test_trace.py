import json

from minicodeagent.trace import TraceLogger


def test_trace_logger_writes_json(tmp_path):
    trace_path = tmp_path / "trace.json"
    logger = TraceLogger(trace_path)

    logger.record("plan", tool="list_files")
    logger.flush()

    payload = json.loads(trace_path.read_text(encoding="utf-8"))
    assert payload[0]["type"] == "plan"
    assert payload[0]["data"] == {"tool": "list_files"}
    assert "timestamp" in payload[0]


def test_trace_logger_formats_json_without_writing():
    logger = TraceLogger()

    logger.record("final", text="done")

    assert '"type": "final"' in logger.to_json()
    assert '"text": "done"' in logger.to_json()
