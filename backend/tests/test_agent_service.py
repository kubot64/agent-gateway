from __future__ import annotations

import asyncio
import hmac
import json
import os
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from urllib.parse import urlencode
from uuid import uuid4

from nacl.signing import SigningKey

from gateway.slack_web_api import SlackAPIError, SlackWebAPIClient
from app.llm import LocalFallbackLLMClient
from app.models import InboundEvent
from app.service import AgentService, InMemoryMetricStore
from policy.executor import ToolExecutor
from tools.docs_search import DocsSearchTool, LocalMarkdownSearchClient, LocalVectorSearchClient, SearchResult, build_default_search_client
from tools.linear import TaskClientError
from tools.registry import ToolRegistry
from gateway.discord_payloads import normalize_discord_interaction
from gateway.discord_security import DiscordSignatureVerificationError, verify_discord_signature
from gateway.inbound import handle_agent_message, handle_discord_interaction, handle_slack_command, handle_slack_event, handle_slack_interaction
from gateway.security import verify_slack_signature
from gateway.slack_payloads import extract_confirmation_from_interaction, normalize_slash_command, parse_interaction_payload
from gateway.slack_responses import build_slack_response
from infra.stores import SQLiteAuditLogStore, SQLiteCommandMetricStore, SQLiteDatabase, SQLiteIdempotencyStore, SQLiteReminderStore
from infra.factory import build_infra


SLACK_TEST_SECRET = "test-signing-secret"


def _slack_signed_headers(secret: str, raw_body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    base = f"v0:{timestamp}:".encode("utf-8") + raw_body
    signature = "v0=" + hmac.new(secret.encode("utf-8"), base, "sha256").hexdigest()
    return {"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature}


DISCORD_TEST_SIGNING_KEY = SigningKey.generate()
DISCORD_TEST_PUBLIC_KEY = DISCORD_TEST_SIGNING_KEY.verify_key.encode().hex()


def _discord_signed_headers(raw_body: bytes) -> dict[str, str]:
    timestamp = str(int(time.time()))
    signature = DISCORD_TEST_SIGNING_KEY.sign(timestamp.encode("utf-8") + raw_body).signature.hex()
    return {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp}


def _signed_slack_event(payload: dict, *, request_id: str | None = None) -> dict:
    raw_body = json.dumps(payload).encode("utf-8")
    return handle_slack_event(
        payload,
        headers=_slack_signed_headers(SLACK_TEST_SECRET, raw_body),
        raw_body=raw_body,
        signing_secret=SLACK_TEST_SECRET,
        request_id=request_id,
    )


def _signed_slack_command(form: dict[str, str]) -> dict:
    raw_body = urlencode(form).encode("utf-8")
    return handle_slack_command(
        form,
        headers=_slack_signed_headers(SLACK_TEST_SECRET, raw_body),
        raw_body=raw_body,
        signing_secret=SLACK_TEST_SECRET,
    )


def _signed_discord_interaction(payload: dict) -> dict:
    raw_body = json.dumps(payload).encode("utf-8")
    return handle_discord_interaction(
        payload,
        headers=_discord_signed_headers(raw_body),
        raw_body=raw_body,
        public_key=DISCORD_TEST_PUBLIC_KEY,
    )


def build_event(text: str) -> InboundEvent:
    return InboundEvent(
        platform="slack",
        workspace_id="T1",
        channel_id="C1",
        thread_id="123.45",
        user_id="U1",
        message_id="m1",
        text=text,
        ts=datetime.now(timezone.utc),
    )


class AgentServiceTests(unittest.TestCase):
    class FakeLLMClient:
        def answer(self, question: str) -> str:
            return f"SUMMARY::{question[:40]}"

    class FakeLongLLMClient:
        def answer(self, question: str) -> str:
            return "L" * 220

    class FakeTaskClient:
        def __init__(self, should_fail: bool = False) -> None:
            self.should_fail = should_fail

        def create_task(self, *, title: str, description: str | None = None, creator_id: str | None = None) -> dict[str, str]:
            if self.should_fail:
                raise TaskClientError("failed")
            return {
                "provider": "linear",
                "task_id": "ENG-123",
                "url": "https://linear.app/example/issue/ENG-123",
            }

    class FakeSearchClient:
        def __init__(self, results: list[SearchResult]) -> None:
            self._results = results

        def search(self, query: str, *, limit: int = 3) -> list[SearchResult]:
            return self._results[:limit]

    def test_ask_command_uses_llm(self) -> None:
        service = AgentService(llm_client=LocalFallbackLLMClient())
        response = service.handle_message(build_event("/ask 今日の予定は？"))
        self.assertEqual(response.status, "completed")
        self.assertIn("今日の予定は？", response.text)

    def test_remind_command_registers_reminder(self) -> None:
        service = AgentService()
        response = service.handle_message(build_event("/remind 2026-03-01T09:00 資料提出"))
        self.assertEqual(response.status, "completed")
        self.assertIn("reminder_id", response.metadata)

    def test_summarize_with_conversation_messages(self) -> None:
        service = AgentService(llm_client=self.FakeLLMClient())
        service.handle_message(build_event("昨日の進捗はAを完了"))
        response = service.handle_message(build_event("/summarize current_thread"))
        self.assertEqual(response.status, "completed")
        self.assertGreaterEqual(int(response.metadata.get("message_count", 0)), 1)
        self.assertTrue(response.text.startswith("SUMMARY::"))

    def test_summarize_without_messages(self) -> None:
        service = AgentService(llm_client=self.FakeLLMClient())
        response = service.handle_message(build_event("/summarize current_thread"))
        self.assertEqual(response.status, "not_found")
        self.assertEqual(response.metadata.get("message_count"), 0)

    def test_remind_all_requires_confirmation(self) -> None:
        service = AgentService()
        response = service.handle_message(build_event("/remind 2026-03-01T09:00 all hands"))
        self.assertEqual(response.status, "needs_confirmation")
        confirmation_id = response.metadata["confirmation_id"]
        approved = service.confirm_action(confirmation_id, True)
        self.assertEqual(approved.status, "completed")
        self.assertIn("reminder_id", approved.metadata)

    def test_confirmation_expires(self) -> None:
        current_time = datetime(2026, 3, 1, 0, 0, tzinfo=timezone.utc)
        service = AgentService(
            now_provider=lambda: current_time,
            confirmation_ttl=timedelta(seconds=1),
        )
        response = service.handle_message(build_event("/remind 2026-03-01T09:00 all hands"))
        confirmation_id = response.metadata["confirmation_id"]
        current_time = current_time + timedelta(seconds=3)
        expired = service.confirm_action(confirmation_id, True)
        self.assertEqual(expired.status, "expired")

    def test_task_add_creates_task(self) -> None:
        service = AgentService(task_client=self.FakeTaskClient())
        response = service.handle_message(build_event("/task add APIの不具合を調査"))
        self.assertEqual(response.status, "completed")
        self.assertEqual(response.metadata["provider"], "linear")
        self.assertEqual(response.metadata["task_id"], "ENG-123")

    def test_task_add_validation_error(self) -> None:
        service = AgentService(task_client=self.FakeTaskClient())
        response = service.handle_message(build_event("/task add"))
        self.assertEqual(response.status, "validation_error")

    def test_mode_brief_truncates_ask(self) -> None:
        service = AgentService(llm_client=self.FakeLongLLMClient())
        mode_resp = service.handle_message(build_event("/mode brief"))
        self.assertEqual(mode_resp.status, "completed")
        ask_resp = service.handle_message(build_event("/ask 長文確認"))
        self.assertEqual(ask_resp.status, "completed")
        self.assertLessEqual(len(ask_resp.text), 143)
        self.assertTrue(ask_resp.text.endswith("..."))

    def test_mode_detailed_keeps_full_length(self) -> None:
        service = AgentService(llm_client=self.FakeLongLLMClient())
        mode_resp = service.handle_message(build_event("/mode detailed"))
        self.assertEqual(mode_resp.status, "completed")
        ask_resp = service.handle_message(build_event("/ask 長文確認"))
        self.assertEqual(ask_resp.status, "completed")
        self.assertEqual(len(ask_resp.text), 220)

    def test_task_add_integration_error(self) -> None:
        service = AgentService(task_client=self.FakeTaskClient(should_fail=True))
        response = service.handle_message(build_event("/task add APIの不具合を調査"))
        self.assertEqual(response.status, "integration_error")

    def test_metrics_are_recorded(self) -> None:
        metric_store = InMemoryMetricStore()
        service = AgentService(metric_store=metric_store)
        service.handle_message(build_event("/ask test"))
        self.assertGreaterEqual(len(metric_store.records), 1)
        self.assertEqual(metric_store.records[0]["intent"], "ask")

    def test_ask_includes_sources_from_search(self) -> None:
        search_client = self.FakeSearchClient(
            [
                SearchResult(
                    path="docs/example.md",
                    snippet="署名検証を実装する",
                    score=3,
                )
            ]
        )
        service = AgentService(
            llm_client=LocalFallbackLLMClient(),
            tool_executor=ToolExecutor(ToolRegistry([DocsSearchTool(search_client)])),
        )
        response = service.handle_message(build_event("/ask 署名検証って何？"))
        self.assertEqual(response.status, "completed")
        self.assertEqual(response.metadata.get("sources"), ["docs/example.md"])
        self.assertIn("参考情報", response.text)


class SecurityTests(unittest.TestCase):
    def test_verify_slack_signature_accepts_valid_signature(self) -> None:
        secret = "secret"
        timestamp = "1700000000"
        body = b'{"type":"event_callback"}'
        base = f"v0:{timestamp}:".encode("utf-8") + body
        signature = "v0=" + hmac.new(secret.encode("utf-8"), base, "sha256").hexdigest()
        verify_slack_signature(
            signing_secret=secret,
            raw_body=body,
            headers={"X-Slack-Request-Timestamp": timestamp, "X-Slack-Signature": signature},
            now_epoch=1700000001,
        )

    def test_handle_slack_event_rejects_invalid_signature(self) -> None:
        payload = {"team_id": "T1", "event": {"ts": "1700000000", "channel": "C1", "user": "U1", "text": "/ask hello"}}
        response = handle_slack_event(
            payload,
            headers={"X-Slack-Request-Timestamp": "1700000000", "X-Slack-Signature": "v0=invalid"},
            raw_body=json.dumps(payload).encode("utf-8"),
            signing_secret="secret",
        )
        self.assertEqual(response["status"], "unauthorized")
        self.assertEqual(response["metadata"]["error_type"], "signature_verification_error")

    def test_verify_discord_signature_accepts_valid_with_custom_verifier(self) -> None:
        called: dict[str, object] = {}

        def verifier(public_key: str, message: bytes, signature: bytes) -> None:
            called["public_key"] = public_key
            called["message"] = message
            called["signature"] = signature

        verify_discord_signature(
            public_key="a" * 64,
            raw_body=b'{"type":1}',
            headers={
                "X-Signature-Ed25519": "ab" * 64,
                "X-Signature-Timestamp": "1700000000",
            },
            verifier=verifier,
        )
        self.assertEqual(called["public_key"], "a" * 64)
        self.assertEqual(called["message"], b"1700000000" + b'{"type":1}')
        self.assertEqual(called["signature"], bytes.fromhex("ab" * 64))

    def test_verify_discord_signature_missing_headers(self) -> None:
        with self.assertRaises(DiscordSignatureVerificationError):
            verify_discord_signature(
                public_key="a" * 64,
                raw_body=b"{}",
                headers={},
            )

    def test_handle_slack_event_duplicate_is_skipped(self) -> None:
        event_id = f"evt-dup-{uuid4()}"
        payload = {
            "event_id": event_id,
            "team_id": "T1",
            "event": {
                "ts": "1700000000",
                "channel": "C1",
                "user": "U1",
                "text": "/ask hello",
            },
        }
        first = _signed_slack_event(payload)
        second = _signed_slack_event(payload)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "duplicate")

    def test_request_id_is_propagated_to_metadata(self) -> None:
        payload = {
            "event_id": f"evt-{uuid4()}",
            "team_id": "T1",
            "event": {
                "ts": "1700000000",
                "channel": "C1",
                "user": "U1",
                "text": "/ask hello",
            },
        }
        response = _signed_slack_event(payload, request_id="req-123")
        self.assertEqual(response["metadata"]["request_id"], "req-123")

    def test_handle_agent_message_direct(self) -> None:
        response = handle_agent_message(
            {
                "platform": "api",
                "workspace_id": "W1",
                "channel_id": "direct",
                "user_id": "U1",
                "text": "/ask こんにちは",
            },
            request_id="req-direct-1",
        )
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["metadata"]["request_id"], "req-direct-1")


class SlackPayloadTests(unittest.TestCase):
    def test_normalize_slash_command(self) -> None:
        payload = normalize_slash_command(
            {
                "team_id": "T1",
                "channel_id": "C1",
                "user_id": "U1",
                "command": "/ask",
                "text": "今日の予定は？",
            }
        )
        self.assertEqual(payload["team_id"], "T1")
        self.assertEqual(payload["event"]["channel"], "C1")
        self.assertEqual(payload["event"]["user"], "U1")
        self.assertTrue(payload["event"]["text"].startswith("/ask "))

    def test_handle_slash_command(self) -> None:
        response = _signed_slack_command(
            {
                "team_id": "T1",
                "channel_id": "C1",
                "user_id": "U1",
                "command": "/ask",
                "text": "今日の予定は？",
            }
        )
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["response_type"], "ephemeral")
        self.assertFalse("blocks" in response)

    def test_handle_slash_command_with_alias(self) -> None:
        response = _signed_slack_command(
            {
                "team_id": "T1",
                "channel_id": "C1",
                "user_id": "U1",
                "command": "/homeask",
                "text": "今日の予定は？",
            }
        )
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["response_type"], "ephemeral")

    def test_parse_interaction_payload(self) -> None:
        parsed = parse_interaction_payload(
            {
                "payload": json.dumps(
                    {
                        "actions": [
                            {
                                "action_id": "confirm",
                                "value": "abc",
                            }
                        ]
                    }
                )
            }
        )
        self.assertEqual(parsed["actions"][0]["value"], "abc")

    def test_extract_confirmation_from_interaction(self) -> None:
        confirmation = extract_confirmation_from_interaction(
            {
                "actions": [
                    {
                        "action_id": "cancel",
                        "value": "xyz",
                    }
                ]
            }
        )
        self.assertEqual(confirmation["confirmation_id"], "xyz")
        self.assertEqual(confirmation["approved"], False)

    def test_handle_slack_interaction_confirm(self) -> None:
        pending = _signed_slack_command(
            {
                "team_id": "T1",
                "channel_id": "C1",
                "user_id": "U1",
                "command": "/remind",
                "text": "2026-03-01T09:00 all hands",
            }
        )
        confirmation_id = pending["metadata"]["confirmation_id"]
        form, raw_body = _interaction_form(confirmation_id)
        response = handle_slack_interaction(
            form,
            headers=_slack_signed_headers(SLACK_TEST_SECRET, raw_body),
            raw_body=raw_body,
            signing_secret=SLACK_TEST_SECRET,
        )
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["response_type"], "ephemeral")
        self.assertFalse("blocks" in response)

    def test_handle_slack_interaction_rejects_when_signing_secret_missing(self) -> None:
        pending = _signed_slack_command(
            {"team_id": "T1", "channel_id": "C1", "user_id": "U1", "command": "/remind", "text": "2026-03-01T09:00 all hands"}
        )
        form, raw_body = _interaction_form(pending["metadata"]["confirmation_id"])
        response = handle_slack_interaction(form, raw_body=raw_body, signing_secret=None)
        self.assertEqual(response["status"], "unauthorized")
        self.assertEqual(response["metadata"]["error_type"], "signing_secret_not_configured")

    def test_handle_slash_command_needs_confirmation_has_blocks(self) -> None:
        response = _signed_slack_command(
            {
                "team_id": "T1",
                "channel_id": "C1",
                "user_id": "U1",
                "command": "/remind",
                "text": "2026-03-01T09:00 all members",
            }
        )
        self.assertEqual(response["status"], "needs_confirmation")
        self.assertTrue("blocks" in response)
        blocks = response["blocks"]
        self.assertEqual(blocks[1]["type"], "actions")
        self.assertEqual(blocks[1]["elements"][0]["action_id"], "confirm")
        self.assertEqual(blocks[1]["elements"][1]["action_id"], "cancel")
        self.assertEqual(blocks[1]["elements"][0]["value"], response["metadata"]["confirmation_id"])

    def test_build_slack_response_with_confirmation(self) -> None:
        response = build_slack_response(
            {
                "status": "needs_confirmation",
                "text": "確認が必要です",
                "metadata": {"confirmation_id": "abc-123"},
            }
        )
        self.assertEqual(response["blocks"][1]["elements"][0]["value"], "abc-123")


def _interaction_form(confirmation_id: str, action_id: str = "confirm") -> tuple[dict[str, str], bytes]:
    form = {"payload": json.dumps({"actions": [{"action_id": action_id, "value": confirmation_id}]})}
    return form, urlencode(form).encode("utf-8")


def _post(path: str, body: bytes, content_type: str, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    """Send one POST to the FastAPI app over ASGI and return (status, JSON body)."""
    from gateway.main import app

    raw_headers = [(b"content-type", content_type.encode("ascii")), (b"content-length", str(len(body)).encode("ascii"))]
    for key, value in (headers or {}).items():
        raw_headers.append((key.lower().encode("ascii"), value.encode("utf-8")))
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": path,
        "raw_path": path.encode("ascii"),
        "query_string": b"",
        "root_path": "",
        "headers": raw_headers,
        "client": ("testclient", 0),
        "server": ("testserver", 80),
    }
    received = False
    messages: list[dict] = []

    async def receive() -> dict:
        nonlocal received
        if not received:
            received = True
            return {"type": "http.request", "body": body, "more_body": False}
        return {"type": "http.disconnect"}

    async def send(message: dict) -> None:
        messages.append(message)

    asyncio.run(app(scope, receive, send))
    start = next(m for m in messages if m["type"] == "http.response.start")
    response_body = b"".join(m.get("body", b"") for m in messages if m["type"] == "http.response.body")
    return start["status"], json.loads(response_body or b"{}")


def _post_json(path: str, payload: dict, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    return _post(path, json.dumps(payload).encode("utf-8"), "application/json", headers)


def _post_interaction(raw_body: bytes, headers: dict[str, str] | None = None) -> tuple[int, dict]:
    return _post("/api/inbound/slack/interactions", raw_body, "application/x-www-form-urlencoded", headers)


def _create_pending_confirmation() -> str:
    pending = _signed_slack_command(
        {"team_id": "T1", "channel_id": "C1", "user_id": "U1", "command": "/remind", "text": "2026-03-01T09:00 all hands"}
    )
    return pending["metadata"]["confirmation_id"]


class UnauthenticatedEntryTests(unittest.TestCase):
    ENV = "ENABLE_DIRECT_HANDLE_MESSAGE"
    ASK = {"platform": "api", "workspace_id": "W1", "channel_id": "C1", "user_id": "U1", "text": "/ask こんにちは"}

    def test_confirm_action_entry_does_not_exist(self) -> None:
        confirmation_id = _create_pending_confirmation()

        status, _ = _post_json("/api/agent/confirm-action", {"confirmation_id": confirmation_id, "approved": True})

        self.assertEqual(status, 404)
        # 承認待ちは消費されず、署名つきの Slack interaction からは今までどおり承認できる。
        _, raw_body = _interaction_form(confirmation_id)
        with _temporary_env({"SLACK_SIGNING_SECRET": SLACK_TEST_SECRET}):
            status, body = _post_interaction(raw_body, _slack_signed_headers(SLACK_TEST_SECRET, raw_body))
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "completed")

    def test_interaction_is_rejected_when_signing_secret_is_not_configured(self) -> None:
        confirmation_id = _create_pending_confirmation()
        _, raw_body = _interaction_form(confirmation_id)

        with _temporary_env({"SLACK_SIGNING_SECRET": None}):
            status, body = _post_interaction(raw_body)

        self.assertEqual(status, 401)
        self.assertEqual(body["status"], "unauthorized")
        # 拒否された承認は実行されず、承認待ちのまま残る。
        with _temporary_env({"SLACK_SIGNING_SECRET": SLACK_TEST_SECRET}):
            status, body = _post_interaction(raw_body, _slack_signed_headers(SLACK_TEST_SECRET, raw_body))
        self.assertEqual(body["status"], "completed")

    def test_unsigned_interaction_is_rejected(self) -> None:
        _, raw_body = _interaction_form(_create_pending_confirmation())
        with _temporary_env({"SLACK_SIGNING_SECRET": SLACK_TEST_SECRET}):
            status, body = _post_interaction(raw_body)
        self.assertEqual(status, 401)
        self.assertEqual(body["status"], "unauthorized")

    def test_slack_event_is_rejected_when_signing_secret_is_not_configured(self) -> None:
        payload = {"event_id": f"evt-{uuid4()}", "team_id": "T1", "event": {"ts": "1700000000", "channel": "C1", "user": "U1", "text": "/ask hello"}}
        with _temporary_env({"SLACK_SIGNING_SECRET": None, "SLACK_BOT_TOKEN": None}):
            status, body = _post_json("/api/inbound/slack/events", payload)
        self.assertEqual(status, 401)
        self.assertEqual(body["metadata"]["error_type"], "signing_secret_not_configured")

    def test_signed_slack_event_is_accepted(self) -> None:
        payload = {"event_id": f"evt-{uuid4()}", "team_id": "T1", "event": {"ts": "1700000000", "channel": "C1", "user": "U1", "text": "/ask hello"}}
        raw_body = json.dumps(payload).encode("utf-8")
        with _temporary_env({"SLACK_SIGNING_SECRET": SLACK_TEST_SECRET, "SLACK_BOT_TOKEN": None}):
            status, body = _post("/api/inbound/slack/events", raw_body, "application/json", _slack_signed_headers(SLACK_TEST_SECRET, raw_body))
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "completed")

    URL_VERIFICATION = {"type": "url_verification", "token": "t", "challenge": "challenge-123"}

    def test_url_verification_is_rejected_when_signing_secret_is_not_configured(self) -> None:
        with _temporary_env({"SLACK_SIGNING_SECRET": None}):
            status, body = _post_json("/api/inbound/slack/events", self.URL_VERIFICATION)
        self.assertEqual(status, 401)
        self.assertNotIn("challenge", body)

    def test_unsigned_url_verification_is_rejected(self) -> None:
        with _temporary_env({"SLACK_SIGNING_SECRET": SLACK_TEST_SECRET}):
            status, body = _post_json("/api/inbound/slack/events", self.URL_VERIFICATION)
        self.assertEqual(status, 401)
        self.assertNotIn("challenge", body)

    def test_signed_url_verification_returns_challenge(self) -> None:
        raw_body = json.dumps(self.URL_VERIFICATION).encode("utf-8")
        with _temporary_env({"SLACK_SIGNING_SECRET": SLACK_TEST_SECRET}):
            status, body = _post("/api/inbound/slack/events", raw_body, "application/json", _slack_signed_headers(SLACK_TEST_SECRET, raw_body))
        self.assertEqual(status, 200)
        self.assertEqual(body, {"challenge": "challenge-123"})

    def test_slack_command_is_rejected_when_signing_secret_is_not_configured(self) -> None:
        raw_body = urlencode({"team_id": "T1", "channel_id": "C1", "user_id": "U1", "command": "/ask", "text": "hello"}).encode("utf-8")
        with _temporary_env({"SLACK_SIGNING_SECRET": None}):
            status, body = _post("/api/inbound/slack/commands", raw_body, "application/x-www-form-urlencoded")
        self.assertEqual(status, 401)
        self.assertEqual(body["metadata"]["error_type"], "signing_secret_not_configured")

    def test_discord_interaction_is_rejected_when_public_key_is_not_configured(self) -> None:
        payload = {"type": 2, "id": f"i-{uuid4()}", "guild_id": "G1", "channel_id": "D1", "member": {"user": {"id": "U1"}}, "data": {"name": "ask", "options": [{"name": "question", "value": "hello"}]}}
        with _temporary_env({"DISCORD_PUBLIC_KEY": None}):
            status, body = _post_json("/api/inbound/discord/interactions", payload)
        self.assertEqual(status, 401)
        self.assertEqual(body["metadata"]["error_type"], "public_key_not_configured")

    def test_signed_discord_interaction_is_accepted(self) -> None:
        payload = {"type": 2, "id": f"i-{uuid4()}", "guild_id": "G1", "channel_id": "D1", "member": {"user": {"id": "U1"}}, "data": {"name": "ask", "options": [{"name": "question", "value": "hello"}]}}
        raw_body = json.dumps(payload).encode("utf-8")
        with _temporary_env({"DISCORD_PUBLIC_KEY": DISCORD_TEST_PUBLIC_KEY}):
            status, body = _post("/api/inbound/discord/interactions", raw_body, "application/json", _discord_signed_headers(raw_body))
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "completed")

    def test_handle_message_returns_404_without_env(self) -> None:
        with _temporary_env({self.ENV: None}):
            status, body = _post_json("/api/agent/handle-message", self.ASK)
        self.assertEqual(status, 404)
        self.assertNotIn("status", body)

    def test_handle_message_returns_404_when_env_is_not_truthy(self) -> None:
        with _temporary_env({self.ENV: "0"}):
            status, _ = _post_json("/api/agent/handle-message", self.ASK)
        self.assertEqual(status, 404)

    def test_handle_message_works_when_env_enabled(self) -> None:
        with _temporary_env({self.ENV: "1"}):
            status, body = _post_json("/api/agent/handle-message", self.ASK, headers={"x-request-id": "req-direct-http"})
        self.assertEqual(status, 200)
        self.assertEqual(body["status"], "completed")
        self.assertEqual(body["metadata"]["request_id"], "req-direct-http")


class SQLiteStoreTests(unittest.TestCase):
    def test_sqlite_reminder_and_audit_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            database = SQLiteDatabase(f"{tmp_dir}/agent.db")
            reminder_store = SQLiteReminderStore(database)
            audit_store = SQLiteAuditLogStore(database)

            reminder = reminder_store.create(
                platform="slack",
                channel_id="C1",
                user_id="U1",
                remind_at=datetime(2026, 3, 1, 9, 0, tzinfo=timezone.utc),
                message="資料提出",
            )
            fetched = reminder_store.get(reminder.reminder_id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.message if fetched else "", "資料提出")

            audit_store.log(
                action="handle_message",
                status="completed",
                platform="slack",
                user_id="U1",
                details='{"intent":"remind"}',
            )
            self.assertGreaterEqual(audit_store.count(), 1)

            metric_store = SQLiteCommandMetricStore(database)
            metric_store.record(platform="slack", intent="ask", status="completed", duration_ms=12)
            metric_store.record(platform="slack", intent="ask", status="integration_error", duration_ms=30)
            summary = metric_store.summary()
            self.assertEqual(summary["total"], 2)
            self.assertEqual(summary["errors"], 1)
            self.assertEqual(summary["by_intent"]["ask"], 2)
            self.assertEqual(summary["by_status"]["completed"], 1)
            self.assertEqual(summary["by_status"]["integration_error"], 1)
            self.assertEqual(database.health_check(), True)
            self.assertEqual(SQLiteIdempotencyStore(database).health_check(), True)


class SearchClientTests(unittest.TestCase):
    def test_local_markdown_search_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = tempfile.TemporaryDirectory(dir=tmp_dir)
            try:
                base = f"{root.name}/workspace"
                from pathlib import Path

                docs_dir = Path(base) / "docs"
                docs_dir.mkdir(parents=True, exist_ok=True)
                (docs_dir / "a.md").write_text("# Note\nSlack署名検証を実装した。", encoding="utf-8")
                (docs_dir / "b.md").write_text("# Other\n無関係なテキスト。", encoding="utf-8")

                client = LocalMarkdownSearchClient(
                    root_dir=Path(base),
                    include_paths=("docs",),
                )
                results = client.search("署名検証", limit=3)
                self.assertGreaterEqual(len(results), 1)
                self.assertEqual(results[0].path, "docs/a.md")
            finally:
                root.cleanup()

    def test_local_vector_search_client(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            root = tempfile.TemporaryDirectory(dir=tmp_dir)
            try:
                base = f"{root.name}/workspace"
                from pathlib import Path

                docs_dir = Path(base) / "docs"
                docs_dir.mkdir(parents=True, exist_ok=True)
                (docs_dir / "a.md").write_text("# Note\nSlack署名検証を実装した。", encoding="utf-8")
                (docs_dir / "b.md").write_text("# Other\n休暇の計画メモ。", encoding="utf-8")

                client = LocalVectorSearchClient(
                    root_dir=Path(base),
                    include_paths=("docs",),
                )
                results = client.search("署名検証", limit=3)
                self.assertGreaterEqual(len(results), 1)
                self.assertEqual(results[0].path, "docs/a.md")
            finally:
                root.cleanup()

    def test_build_default_search_client_vector_backend(self) -> None:
        with _temporary_env({"DOC_SEARCH_BACKEND": "vector"}):
            client = build_default_search_client()
        self.assertIsInstance(client, LocalVectorSearchClient)


class InfraFactoryTests(unittest.TestCase):
    def test_build_infra_defaults_to_sqlite(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = f"{tmp_dir}/agent.db"
            with _temporary_env({"DATABASE_BACKEND": None, "IDEMPOTENCY_BACKEND": None}):
                infra = build_infra(default_sqlite_path=db_path)
            self.assertIsInstance(infra.database, SQLiteDatabase)
            self.assertTrue(infra.database.health_check())

    def test_build_infra_redis_requires_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_path = f"{tmp_dir}/agent.db"
            with _temporary_env({"DATABASE_BACKEND": "sqlite", "IDEMPOTENCY_BACKEND": "redis", "REDIS_URL": None}):
                with self.assertRaises(ValueError):
                    build_infra(default_sqlite_path=db_path)


class _temporary_env:
    def __init__(self, updates: dict[str, str | None]) -> None:
        self.updates = updates
        self.originals: dict[str, str | None] = {}

    def __enter__(self) -> None:
        for key, value in self.updates.items():
            self.originals[key] = os.environ.get(key)
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
        for key, original in self.originals.items():
            if original is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = original
        return False


class SlackWebAPITests(unittest.TestCase):
    def test_post_message_success(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

            def read(self) -> bytes:
                return b'{"ok": true, "channel": "C1", "ts": "1.23"}'

        with patch("gateway.slack_web_api.request.urlopen", return_value=FakeResponse()) as mocked:
            client = SlackWebAPIClient(bot_token="xoxb-test")
            result = client.post_message(channel="C1", text="hello", thread_ts="1.11")
            self.assertEqual(result["ok"], True)
            req = mocked.call_args[0][0]
            self.assertEqual(req.full_url, "https://slack.com/api/chat.postMessage")

    def test_post_message_error(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

            def read(self) -> bytes:
                return b'{"ok": false, "error": "channel_not_found"}'

        with patch("gateway.slack_web_api.request.urlopen", return_value=FakeResponse()):
            client = SlackWebAPIClient(bot_token="xoxb-test")
            with self.assertRaises(SlackAPIError):
                client.post_message(channel="C1", text="hello")


class DiscordInboundTests(unittest.TestCase):
    def test_normalize_discord_interaction_ask(self) -> None:
        payload = normalize_discord_interaction(
            {
                "id": "i1",
                "guild_id": "G1",
                "channel_id": "D1",
                "member": {"user": {"id": "U1"}},
                "data": {
                    "name": "ask",
                    "options": [{"name": "question", "value": "今日の予定は？"}],
                },
            }
        )
        self.assertEqual(payload["team_id"], "G1")
        self.assertEqual(payload["event"]["channel"], "D1")
        self.assertEqual(payload["event"]["text"], "/ask 今日の予定は？")

    def test_normalize_discord_interaction_mode(self) -> None:
        payload = normalize_discord_interaction(
            {
                "id": "i-mode",
                "guild_id": "G1",
                "channel_id": "D1",
                "member": {"user": {"id": "U1"}},
                "data": {
                    "name": "mode",
                    "options": [{"name": "value", "value": "detailed"}],
                },
            }
        )
        self.assertEqual(payload["event"]["text"], "/mode detailed")

    def test_handle_discord_ping(self) -> None:
        response = _signed_discord_interaction({"type": 1})
        self.assertEqual(response["type"], 1)

    def test_handle_discord_interaction_remind(self) -> None:
        interaction_id = f"i-{uuid4()}"
        response = _signed_discord_interaction(
            {
                "type": 2,
                "id": interaction_id,
                "guild_id": "G1",
                "channel_id": "D1",
                "member": {"user": {"id": "U1"}},
                "data": {
                    "name": "remind",
                    "options": [
                        {"name": "datetime", "value": "2026-03-01T09:00"},
                        {"name": "message", "value": "資料提出"},
                    ],
                },
            }
        )
        self.assertEqual(response["type"], 4)
        self.assertEqual(response["status"], "completed")

    def test_handle_discord_interaction_rejects_invalid_signature(self) -> None:
        with patch("gateway.inbound.verify_discord_signature", side_effect=DiscordSignatureVerificationError("bad")):
            response = handle_discord_interaction(
                {
                    "type": 1,
                },
                headers={
                    "X-Signature-Ed25519": "00",
                    "X-Signature-Timestamp": "1700000000",
                },
                raw_body=b'{"type":1}',
                public_key="a" * 64,
            )
        self.assertEqual(response["status"], "unauthorized")

    def test_handle_discord_duplicate_is_skipped(self) -> None:
        interaction_id = f"discord-dup-{uuid4()}"
        payload = {
            "type": 2,
            "id": interaction_id,
            "guild_id": "G1",
            "channel_id": "D1",
            "member": {"user": {"id": "U1"}},
            "data": {
                "name": "ask",
                "options": [{"name": "question", "value": "hello"}],
            },
        }
        first = _signed_discord_interaction(payload)
        second = _signed_discord_interaction(payload)
        self.assertEqual(first["status"], "completed")
        self.assertEqual(second["status"], "duplicate")


if __name__ == "__main__":
    unittest.main()
