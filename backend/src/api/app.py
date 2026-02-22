from __future__ import annotations

import os
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, PlainTextResponse

from api import inbound
from api.slack_payloads import extract_challenge, is_url_verification

app = FastAPI(title="home-secretary-backend", version="0.1.0")


def _signing_secret() -> str | None:
    secret = os.getenv("SLACK_SIGNING_SECRET", "").strip()
    return secret or None


@app.get("/api/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/api/metrics")
async def metrics() -> dict[str, Any]:
    audit_store = inbound.service.audit_store
    count_fn = getattr(audit_store, "count", None)
    audit_count = int(count_fn()) if callable(count_fn) else -1
    return {
        "pending_confirmations": len(inbound.service.pending_confirmations),
        "audit_log_count": audit_count,
    }


@app.post("/api/inbound/slack/events")
async def inbound_slack_events(request: Request) -> JSONResponse:
    raw_body = await request.body()
    payload = await request.json()

    if is_url_verification(payload):
        return JSONResponse({"challenge": extract_challenge(payload)})

    result = inbound.handle_slack_event(
        payload,
        headers=request.headers,
        raw_body=raw_body,
        signing_secret=_signing_secret(),
    )
    status_code = 401 if result.get("status") == "unauthorized" else 200
    return JSONResponse(result, status_code=status_code)


@app.post("/api/inbound/slack/commands")
async def inbound_slack_commands(request: Request) -> JSONResponse | PlainTextResponse:
    raw_body = await request.body()
    form = await request.form()
    form_payload = {k: str(v) for k, v in form.items()}
    result = inbound.handle_slack_command(
        form_payload,
        headers=request.headers,
        raw_body=raw_body,
        signing_secret=_signing_secret(),
    )
    status_code = 401 if result.get("status") == "unauthorized" else 200
    return JSONResponse(result, status_code=status_code)


@app.post("/api/inbound/slack/interactions")
async def inbound_slack_interactions(request: Request) -> JSONResponse:
    raw_body = await request.body()
    form = await request.form()
    form_payload = {k: str(v) for k, v in form.items()}
    result = inbound.handle_slack_interaction(
        form_payload,
        headers=request.headers,
        raw_body=raw_body,
        signing_secret=_signing_secret(),
    )
    status_code = 401 if result.get("status") == "unauthorized" else 200
    return JSONResponse(result, status_code=status_code)


@app.post("/api/agent/confirm-action")
async def confirm_action(request: Request) -> JSONResponse:
    payload = await request.json()
    result = inbound.handle_confirmation(payload)
    return JSONResponse(result)
