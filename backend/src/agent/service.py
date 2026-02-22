from __future__ import annotations

import json
from datetime import datetime, timezone
from datetime import timedelta
from typing import Callable, Protocol
from uuid import uuid4

from .llm import LLMClient, build_default_llm_client
from .models import AgentResponse, Command, InboundEvent, Intent, PendingConfirmation, Reminder
from .parser import CommandParser


class ReminderStore(Protocol):
    def create(self, *, platform: str, channel_id: str, user_id: str, remind_at: datetime, message: str) -> Reminder:
        ...

    def get(self, reminder_id: str) -> Reminder | None:
        ...


class AuditLogStore(Protocol):
    def log(self, *, action: str, status: str, platform: str | None, user_id: str | None, details: str) -> str:
        ...


class InMemoryReminderStore:
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


class InMemoryAuditLogStore:
    def __init__(self) -> None:
        self._logs: list[dict[str, str | None]] = []

    def log(self, *, action: str, status: str, platform: str | None, user_id: str | None, details: str) -> str:
        log_id = str(uuid4())
        self._logs.append(
            {
                "log_id": log_id,
                "action": action,
                "status": status,
                "platform": platform,
                "user_id": user_id,
                "details": details,
            }
        )
        return log_id

    @property
    def logs(self) -> list[dict[str, str | None]]:
        return self._logs


class AgentService:
    def __init__(
        self,
        parser: CommandParser | None = None,
        reminder_store: ReminderStore | None = None,
        audit_store: AuditLogStore | None = None,
        llm_client: LLMClient | None = None,
        confirmation_ttl: timedelta = timedelta(minutes=5),
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.parser = parser or CommandParser()
        self.reminder_store = reminder_store or InMemoryReminderStore()
        self.audit_store = audit_store or InMemoryAuditLogStore()
        self.llm_client = llm_client or build_default_llm_client()
        self.pending_confirmations: dict[str, PendingConfirmation] = {}
        self.confirmation_ttl = confirmation_ttl
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def handle_message(self, event: InboundEvent) -> AgentResponse:
        command = self.parser.parse(event.text)

        if command.intent is Intent.ASK:
            question = command.payload.get("question") or "質問が空です。"
            answer = self.llm_client.answer(str(question))
            response = AgentResponse(text=answer, status="completed")
            self._log(action="handle_message", event=event, command=command, response=response)
            return response

        if command.intent is Intent.SUMMARIZE:
            response = AgentResponse(text="要約ジョブを開始しました。", status="queued")
            self._log(action="handle_message", event=event, command=command, response=response)
            return response

        if command.intent is Intent.REMIND:
            response = self._handle_remind(event, command)
            self._log(action="handle_message", event=event, command=command, response=response)
            return response

        response = AgentResponse(
            text="コマンドを解釈できませんでした。`/ask` `/summarize` `/remind` を使用してください。",
            status="unsupported",
        )
        self._log(action="handle_message", event=event, command=command, response=response)
        return response

    def confirm_action(self, confirmation_id: str, approved: bool) -> AgentResponse:
        pending = self.pending_confirmations.pop(confirmation_id, None)
        if pending is None:
            response = AgentResponse(text="確認対象が見つかりません。", status="not_found")
            self._log(action="confirm_action", event=None, command=None, response=response, extra={"confirmation_id": confirmation_id})
            return response

        if self.now_provider() > pending.expires_at:
            response = AgentResponse(text="確認期限が切れています。再実行してください。", status="expired")
            self._log(action="confirm_action", event=None, command=pending.command, response=response, extra={"confirmation_id": confirmation_id})
            return response

        if not approved:
            response = AgentResponse(text="操作をキャンセルしました。", status="cancelled")
            self._log(action="confirm_action", event=None, command=pending.command, response=response, extra={"confirmation_id": confirmation_id})
            return response

        payload = pending.command.payload
        reminder = self.reminder_store.create(
            platform=str(payload["platform"]),
            channel_id=str(payload["channel_id"]),
            user_id=str(payload["user_id"]),
            remind_at=payload["remind_at"],
            message=str(payload["message"]),
        )
        response = AgentResponse(
            text=f"リマインドを登録しました: {reminder.reminder_id}",
            status="completed",
            metadata={"reminder_id": reminder.reminder_id},
        )
        self._log(action="confirm_action", event=None, command=pending.command, response=response, extra={"confirmation_id": confirmation_id})
        return response

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
            now = self.now_provider()
            self.pending_confirmations[confirmation_id] = PendingConfirmation(
                confirmation_id=confirmation_id,
                command=Command(
                    intent=Intent.REMIND,
                    payload={
                        "platform": event.platform,
                        "channel_id": event.channel_id,
                        "user_id": event.user_id,
                        "remind_at": remind_at,
                        "message": message,
                    },
                ),
                created_at=now,
                expires_at=now + self.confirmation_ttl,
            )
            return AgentResponse(
                text="この操作は確認が必要です。5分以内に confirm_action を実行してください。",
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

    def _log(
        self,
        *,
        action: str,
        event: InboundEvent | None,
        command: Command | None,
        response: AgentResponse,
        extra: dict[str, str] | None = None,
    ) -> None:
        details = {
            "intent": command.intent.value if command else None,
            "status": response.status,
            "metadata": response.metadata,
        }
        if extra:
            details.update(extra)
        self.audit_store.log(
            action=action,
            status=response.status,
            platform=event.platform if event else None,
            user_id=event.user_id if event else None,
            details=json.dumps(details, ensure_ascii=False),
        )
