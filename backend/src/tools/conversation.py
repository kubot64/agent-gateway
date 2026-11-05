from __future__ import annotations

from typing import Any, Mapping, Protocol, Sequence

from tools.base import InputField, InputSchema, PlannedExecution, ToolKind


class ConversationLine(Protocol):
    user_id: str
    text: str


class ConversationReader(Protocol):
    def recent(
        self,
        *,
        platform: str,
        workspace_id: str,
        channel_id: str,
        thread_id: str | None,
        limit: int = 20,
    ) -> Sequence[ConversationLine]:
        ...


class ConversationSummarizeTool:
    """conversation.summarize: 要約の対象になる会話を読む読み取りツール。

    要約の文を作る LLM 呼び出しは app 層が受け持つ。このツールは会話履歴を読んで返すだけにする。
    """

    name = "conversation.summarize"
    kind = ToolKind.READ
    input_schema = InputSchema(
        fields={
            "platform": InputField(str),
            "workspace_id": InputField(str),
            "channel_id": InputField(str),
            "thread_id": InputField((str, type(None))),
            "limit": InputField(int),
        }
    )

    def __init__(self, conversations: ConversationReader) -> None:
        self._conversations = conversations

    def dry_run(self, tool_input: Mapping[str, Any]) -> PlannedExecution:
        self.input_schema.validate(tool_input)
        where = "スレッド" if tool_input["thread_id"] is not None else "チャンネル"
        return PlannedExecution(
            tool=self.name,
            input=dict(tool_input),
            description=f"{where}の会話を読んで要約する",
        )

    def execute(self, tool_input: Mapping[str, Any], *, idempotency_key: str) -> dict[str, Any]:
        self.input_schema.validate(tool_input)
        lines = self._conversations.recent(
            platform=str(tool_input["platform"]),
            workspace_id=str(tool_input["workspace_id"]),
            channel_id=str(tool_input["channel_id"]),
            thread_id=tool_input["thread_id"],
            limit=int(tool_input["limit"]),
        )
        return {"messages": [{"user_id": line.user_id, "text": line.text} for line in lines]}
