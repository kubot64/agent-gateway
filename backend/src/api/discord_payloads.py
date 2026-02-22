from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Mapping
from uuid import uuid4


def is_discord_ping(payload: Mapping[str, Any]) -> bool:
    return int(payload.get("type", 0)) == 1


def normalize_discord_interaction(payload: Mapping[str, Any]) -> dict[str, Any]:
    guild_id = str(payload.get("guild_id", "unknown"))
    channel_id = str(payload.get("channel_id", "unknown"))
    interaction_id = str(payload.get("id", str(uuid4())))

    data = payload.get("data", {})
    if not isinstance(data, dict):
        data = {}
    command_name = str(data.get("name", "")).lower().strip()
    options = _options_to_map(data.get("options", []))

    text = _build_command_text(command_name=command_name, options=options)
    return {
        "team_id": guild_id,
        "event": {
            "channel": channel_id,
            "thread_ts": None,
            "user": str(payload.get("member", {}).get("user", {}).get("id", payload.get("user", {}).get("id", "unknown"))),
            "client_msg_id": interaction_id,
            "ts": str(datetime.now(timezone.utc).timestamp()),
            "text": text,
        },
    }


def _options_to_map(options: Any) -> dict[str, str]:
    if not isinstance(options, list):
        return {}
    mapped: dict[str, str] = {}
    for item in options:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name", "")).strip()
        value = str(item.get("value", "")).strip()
        if name:
            mapped[name] = value
    return mapped


def _build_command_text(*, command_name: str, options: Mapping[str, str]) -> str:
    if command_name == "ask":
        return f"/ask {options.get('question', '')}".strip()
    if command_name == "summarize":
        return f"/summarize {options.get('target', '')}".strip()
    if command_name == "remind":
        when = options.get("datetime", "")
        message = options.get("message", "")
        return f"/remind {when} {message}".strip()
    return str(options.get("text", "")).strip()
