"""ツールの I/F。

ツールは、ボットが依頼者の代わりに行う操作 1 種類である。各ツールは名前、入力スキーマ、
読み取りか書き込みかの宣言、dry_run、execute を持つ。execute は idempotency key を受け取る。
policy 層はこの宣言だけを見て、すぐ実行するか承認待ちを作るかを決める。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Protocol


class ToolKind(str, Enum):
    READ = "read"
    WRITE = "write"


class ToolInputError(ValueError):
    """ツールの入力が入力スキーマに合わない。"""


@dataclass(frozen=True)
class InputField:
    type: type | tuple[type, ...]
    required: bool = True


@dataclass(frozen=True)
class InputSchema:
    fields: Mapping[str, InputField] = field(default_factory=dict)

    def validate(self, tool_input: Mapping[str, Any]) -> None:
        unknown = sorted(set(tool_input) - set(self.fields))
        if unknown:
            raise ToolInputError(f"unknown input fields: {', '.join(unknown)}")
        for name, spec in self.fields.items():
            if name not in tool_input:
                if spec.required:
                    raise ToolInputError(f"missing input field: {name}")
                continue
            if not isinstance(tool_input[name], spec.type):
                raise ToolInputError(f"invalid type for input field: {name}")


@dataclass(frozen=True)
class PlannedExecution:
    """実行内容。実行するツール、その入力、人が読める説明の 3 つからなる。"""

    tool: str
    input: Mapping[str, Any]
    description: str


class Tool(Protocol):
    name: str
    kind: ToolKind
    input_schema: InputSchema

    def dry_run(self, tool_input: Mapping[str, Any]) -> PlannedExecution:
        """入力を検証し、実行内容を返す。外部呼び出しも DB 書き込みもしない。"""
        ...

    def execute(self, tool_input: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        """ツールを実行し、JSON にできる結果を返す。"""
        ...
