from __future__ import annotations

from datetime import datetime, timezone
from uuid import uuid4

from .models import AgentResponse, Command, InboundEvent, Intent, Reminder
from .parser import CommandParser


class ReminderStore:
    """In-memory store for kickoff implementation."""

    def __init__(self) -> None:
        self._items: dict[str, Reminder] = {}

    def create(self, *, platform: str, channel_id: str, user_id: str, remind_at: datetime, message: str) -> Reminder:
        reminder = Reminder(
            reminder_id=str(uuid4()),
            platform=platform,
            channel_id=channel_id,
            user_id=user_id,
            remind_at=remind_at,
            message=message,
        )
        self._items[reminder.reminder_id] = reminder
        return reminder

    def get(self, reminder_id: str) -> Reminder | None:
        return self._items.get(reminder_id)


class AgentService:
    def __init__(self, parser: CommandParser | None = None, reminder_store: ReminderStore | None = None) -> None:
        self.parser = parser or CommandParser()
        self.reminder_store = reminder_store or ReminderStore()
        self.pending_confirmations: dict[str, Command] = {}

    def handle_message(self, event: InboundEvent) -> AgentResponse:
        command = self.parser.parse(event.text)

        if command.intent is Intent.ASK:
            question = command.payload.get("question") or "質問が空です。"
            return AgentResponse(text=f"受け付けました。回答を準備します: {question}", status="accepted")

        if command.intent is Intent.SUMMARIZE:
            return AgentResponse(text="要約ジョブを開始しました。", status="queued")

        if command.intent is Intent.REMIND:
            return self._handle_remind(event, command)

        return AgentResponse(
            text="コマンドを解釈できませんでした。`/ask` `/summarize` `/remind` を使用してください。",
            status="unsupported",
        )

    def confirm_action(self, confirmation_id: str, approved: bool) -> AgentResponse:
        command = self.pending_confirmations.pop(confirmation_id, None)
        if command is None:
            return AgentResponse(text="確認対象が見つかりません。", status="not_found")

        if not approved:
            return AgentResponse(text="操作をキャンセルしました。", status="cancelled")

        payload = command.payload
        reminder = self.reminder_store.create(
            platform=str(payload["platform"]),
            channel_id=str(payload["channel_id"]),
            user_id=str(payload["user_id"]),
            remind_at=payload["remind_at"],
            message=str(payload["message"]),
        )
        return AgentResponse(
            text=f"リマインドを登録しました: {reminder.reminder_id}",
            status="completed",
            metadata={"reminder_id": reminder.reminder_id},
        )

    def _handle_remind(self, event: InboundEvent, command: Command) -> AgentResponse:
        remind_at = command.payload.get("remind_at")
        message = command.payload.get("message", "")

        if not isinstance(remind_at, datetime):
            example = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            return AgentResponse(
                text=f"日時形式が不正です。例: /remind {example} レポート提出",
                status="validation_error",
            )

        if command.requires_confirmation:
            confirmation_id = str(uuid4())
            self.pending_confirmations[confirmation_id] = Command(
                intent=Intent.REMIND,
                payload={
                    "platform": event.platform,
                    "channel_id": event.channel_id,
                    "user_id": event.user_id,
                    "remind_at": remind_at,
                    "message": message,
                },
            )
            return AgentResponse(
                text="この操作は確認が必要です。confirm_action を実行してください。",
                status="needs_confirmation",
                metadata={"confirmation_id": confirmation_id},
            )

        reminder = self.reminder_store.create(
            platform=event.platform,
            channel_id=event.channel_id,
            user_id=event.user_id,
            remind_at=remind_at,
            message=str(message),
        )
        return AgentResponse(
            text=f"リマインドを登録しました: {reminder.reminder_id}",
            status="completed",
            metadata={"reminder_id": reminder.reminder_id},
        )
