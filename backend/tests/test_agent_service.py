from __future__ import annotations

import hmac
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

from agent.llm import LocalFallbackLLMClient
from agent.models import InboundEvent
from agent.service import AgentService
from api.inbound import handle_slack_command, handle_slack_event
from api.security import verify_slack_signature
from api.slack_payloads import normalize_slash_command
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


if __name__ == "__main__":
    unittest.main()
