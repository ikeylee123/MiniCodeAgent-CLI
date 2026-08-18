import pytest

from minicodeagent.registry import Tool, ToolRegistry


def test_registry_registers_and_lists_tools():
    registry = ToolRegistry()
    registry.register(Tool("demo", "Demo tool", lambda: "ok", {}))

    assert registry.names() == ["demo"]
    assert registry.get("demo").handler() == "ok"


def test_registry_rejects_duplicates():
    registry = ToolRegistry()
    registry.register(Tool("demo", "Demo tool", lambda: "ok", {}))

    with pytest.raises(ValueError):
        registry.register(Tool("demo", "Duplicate", lambda: "nope", {}))


def test_registry_exposes_schemas():
    registry = ToolRegistry()
    registry.register(
        Tool(
            "read_file",
            "Read file",
            lambda path: path,
            {"path": {"type": "string", "required": True}},
        )
    )

    assert registry.schemas()["read_file"]["path"]["required"] is True
