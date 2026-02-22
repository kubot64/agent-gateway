from __future__ import annotations

from datetime import datetime, timezone
from typing import Mapping
from uuid import uuid4


def is_url_verification(payload: Mapping[str, object]) -> bool:
    return str(payload.get("type", "")) == "url_verification"


def extract_challenge(payload: Mapping[str, object]) -> str:
    return str(payload.get("challenge", ""))


def normalize_slash_command(form_payload: Mapping[str, str]) -> dict:
    command = str(form_payload.get("command", "")).strip()
    text = str(form_payload.get("text", "")).strip()
    command_text = f"{command} {text}".strip()
    event_ts = str(datetime.now(timezone.utc).timestamp())
    return {
        "team_id": str(form_payload.get("team_id", "unknown")),
        "event": {
            "channel": str(form_payload.get("channel_id", "unknown")),
            "thread_ts": None,
            "user": str(form_payload.get("user_id", "unknown")),
            "client_msg_id": str(uuid4()),
            "ts": event_ts,
            "text": command_text,
        },
    }
