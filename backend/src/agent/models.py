from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any


class Intent(str, Enum):
    ASK = "ask"
    SUMMARIZE = "summarize"
    REMIND = "remind"
    TASK = "task"
    UNKNOWN = "unknown"


@dataclass
class InboundEvent:
    platform: str
    workspace_id: str
    channel_id: str
    thread_id: str | None
    user_id: str
    message_id: str
    text: str
    ts: datetime


@dataclass
class Command:
    intent: Intent
    payload: dict[str, Any] = field(default_factory=dict)
    requires_confirmation: bool = False


@dataclass
class Reminder:
    reminder_id: str
    platform: str
    channel_id: str
    user_id: str
    remind_at: datetime
    message: str
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


@dataclass
class AgentResponse:
    text: str
    status: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class PendingConfirmation:
    confirmation_id: str
    command: Command
    created_at: datetime
    expires_at: datetime
