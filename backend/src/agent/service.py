from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from datetime import timedelta
from typing import Callable, Protocol
from uuid import uuid4

from .llm import LLMClient, build_default_llm_client
from .models import AgentResponse, Command, ConversationMessage, InboundEvent, Intent, PendingConfirmation, Reminder
from .parser import CommandParser
from .search import DocsSearchClient, SearchResult, build_default_search_client
from .task import TaskClient, TaskClientError, build_default_task_client


class ReminderStore(Protocol):
    def create(self, *, platform: str, channel_id: str, user_id: str, remind_at: datetime, message: str) -> Reminder:
        ...

    def get(self, reminder_id: str) -> Reminder | None:
        ...


class AuditLogStore(Protocol):
    def log(self, *, action: str, status: str, platform: str | None, user_id: str | None, details: str) -> str:
        ...


class MetricStore(Protocol):
    def record(self, *, platform: str | None, intent: str | None, status: str, duration_ms: int) -> str:
        ...

    def summary(self) -> dict[str, object]:
        ...


class ConversationStore(Protocol):
    def append(self, message: ConversationMessage) -> None:
        ...

    def recent(
        self,
        *,
        platform: str,
        workspace_id: str,
        channel_id: str,
        thread_id: str | None,
        limit: int = 20,
    ) -> list[ConversationMessage]:
        ...


class UserPreferenceStore(Protocol):
    def get_mode(self, *, platform: str, workspace_id: str, user_id: str) -> str:
        ...

    def set_mode(self, *, platform: str, workspace_id: str, user_id: str, mode: str) -> None:
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


class InMemoryMetricStore:
    def __init__(self) -> None:
        self._records: list[dict[str, str | int | None]] = []

    def record(self, *, platform: str | None, intent: str | None, status: str, duration_ms: int) -> str:
        metric_id = str(uuid4())
        self._records.append(
            {
                "metric_id": metric_id,
                "platform": platform,
                "intent": intent,
                "status": status,
                "duration_ms": duration_ms,
            }
        )
        return metric_id

    @property
    def records(self) -> list[dict[str, str | int | None]]:
        return self._records

    def summary(self) -> dict[str, object]:
        total = len(self._records)
        error_statuses = {"integration_error", "validation_error", "failed", "unauthorized"}
        errors = len([r for r in self._records if str(r.get("status")) in error_statuses])
        durations = [int(r["duration_ms"]) for r in self._records if isinstance(r.get("duration_ms"), int)]
        avg_duration = int(sum(durations) / len(durations)) if durations else 0
        p95 = 0
        if durations:
            sorted_durations = sorted(durations)
            idx = max(0, int(len(sorted_durations) * 0.95) - 1)
            p95 = sorted_durations[idx]
        by_intent: dict[str, int] = {}
        by_status: dict[str, int] = {}
        for row in self._records:
            intent = str(row.get("intent") or "unknown")
            status = str(row.get("status") or "unknown")
            by_intent[intent] = by_intent.get(intent, 0) + 1
            by_status[status] = by_status.get(status, 0) + 1
        return {
            "total": total,
            "errors": errors,
            "avg_duration_ms": avg_duration,
            "p95_duration_ms": p95,
            "by_intent": by_intent,
            "by_status": by_status,
        }


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._messages: list[ConversationMessage] = []

    def append(self, message: ConversationMessage) -> None:
        self._messages.append(message)

    def recent(
        self,
        *,
        platform: str,
        workspace_id: str,
        channel_id: str,
        thread_id: str | None,
        limit: int = 20,
    ) -> list[ConversationMessage]:
        filtered = [
            m
            for m in self._messages
            if m.platform == platform and m.workspace_id == workspace_id and m.channel_id == channel_id
        ]
        if thread_id is not None:
            filtered = [m for m in filtered if m.thread_id == thread_id]
        filtered.sort(key=lambda x: x.ts)
        return filtered[-limit:]


class InMemoryUserPreferenceStore:
    def __init__(self) -> None:
        self._modes: dict[tuple[str, str, str], str] = {}

    def get_mode(self, *, platform: str, workspace_id: str, user_id: str) -> str:
        return self._modes.get((platform, workspace_id, user_id), "brief")

    def set_mode(self, *, platform: str, workspace_id: str, user_id: str, mode: str) -> None:
        self._modes[(platform, workspace_id, user_id)] = mode


class AgentService:
    def __init__(
        self,
        parser: CommandParser | None = None,
        reminder_store: ReminderStore | None = None,
        audit_store: AuditLogStore | None = None,
        metric_store: MetricStore | None = None,
        conversation_store: ConversationStore | None = None,
        preference_store: UserPreferenceStore | None = None,
        llm_client: LLMClient | None = None,
        search_client: DocsSearchClient | None = None,
        task_client: TaskClient | None = None,
        confirmation_ttl: timedelta = timedelta(minutes=5),
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self.parser = parser or CommandParser()
        self.reminder_store = reminder_store or InMemoryReminderStore()
        self.audit_store = audit_store or InMemoryAuditLogStore()
        self.metric_store = metric_store or InMemoryMetricStore()
        self.conversation_store = conversation_store or InMemoryConversationStore()
        self.preference_store = preference_store or InMemoryUserPreferenceStore()
        self.llm_client = llm_client or build_default_llm_client()
        self.search_client = search_client or build_default_search_client()
        self.task_client = task_client or build_default_task_client()
        self.pending_confirmations: dict[str, PendingConfirmation] = {}
        self.confirmation_ttl = confirmation_ttl
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    def handle_message(self, event: InboundEvent, *, request_id: str | None = None) -> AgentResponse:
        start = time.perf_counter()
        command = self.parser.parse(event.text)
        self._store_conversation(event, command)

        if command.intent is Intent.ASK:
            question = command.payload.get("question") or "質問が空です。"
            context_results = self.search_client.search(str(question), limit=3)
            llm_input = _build_ask_prompt(str(question), context_results)
            answer = self.llm_client.answer(llm_input)
            answer = self._apply_mode(event=event, text=answer)
            metadata: dict[str, object] = {}
            if context_results:
                metadata["sources"] = [r.path for r in context_results]
            response = AgentResponse(text=answer, status="completed", metadata=metadata)
            self._log(action="handle_message", event=event, command=command, response=response, request_id=request_id)
            self._record_metric(event=event, command=command, response=response, duration_ms=_duration_ms(start))
            return response

        if command.intent is Intent.SUMMARIZE:
            response = self._handle_summarize(event, command)
            self._log(action="handle_message", event=event, command=command, response=response, request_id=request_id)
            self._record_metric(event=event, command=command, response=response, duration_ms=_duration_ms(start))
            return response

        if command.intent is Intent.REMIND:
            response = self._handle_remind(event, command)
            self._log(action="handle_message", event=event, command=command, response=response, request_id=request_id)
            self._record_metric(event=event, command=command, response=response, duration_ms=_duration_ms(start))
            return response

        if command.intent is Intent.TASK:
            response = self._handle_task(event, command)
            self._log(action="handle_message", event=event, command=command, response=response, request_id=request_id)
            self._record_metric(event=event, command=command, response=response, duration_ms=_duration_ms(start))
            return response

        if command.intent is Intent.MODE:
            response = self._handle_mode(event, command)
            self._log(action="handle_message", event=event, command=command, response=response, request_id=request_id)
            self._record_metric(event=event, command=command, response=response, duration_ms=_duration_ms(start))
            return response

        response = AgentResponse(
            text="コマンドを解釈できませんでした。`/ask` `/summarize` `/remind` `/task` `/mode` を使用してください。",
            status="unsupported",
        )
        self._log(action="handle_message", event=event, command=command, response=response, request_id=request_id)
        self._record_metric(event=event, command=command, response=response, duration_ms=_duration_ms(start))
        return response

    def confirm_action(self, confirmation_id: str, approved: bool, *, request_id: str | None = None) -> AgentResponse:
        start = time.perf_counter()
        pending = self.pending_confirmations.pop(confirmation_id, None)
        if pending is None:
            response = AgentResponse(text="確認対象が見つかりません。", status="not_found")
            self._log(
                action="confirm_action",
                event=None,
                command=None,
                response=response,
                extra={"confirmation_id": confirmation_id},
                request_id=request_id,
            )
            self._record_metric(event=None, command=None, response=response, duration_ms=_duration_ms(start))
            return response

        if self.now_provider() > pending.expires_at:
            response = AgentResponse(text="確認期限が切れています。再実行してください。", status="expired")
            self._log(
                action="confirm_action",
                event=None,
                command=pending.command,
                response=response,
                extra={"confirmation_id": confirmation_id},
                request_id=request_id,
            )
            self._record_metric(event=None, command=pending.command, response=response, duration_ms=_duration_ms(start))
            return response

        if not approved:
            response = AgentResponse(text="操作をキャンセルしました。", status="cancelled")
            self._log(
                action="confirm_action",
                event=None,
                command=pending.command,
                response=response,
                extra={"confirmation_id": confirmation_id},
                request_id=request_id,
            )
            self._record_metric(event=None, command=pending.command, response=response, duration_ms=_duration_ms(start))
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
        self._log(
            action="confirm_action",
            event=None,
            command=pending.command,
            response=response,
            extra={"confirmation_id": confirmation_id},
            request_id=request_id,
        )
        self._record_metric(event=None, command=pending.command, response=response, duration_ms=_duration_ms(start))
        return response

    def _handle_remind(self, event: InboundEvent, command: Command) -> AgentResponse:
        remind_at = command.payload.get("remind_at")
        message = command.payload.get("message", "")

        if not isinstance(remind_at, datetime):
            example = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
            return AgentResponse(
                text=f"日時形式が不正です。例: /remind {example} レポート提出",
                status="validation_error",
                metadata={
                    "error_type": "validation_error",
                    "error_message": "invalid remind datetime format",
                },
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

    def _handle_task(self, event: InboundEvent, command: Command) -> AgentResponse:
        action = str(command.payload.get("action", "")).lower()
        if action != "add":
            return AgentResponse(
                text="形式が不正です。例: /task add バグ調査",
                status="validation_error",
                metadata={
                    "error_type": "validation_error",
                    "error_message": "invalid task command format",
                },
            )

        title = str(command.payload.get("title", "")).strip()
        if not title:
            return AgentResponse(
                text="タスク内容が空です。例: /task add バグ調査",
                status="validation_error",
                metadata={
                    "error_type": "validation_error",
                    "error_message": "empty task title",
                },
            )

        try:
            result = self.task_client.create_task(
                title=title,
                description=f"Created by {event.platform}:{event.user_id}",
                creator_id=event.user_id,
            )
        except TaskClientError as exc:
            return AgentResponse(
                text=f"タスク登録に失敗しました: {exc}",
                status="integration_error",
                metadata={
                    "error_type": "integration_error",
                    "error_message": str(exc),
                },
            )

        task_id = str(result.get("task_id", "")).strip() or "unknown"
        metadata = {
            "provider": str(result.get("provider", "unknown")),
            "task_id": task_id,
        }
        url = str(result.get("url", "")).strip()
        if url:
            metadata["url"] = url
        return AgentResponse(
            text=f"タスクを登録しました: {task_id}",
            status="completed",
            metadata=metadata,
        )

    def _handle_summarize(self, event: InboundEvent, command: Command) -> AgentResponse:
        target = str(command.payload.get("target", "current_thread"))
        use_thread = target == "current_thread"
        messages = self.conversation_store.recent(
            platform=event.platform,
            workspace_id=event.workspace_id,
            channel_id=event.channel_id,
            thread_id=event.thread_id if use_thread else None,
            limit=30,
        )
        if not messages:
            return AgentResponse(
                text="要約対象メッセージが見つかりませんでした。",
                status="not_found",
                metadata={"message_count": 0},
            )

        prompt_lines = ["以下を日本語で3行程度に要約してください。"]
        for idx, message in enumerate(messages, start=1):
            prompt_lines.append(f"{idx}. {message.user_id}: {message.text}")
        summary = self.llm_client.answer("\n".join(prompt_lines))
        summary = self._apply_mode(event=event, text=summary)
        return AgentResponse(
            text=summary,
            status="completed",
            metadata={"message_count": len(messages)},
        )

    def _handle_mode(self, event: InboundEvent, command: Command) -> AgentResponse:
        mode = str(command.payload.get("mode", "")).lower()
        if mode not in {"brief", "detailed"}:
            return AgentResponse(
                text="形式が不正です。例: /mode brief",
                status="validation_error",
                metadata={
                    "error_type": "validation_error",
                    "error_message": "invalid mode",
                },
            )
        self.preference_store.set_mode(
            platform=event.platform,
            workspace_id=event.workspace_id,
            user_id=event.user_id,
            mode=mode,
        )
        return AgentResponse(
            text=f"応答モードを {mode} に設定しました。",
            status="completed",
            metadata={"mode": mode},
        )

    def _log(
        self,
        *,
        action: str,
        event: InboundEvent | None,
        command: Command | None,
        response: AgentResponse,
        extra: dict[str, str] | None = None,
        request_id: str | None = None,
    ) -> None:
        details = {
            "intent": command.intent.value if command else None,
            "status": response.status,
            "metadata": response.metadata,
            "request_id": request_id,
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

    def _record_metric(
        self,
        *,
        event: InboundEvent | None,
        command: Command | None,
        response: AgentResponse,
        duration_ms: int,
    ) -> None:
        self.metric_store.record(
            platform=event.platform if event else None,
            intent=command.intent.value if command else None,
            status=response.status,
            duration_ms=duration_ms,
        )

    def _store_conversation(self, event: InboundEvent, command: Command) -> None:
        if command.intent in {Intent.SUMMARIZE, Intent.MODE}:
            return
        if not event.text.strip():
            return
        self.conversation_store.append(
            ConversationMessage(
                platform=event.platform,
                workspace_id=event.workspace_id,
                channel_id=event.channel_id,
                thread_id=event.thread_id,
                user_id=event.user_id,
                message_id=event.message_id,
                text=event.text,
                ts=event.ts,
            )
        )

    def _apply_mode(self, *, event: InboundEvent, text: str) -> str:
        mode = self.preference_store.get_mode(
            platform=event.platform,
            workspace_id=event.workspace_id,
            user_id=event.user_id,
        )
        if mode == "detailed":
            return text
        limit = 140
        if len(text) <= limit:
            return text
        return text[:limit].rstrip() + "..."


def _duration_ms(start: float) -> int:
    return int((time.perf_counter() - start) * 1000)


def _build_ask_prompt(question: str, search_results: list[SearchResult]) -> str:
    if not search_results:
        return question

    lines = [
        f"質問: {question}",
        "参考情報:",
    ]
    for idx, result in enumerate(search_results, start=1):
        lines.append(f"{idx}. source={result.path}")
        lines.append(f"   snippet={result.snippet}")
    lines.append("参考情報に基づいて簡潔に回答してください。")
    return "\n".join(lines)
