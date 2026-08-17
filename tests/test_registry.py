import pytest

from minicodeagent.registry import Tool, ToolRegistry


def test_registry_registers_and_lists_tools():
    registry = ToolRegistry()
    registry.register(Tool("demo", "Demo tool", lambda: "ok"))

    assert registry.names() == ["demo"]
    assert registry.get("demo").handler() == "ok"


def test_registry_rejects_duplicates():
    registry = ToolRegistry()
    registry.register(Tool("demo", "Demo tool", lambda: "ok"))

    with pytest.raises(ValueError):
        registry.register(Tool("demo", "Duplicate", lambda: "nope"))
