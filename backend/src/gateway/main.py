from __future__ import annotations

import os
from uuid import uuid4
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from gateway.slack_web_api import SlackAPIError, SlackWebAPIClient
from gateway import inbound
from gateway.observability import configure_logging, log_http_request
from gateway.slack_payloads import extract_challenge, is_url_verification

app = FastAPI(title="home-secretary-backend", version="0.1.0")
configure_logging()


@app.middleware("http")
async def attach_request_id(request: Request, call_next: Any) -> Any:
    request_id = request.headers.get("x-request-id", "").strip() or str(uuid4())
    request.state.request_id = request_id
    response = await call_next(request)
    response.headers["x-request-id"] = request_id
    return response


@app.middleware("http")
async def access_log(request: Request, call_next: Any) -> Any:
    return await log_http_request(request, call_next)


def _signing_secret() -> str | None:
    secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
    return secret or None


def _bot_token() -> str | None:
    token = os.getenv("SLACK_BOT_TOKEN", "").strip()
    return token or None


def _discord_public_key() -> str | None:
    key = os.getenv("DISCORD_PUBLIC_KEY", "").strip()
    return key or None


def _extract_reply_target(payload: dict) -> tuple[str, str | None] | None:
    event = payload.get("event", {})
    if not isinstance(event, dict):
        return None
    if str(event.get("type", "")) != "message":
        return None
    if event.get("bot_id") or event.get("subtype") == "bot_message":
        return None
    channel = str(event.get("channel", "")).strip()
    if not channel:
        return None
    thread_ts = event.get("thread_ts") or event.get("ts")
    return channel, str(thread_ts) if thread_ts else None


def _try_post_reply(payload: dict, text: str) -> None:
    token = _bot_token()
    if not token:
        return
    target = _extract_reply_target(payload)
    if not target:
        return
    channel, thread_ts = target
    try:
        SlackWebAPIClient(bot_token=token).post_message(channel=channel, text=text, thread_ts=thread_ts)
    except SlackAPIError:
        # Keep inbound success path resilient even if Slack outbound fails.
        return


@app.get("/api/health")
async def health() -> dict[str, str]:
    db_ok = inbound.database.health_check()
    idem_check = getattr(inbound.idempotency_store, "health_check", None)
    idem_ok = bool(idem_check()) if callable(idem_check) else True
    overall_ok = db_ok and idem_ok
    return {
        "status": "ok" if overall_ok else "degraded",
        "database": "ok" if db_ok else "error",
        "idempotency": "ok" if idem_ok else "error",
    }


@app.get("/api/metrics")
async def metrics() -> dict[str, Any]:
    audit_store = inbound.service.audit_store
    count_fn = getattr(audit_store, "count", None)
    audit_count = int(count_fn()) if callable(count_fn) else -1
    metric_store = inbound.service.metric_store
    summary_fn = getattr(metric_store, "summary", None)
    metric_summary = summary_fn() if callable(summary_fn) else {}
    return {
        "pending_confirmations": len(inbound.service.pending_confirmations),
        "audit_log_count": audit_count,
        "command_metrics": metric_summary,
    }


@app.post("/api/inbound/slack/events")
async def inbound_slack_events(request: Request) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    raw_body = await request.body()
    payload = await request.json()

    if is_url_verification(payload):
        return JSONResponse({"challenge": extract_challenge(payload)})

    result = inbound.handle_slack_event(
        payload,
        headers=request.headers,
        raw_body=raw_body,
        signing_secret=_signing_secret(),
        request_id=request_id,
    )
    if result.get("status") == "unauthorized":
        return JSONResponse(result, status_code=401, headers={"x-request-id": request_id})

    if result.get("status") != "duplicate":
        _try_post_reply(payload, str(result.get("text", "")))
    return JSONResponse(
        {"ok": True, "status": result.get("status"), "metadata": result.get("metadata", {})},
        status_code=200,
        headers={"x-request-id": request_id},
    )


@app.post("/api/inbound/slack/commands")
async def inbound_slack_commands(request: Request) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    raw_body = await request.body()
    form = await request.form()
    form_payload = {k: str(v) for k, v in form.items()}
    result = inbound.handle_slack_command(
        form_payload,
        headers=request.headers,
        raw_body=raw_body,
        signing_secret=_signing_secret(),
        request_id=request_id,
    )
    status_code = 401 if result.get("status") == "unauthorized" else 200
    return JSONResponse(result, status_code=status_code, headers={"x-request-id": request_id})


@app.post("/api/inbound/slack/interactions")
async def inbound_slack_interactions(request: Request) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    raw_body = await request.body()
    form = await request.form()
    form_payload = {k: str(v) for k, v in form.items()}
    result = inbound.handle_slack_interaction(
        form_payload,
        headers=request.headers,
        raw_body=raw_body,
        signing_secret=_signing_secret(),
        request_id=request_id,
    )
    status_code = 401 if result.get("status") == "unauthorized" else 200
    return JSONResponse(result, status_code=status_code, headers={"x-request-id": request_id})


@app.post("/api/inbound/discord/interactions")
async def inbound_discord_interactions(request: Request) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    raw_body = await request.body()
    payload = await request.json()
    result = inbound.handle_discord_interaction(
        payload,
        headers=request.headers,
        raw_body=raw_body,
        public_key=_discord_public_key(),
        request_id=request_id,
    )
    if result.get("status") == "unauthorized":
        return JSONResponse(result, status_code=401, headers={"x-request-id": request_id})
    return JSONResponse(result, headers={"x-request-id": request_id})


@app.post("/api/agent/confirm-action")
async def confirm_action(request: Request) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    payload = await request.json()
    result = inbound.handle_confirmation(payload, request_id=request_id)
    return JSONResponse(result, headers={"x-request-id": request_id})


@app.post("/api/agent/handle-message")
async def handle_message(request: Request) -> JSONResponse:
    request_id = str(getattr(request.state, "request_id", ""))
    payload = await request.json()
    result = inbound.handle_agent_message(payload, request_id=request_id)
    return JSONResponse(result, headers={"x-request-id": request_id})
