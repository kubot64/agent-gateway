"""HTTP の入口すべてについて、認証の不変条件を確かめる。

`gateway.main.app` の入口を回し、どの入口も下の分類表のどれかに入っていることを確かめる。署名が必須の入口は、
鍵が未設定でも、署名がなくても 401 を返すことを確かめる。入口を足したら分類表に足さない限りテストが落ちるので、
新しい入口も自動で検査の対象になる。
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from urllib.parse import urlencode
from uuid import uuid4

from gateway.main import app
from tests.test_agent_service import DISCORD_TEST_PUBLIC_KEY, SLACK_TEST_SECRET, _post, _temporary_env


@dataclass(frozen=True)
class SignedEntry:
    path: str
    key_env: str
    key_value: str
    content_type: str
    body: bytes


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _form(payload: dict) -> bytes:
    return urlencode(payload).encode("utf-8")


# 署名を検証してから処理する入口。鍵が未設定でも、署名がなくても 401 を返す。
SIGNED_ENTRIES = [
    SignedEntry(
        path="/api/inbound/slack/events",
        key_env="SLACK_SIGNING_SECRET",
        key_value=SLACK_TEST_SECRET,
        content_type="application/json",
        body=_json(
            {
                "type": "event_callback",
                "event_id": "evt-invariant",
                "team_id": "T1",
                "event": {"type": "message", "ts": "1700000000", "channel": "C1", "user": "U1", "text": "/ask hello"},
            }
        ),
    ),
    SignedEntry(
        path="/api/inbound/slack/commands",
        key_env="SLACK_SIGNING_SECRET",
        key_value=SLACK_TEST_SECRET,
        content_type="application/x-www-form-urlencoded",
        body=_form({"team_id": "T1", "channel_id": "C1", "user_id": "U1", "command": "/ask", "text": "hello"}),
    ),
    SignedEntry(
        path="/api/inbound/slack/interactions",
        key_env="SLACK_SIGNING_SECRET",
        key_value=SLACK_TEST_SECRET,
        content_type="application/x-www-form-urlencoded",
        body=_form({"payload": json.dumps({"actions": [{"action_id": "confirm", "value": "unknown"}]})}),
    ),
    SignedEntry(
        path="/api/inbound/discord/interactions",
        key_env="DISCORD_PUBLIC_KEY",
        key_value=DISCORD_TEST_PUBLIC_KEY,
        content_type="application/json",
        body=_json(
            {
                "type": 2,
                "id": "i-invariant",
                "guild_id": "G1",
                "channel_id": "D1",
                "member": {"user": {"id": "U1"}},
                "data": {"name": "ask", "options": [{"name": "question", "value": "hello"}]},
            }
        ),
    ),
]

# ローカル開発用の直接入口。環境変数で有効にしない限り 404 を返す。
DIRECT_ENTRIES = {
    "/api/agent/handle-message": "ENABLE_DIRECT_HANDLE_MESSAGE",
}

# 認証なしで公開する入口。足すときは、なぜ公開してよいかを書く。
PUBLIC_ENTRIES = {
    "/api/health": "Cloud Run のヘルスチェック。DB と冪等性 store の状態だけを返す",
    "/api/metrics": "件数と所要時間の集計だけを返す。依頼の本文は含まない",
    # FastAPI が自動で足す API 文書。
    "/openapi.json": "FastAPI が自動で足す API スキーマ",
    "/docs": "FastAPI が自動で足す API 文書",
    "/docs/oauth2-redirect": "FastAPI が自動で足す API 文書",
    "/redoc": "FastAPI が自動で足す API 文書",
}


def _route_paths() -> set[str]:
    return {route.path for route in app.routes if getattr(route, "methods", None)}


class InboundAuthInvariantTests(unittest.TestCase):
    def test_every_route_is_classified(self) -> None:
        classified = {e.path for e in SIGNED_ENTRIES} | set(DIRECT_ENTRIES) | set(PUBLIC_ENTRIES)

        self.assertEqual(_route_paths() - classified, set(), "分類表にない入口がある。署名必須・直接・公開のどれかに足す")
        self.assertEqual(classified - _route_paths(), set(), "分類表に、もう存在しない入口が残っている")

    def test_signed_entries_reject_when_key_is_not_configured(self) -> None:
        for entry in SIGNED_ENTRIES:
            with self.subTest(path=entry.path):
                with _temporary_env({entry.key_env: None, "SLACK_BOT_TOKEN": None}):
                    status, body = _post(entry.path, entry.body, entry.content_type)
                self.assertEqual(status, 401)
                self.assertEqual(body.get("status"), "unauthorized")

    def test_signed_entries_reject_unsigned_requests(self) -> None:
        for entry in SIGNED_ENTRIES:
            with self.subTest(path=entry.path):
                with _temporary_env({entry.key_env: entry.key_value, "SLACK_BOT_TOKEN": None}):
                    status, body = _post(entry.path, entry.body, entry.content_type)
                self.assertEqual(status, 401)
                self.assertEqual(body.get("status"), "unauthorized")

    def test_direct_entries_are_closed_without_env(self) -> None:
        payload = _json({"platform": "api", "workspace_id": "W1", "channel_id": "C1", "user_id": "U1", "text": "/ask hi", "message_id": f"m-{uuid4()}"})
        for path, env in DIRECT_ENTRIES.items():
            with self.subTest(path=path):
                with _temporary_env({env: None}):
                    status, _ = _post(path, payload, "application/json")
                self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
