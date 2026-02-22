from __future__ import annotations

import hmac
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from adapters.slack.web_api import SlackAPIError, SlackWebAPIClient
from agent.llm import LocalFallbackLLMClient
from agent.models import InboundEvent
from agent.service import AgentService
from api.discord_payloads import normalize_discord_interaction
from api.discord_security import DiscordSignatureVerificationError, verify_discord_signature
from api.inbound import handle_discord_interaction, handle_slack_command, handle_slack_event, handle_slack_interaction
from api.security import verify_slack_signature
from api.slack_payloads import extract_confirmation_from_interaction, normalize_slash_command, parse_interaction_payload
from api.slack_responses import build_slack_response
from infra.stores import SQLiteAuditLogStore, SQLiteDatabase, SQLiteReminderStore


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
        response = handle_slack_command(
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
        pending = handle_slack_command(
            {
                "team_id": "T1",
                "channel_id": "C1",
                "user_id": "U1",
                "command": "/remind",
                "text": "2026-03-01T09:00 all hands",
            }
        )
        confirmation_id = pending["metadata"]["confirmation_id"]
        response = handle_slack_interaction(
            {
                "payload": json.dumps(
                    {
                        "actions": [
                            {
                                "action_id": "confirm",
                                "value": confirmation_id,
                            }
                        ]
                    }
                )
            }
        )
        self.assertEqual(response["status"], "completed")
        self.assertEqual(response["response_type"], "ephemeral")
        self.assertFalse("blocks" in response)

    def test_handle_slash_command_needs_confirmation_has_blocks(self) -> None:
        response = handle_slack_command(
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


class SlackWebAPITests(unittest.TestCase):
    def test_post_message_success(self) -> None:
        class FakeResponse:
            def __enter__(self) -> "FakeResponse":
                return self

            def __exit__(self, exc_type: object, exc: object, tb: object) -> bool:
                return False

            def read(self) -> bytes:
                return b'{"ok": true, "channel": "C1", "ts": "1.23"}'

        with patch("adapters.slack.web_api.request.urlopen", return_value=FakeResponse()) as mocked:
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

        with patch("adapters.slack.web_api.request.urlopen", return_value=FakeResponse()):
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

    def test_handle_discord_ping(self) -> None:
        response = handle_discord_interaction({"type": 1})
        self.assertEqual(response["type"], 1)

    def test_handle_discord_interaction_remind(self) -> None:
        response = handle_discord_interaction(
            {
                "type": 2,
                "id": "i2",
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
        with patch("api.inbound.verify_discord_signature", side_effect=DiscordSignatureVerificationError("bad")):
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


if __name__ == "__main__":
    unittest.main()
