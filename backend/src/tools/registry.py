from __future__ import annotations

from typing import Iterable

from tools.base import Tool


class UnknownToolError(LookupError):
    pass


class ToolRegistry:
    """登録簿。名前からツールを引く。ツールを取り出して実行するのは policy 層だけである。"""

    def __init__(self, tools: Iterable[Tool] = ()) -> None:
        self._tools: dict[str, Tool] = {}
        for tool in tools:
            self.register(tool)

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool:
        try:
            return self._tools[name]
        except KeyError:
            raise UnknownToolError(f"unknown tool: {name}") from None
