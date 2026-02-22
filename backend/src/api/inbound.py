from __future__ import annotations

from datetime import datetime, timezone

from agent.models import InboundEvent
from agent.service import AgentService

service = AgentService()


def handle_slack_event(payload: dict) -> dict:
    """Framework-agnostic entrypoint used by future web handlers."""
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
