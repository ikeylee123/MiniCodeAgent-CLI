from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


ToolHandler = Callable[..., Any]


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    handler: ToolHandler
    schema: dict[str, Any]
    mutates_files: bool = False


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise KeyError(f"Unknown tool: {name}") from exc

    def names(self) -> list[str]:
        return sorted(self._tools)

    def descriptions(self) -> dict[str, str]:
        return {name: tool.description for name, tool in sorted(self._tools.items())}

    def schemas(self) -> dict[str, dict[str, Any]]:
        return {name: tool.schema for name, tool in sorted(self._tools.items())}

    def validate(self, name: str, args: dict[str, Any]) -> None:
        schema = self.get(name).schema
        if not isinstance(args, dict):
            raise ValueError("Tool arguments must be a dictionary")
        unknown = args.keys() - schema.keys()
        if unknown:
            raise ValueError(f"Unexpected arguments for {name}: {sorted(unknown)}")
        for key, spec in schema.items():
            if spec.get("required") and key not in args:
                raise ValueError(f"Missing required argument: {key}")
            if key in args and spec.get("type") == "string" and not isinstance(args[key], str):
                raise ValueError(f"Argument {key} must be a string")
