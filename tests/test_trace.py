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
