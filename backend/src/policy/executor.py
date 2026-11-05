"""policy の入口。登録簿からツールを取り出して実行できるのは、この層だけである。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping
from uuid import uuid4

from tools.base import ToolKind
from tools.registry import ToolRegistry


class IntentOrigin(str, Enum):
    """意図の出どころ。

    今は、依頼者の文を意図分類が読み取ったものだけがある。12 月のインジェクション分離では、
    ここに出どころを足し、出どころごとの規則を入口に足す。ツールの I/F は変えない。
    """

    REQUESTER = "requester"


@dataclass(frozen=True)
class ToolIntent:
    """意図。使うツールとその入力。"""

    tool: str
    input: Mapping[str, Any]


class WriteNotSupportedError(RuntimeError):
    """書き込みツールは承認待ちを通す必要があるが、その経路はまだない（#10）。"""


class ToolExecutor:
    def __init__(self, registry: ToolRegistry) -> None:
        self._registry = registry

    def handle(self, intent: ToolIntent, *, origin: IntentOrigin) -> dict[str, Any]:
        """意図を処理する。読み取りなら承認なしでそのまま実行し、結果を返す。"""
        tool = self._registry.get(intent.tool)
        if tool.kind is not ToolKind.READ:
            raise WriteNotSupportedError(f"write tool requires approval: {tool.name}")
        return tool.execute(intent.input, idempotency_key=str(uuid4()))
