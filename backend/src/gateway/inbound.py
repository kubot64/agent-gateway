from __future__ import annotations

import json
from typing import Any, Mapping
from datetime import datetime, timezone

from app.models import InboundEvent
from gateway.discord_payloads import is_discord_ping, normalize_discord_interaction
from gateway.discord_security import DiscordSignatureVerificationError, verify_discord_signature
from gateway.security import SignatureVerificationError, verify_slack_signature
from gateway.slack_payloads import (
    extract_confirmation_from_interaction,
    normalize_slash_command,
    parse_interaction_payload,
)
from gateway.slack_responses import build_slack_response
from infra.factory import build_infra, default_sqlite_path_from_file


_infra = build_infra(default_sqlite_path=default_sqlite_path_from_file(__file__))
database = _infra.database
service = _infra.service
idempotency_store = _infra.idempotency_store


def handle_slack_event(
    payload: dict,
    *,
    headers: Mapping[str, str] | None = None,
    raw_body: bytes | None = None,
    signing_secret: str | None = None,
    request_id: str | None = None,
) -> dict:
    rejected = _reject_unsigned_slack(
        signing_secret=signing_secret,
        headers=headers,
        raw_body=raw_body if raw_body is not None else _json_bytes(payload),
        request_id=request_id,
    )
    if rejected:
        return rejected
    return _process_slack_event(payload, request_id=request_id)


def _process_slack_event(payload: dict, *, request_id: str | None = None) -> dict:
    """署名を確かめたあとのイベントを処理する。署名の検証は呼び出し側の責任。"""
    event = _normalize_slack_event(payload)
    idempotency_key = _build_slack_idempotency_key(payload, event)
    if not idempotency_store.mark_processed(idempotency_key):
        return {
            "status": "duplicate",
            "text": "重複イベントのためスキップしました。",
            "metadata": {"idempotency_key": idempotency_key, "request_id": request_id},
        }
    response = service.handle_message(event, request_id=request_id)
    metadata = dict(response.metadata)
    if request_id:
        metadata["request_id"] = request_id
    return {
        "status": response.status,
        "text": response.text,
        "metadata": metadata,
    }


def _handle_confirmation(payload: dict, *, request_id: str | None = None) -> dict:
    response = service.confirm_action(
        confirmation_id=str(payload.get("confirmation_id", "")),
        approved=bool(payload.get("approved", False)),
        request_id=request_id,
    )
    metadata = dict(response.metadata)
    if request_id:
        metadata["request_id"] = request_id
    return {
        "status": response.status,
        "text": response.text,
        "metadata": metadata,
    }


def handle_agent_message(payload: Mapping[str, Any], *, request_id: str | None = None) -> dict:
    event = InboundEvent(
        platform=str(payload.get("platform", "api")),
        workspace_id=str(payload.get("workspace_id", "direct")),
        channel_id=str(payload.get("channel_id", "direct")),
        thread_id=str(payload.get("thread_id")) if payload.get("thread_id") is not None else None,
        user_id=str(payload.get("user_id", "unknown")),
        message_id=str(payload.get("message_id", f"api-{datetime.now(timezone.utc).timestamp()}")),
        text=str(payload.get("text", "")),
        ts=datetime.now(timezone.utc),
    )
    response = service.handle_message(event, request_id=request_id)
    metadata = dict(response.metadata)
    if request_id:
        metadata["request_id"] = request_id
    return {
        "status": response.status,
        "text": response.text,
        "metadata": metadata,
    }


def handle_slack_command(
    form_payload: Mapping[str, str],
    *,
    headers: Mapping[str, str] | None = None,
    raw_body: bytes | None = None,
    signing_secret: str | None = None,
    request_id: str | None = None,
) -> dict:
    normalized_payload = normalize_slash_command(form_payload)
    agent_response = handle_slack_event(
        normalized_payload,
        headers=headers,
        raw_body=raw_body,
        signing_secret=signing_secret,
        request_id=request_id,
    )
    return build_slack_response(agent_response)


def handle_slack_interaction(
    form_payload: Mapping[str, str],
    *,
    headers: Mapping[str, str] | None = None,
    raw_body: bytes | None = None,
    signing_secret: str | None = None,
    request_id: str | None = None,
) -> dict:
    rejected = _reject_unsigned_slack(
        signing_secret=signing_secret,
        headers=headers,
        raw_body=raw_body if raw_body is not None else _json_bytes(form_payload),
        request_id=request_id,
    )
    if rejected:
        return rejected

    payload = parse_interaction_payload(form_payload)
    confirmation = extract_confirmation_from_interaction(payload)
    if not confirmation:
        return build_slack_response(
            {
                "status": "unsupported",
                "text": "未対応のインタラクションです。",
                "metadata": {},
            }
        )
    agent_response = _handle_confirmation(confirmation, request_id=request_id)
    return build_slack_response(agent_response)


def handle_discord_interaction(
    payload: Mapping[str, Any],
    *,
    headers: Mapping[str, str] | None = None,
    raw_body: bytes | None = None,
    public_key: str | None = None,
    request_id: str | None = None,
) -> dict:
    if not public_key:
        return _unauthorized(
            request_id=request_id,
            text="署名鍵が設定されていないため、受け付けられません。",
            error_type="public_key_not_configured",
            error_message="DISCORD_PUBLIC_KEY is not configured",
        )
    try:
        verify_discord_signature(
            public_key=public_key,
            raw_body=raw_body if raw_body is not None else _json_bytes(payload),
            headers=headers or {},
        )
    except DiscordSignatureVerificationError:
        return _unauthorized(
            request_id=request_id,
            text="署名検証に失敗しました。",
            error_type="signature_verification_error",
            error_message="invalid discord signature",
        )

    if is_discord_ping(payload):
        return {"type": 1}

    idempotency_key = _build_discord_idempotency_key(payload)
    if not idempotency_store.mark_processed(idempotency_key):
        return {
            "type": 4,
            "data": {"content": "重複イベントのためスキップしました。"},
            "status": "duplicate",
            "metadata": {"idempotency_key": idempotency_key, "request_id": request_id},
        }

    normalized_payload = normalize_discord_interaction(payload)
    agent_response = _process_slack_event(normalized_payload, request_id=request_id)
    content = str(agent_response.get("text", ""))
    metadata = dict(agent_response.get("metadata", {}))
    if request_id:
        metadata["request_id"] = request_id
    return {
        "type": 4,
        "data": {
            "content": content,
        },
        "status": agent_response.get("status", "unknown"),
        "metadata": metadata,
    }


def _reject_unsigned_slack(
    *,
    signing_secret: str | None,
    headers: Mapping[str, str] | None,
    raw_body: bytes,
    request_id: str | None,
) -> dict | None:
    """Slack の署名を確かめ、通らなければ拒否の応答を返す。

    署名鍵が未設定のときも拒否する。鍵なしで検証を飛ばすと、誰でも依頼者を名乗ったり承認したりできてしまう。
    """
    if not signing_secret:
        return _unauthorized(
            request_id=request_id,
            text="署名鍵が設定されていないため、受け付けられません。",
            error_type="signing_secret_not_configured",
            error_message="SLACK_SIGNING_SECRET is not configured",
        )
    try:
        verify_slack_signature(signing_secret=signing_secret, raw_body=raw_body, headers=headers or {})
    except SignatureVerificationError:
        return _unauthorized(
            request_id=request_id,
            text="署名検証に失敗しました。",
            error_type="signature_verification_error",
            error_message="invalid slack signature",
        )
    return None


def _unauthorized(*, request_id: str | None, text: str, error_type: str, error_message: str) -> dict:
    return {
        "status": "unauthorized",
        "text": text,
        "metadata": {
            "request_id": request_id,
            "error_type": error_type,
            "error_message": error_message,
        },
    }


def _json_bytes(payload: Mapping[str, Any]) -> bytes:
    return json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


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


def _build_slack_idempotency_key(payload: Mapping[str, Any], event: InboundEvent) -> str:
    event_id = str(payload.get("event_id", "")).strip()
    if event_id:
        return f"slack:event:{event_id}"
    return f"slack:msg:{event.workspace_id}:{event.channel_id}:{event.message_id}"


def _build_discord_idempotency_key(payload: Mapping[str, Any]) -> str:
    interaction_id = str(payload.get("id", "")).strip()
    if interaction_id:
        return f"discord:interaction:{interaction_id}"
    return "discord:interaction:unknown"
