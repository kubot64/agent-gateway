from __future__ import annotations

import json
from pathlib import Path
from typing import Mapping
from datetime import datetime, timezone

from agent.models import InboundEvent
from agent.service import AgentService
from api.security import SignatureVerificationError, verify_slack_signature
from api.slack_payloads import normalize_slash_command
from infra.stores import SQLiteAuditLogStore, SQLiteDatabase, SQLiteReminderStore


def _default_database_path() -> str:
    backend_dir = Path(__file__).resolve().parents[2]
    return str(backend_dir / "data" / "agent.db")


def _build_service() -> AgentService:
    database = SQLiteDatabase(db_path=_default_database_path())
    return AgentService(
        reminder_store=SQLiteReminderStore(database),
        audit_store=SQLiteAuditLogStore(database),
    )


service = _build_service()


def handle_slack_event(
    payload: dict,
    *,
    headers: Mapping[str, str] | None = None,
    raw_body: bytes | None = None,
    signing_secret: str | None = None,
) -> dict:
    """Framework-agnostic entrypoint used by future web handlers."""
    if signing_secret:
        body = raw_body
        if body is None:
            body = json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
        try:
            verify_slack_signature(signing_secret=signing_secret, raw_body=body, headers=headers or {})
        except SignatureVerificationError:
            return {"status": "unauthorized", "text": "署名検証に失敗しました。", "metadata": {}}

    event = _normalize_slack_event(payload)
    response = service.handle_message(event)
    return {
        "status": response.status,
        "text": response.text,
        "metadata": response.metadata,
    }


def handle_confirmation(payload: dict) -> dict:
    response = service.confirm_action(
        confirmation_id=str(payload.get("confirmation_id", "")),
        approved=bool(payload.get("approved", False)),
    )
    return {
        "status": response.status,
        "text": response.text,
        "metadata": response.metadata,
    }


def handle_slack_command(
    form_payload: Mapping[str, str],
    *,
    headers: Mapping[str, str] | None = None,
    raw_body: bytes | None = None,
    signing_secret: str | None = None,
) -> dict:
    normalized_payload = normalize_slash_command(form_payload)
    response = handle_slack_event(
        normalized_payload,
        headers=headers,
        raw_body=raw_body,
        signing_secret=signing_secret,
    )
    return {
        "response_type": "ephemeral",
        "text": response["text"],
        "metadata": response["metadata"],
        "status": response["status"],
    }


def _normalize_slack_event(payload: dict) -> InboundEvent:
    event = payload.get("event", {})
    ts = event.get("ts")
    event_dt = datetime.fromtimestamp(float(ts), tz=timezone.utc) if ts else datetime.now(timezone.utc)
    return InboundEvent(
        platform="slack",
        workspace_id=str(payload.get("team_id", "unknown")),
        channel_id=str(event.get("channel", "unknown")),
        thread_id=event.get("thread_ts"),
        user_id=str(event.get("user", "unknown")),
        message_id=str(event.get("client_msg_id") or event.get("ts") or "unknown"),
        text=str(event.get("text", "")),
        ts=event_dt,
    )
