from __future__ import annotations

import json
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


def parse_interaction_payload(form_payload: Mapping[str, str]) -> dict:
    raw_payload = str(form_payload.get("payload", "")).strip()
    if not raw_payload:
        return {}
    try:
        payload = json.loads(raw_payload)
    except json.JSONDecodeError:
        return {}
    if not isinstance(payload, dict):
        return {}
    return payload


def extract_confirmation_from_interaction(payload: Mapping[str, object]) -> dict:
    actions = payload.get("actions", [])
    if not isinstance(actions, list) or not actions:
        return {}
    action = actions[0]
    if not isinstance(action, dict):
        return {}

    action_id = str(action.get("action_id", "")).strip().lower()
    value = str(action.get("value", "")).strip()
    if not value:
        return {}

    if action_id == "confirm":
        approved = True
    elif action_id == "cancel":
        approved = False
    else:
        return {}

    return {
        "confirmation_id": value,
        "approved": approved,
    }
