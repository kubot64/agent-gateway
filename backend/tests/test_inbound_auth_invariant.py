"""HTTP の入口すべてについて、認証の不変条件を確かめる。

`gateway.main.app` の入口を、Mount と Host の先も含めて (メソッド, パス) 単位で回し、どの入口も下の分類表のどれかに入っていることを確かめる。
署名が必須の入口は、鍵が未設定なら署名の有無にかかわらず 401 を返し、鍵があっても署名がなければ 401 を返す。
入口を足したら分類表に足さない限りテストが落ちるので、新しい入口も自動で検査の対象になる。
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass
from typing import Callable, Sequence
from urllib.parse import urlencode
from uuid import uuid4

from starlette.routing import BaseRoute, Host, Mount

from gateway.main import app
from tests.test_agent_service import (
    DISCORD_TEST_PUBLIC_KEY,
    SLACK_TEST_SECRET,
    _discord_signed_headers,
    _post,
    _slack_signed_headers,
    _temporary_env,
)

Entry = tuple[str, str]  # (メソッド, パス)


@dataclass(frozen=True)
class SignedEntry:
    method: str
    path: str
    key_env: str
    key_value: str
    sign: Callable[[bytes], dict[str, str]]
    content_type: str
    body: bytes

    @property
    def entry(self) -> Entry:
        return (self.method, self.path)


def _json(payload: dict) -> bytes:
    return json.dumps(payload).encode("utf-8")


def _form(payload: dict) -> bytes:
    return urlencode(payload).encode("utf-8")


def _slack_sign(body: bytes) -> dict[str, str]:
    return _slack_signed_headers(SLACK_TEST_SECRET, body)


# 署名を検証してから処理する入口。
SIGNED_ENTRIES = [
    SignedEntry(
        method="POST",
        path="/api/inbound/slack/events",
        key_env="SLACK_SIGNING_SECRET",
        key_value=SLACK_TEST_SECRET,
        sign=_slack_sign,
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
        method="POST",
        path="/api/inbound/slack/commands",
        key_env="SLACK_SIGNING_SECRET",
        key_value=SLACK_TEST_SECRET,
        sign=_slack_sign,
        content_type="application/x-www-form-urlencoded",
        body=_form({"team_id": "T1", "channel_id": "C1", "user_id": "U1", "command": "/ask", "text": "hello"}),
    ),
    SignedEntry(
        method="POST",
        path="/api/inbound/slack/interactions",
        key_env="SLACK_SIGNING_SECRET",
        key_value=SLACK_TEST_SECRET,
        sign=_slack_sign,
        content_type="application/x-www-form-urlencoded",
        body=_form({"payload": json.dumps({"actions": [{"action_id": "confirm", "value": "unknown"}]})}),
    ),
    SignedEntry(
        method="POST",
        path="/api/inbound/discord/interactions",
        key_env="DISCORD_PUBLIC_KEY",
        key_value=DISCORD_TEST_PUBLIC_KEY,
        sign=_discord_signed_headers,
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
DIRECT_ENTRIES: dict[Entry, str] = {
    ("POST", "/api/agent/handle-message"): "ENABLE_DIRECT_HANDLE_MESSAGE",
}

# 認証なしで公開する入口。足すときは、なぜ公開してよいかを書く。
PUBLIC_ENTRIES: dict[Entry, str] = {
    ("GET", "/api/health"): "Cloud Run のヘルスチェック。DB と冪等性 store の状態だけを返す",
    ("GET", "/api/metrics"): "件数と所要時間の集計だけを返す。依頼の本文は含まない",
    ("GET", "/openapi.json"): "FastAPI が自動で足す API スキーマ",
    ("GET", "/docs"): "FastAPI が自動で足す API 文書",
    ("GET", "/docs/oauth2-redirect"): "FastAPI が自動で足す API 文書",
    ("GET", "/redoc"): "FastAPI が自動で足す API 文書",
}


# 中のルートを数えられない入口（素の ASGI アプリや静的ファイルの Mount、WebSocket など）は、このメソッド名で数える。
# 分類表に明示しない限りテストが落ちるので、検査をすり抜けない。
ANY_METHOD = "ANY"


def _route_entries(routes: Sequence[BaseRoute] | None = None, prefix: str = "") -> set[Entry]:
    entries: set[Entry] = set()
    for route in app.routes if routes is None else routes:
        path = prefix + getattr(route, "path", "")
        methods = set(getattr(route, "methods", None) or ())
        if methods:
            # Starlette は GET の入口に HEAD を自動で足す。HEAD は同じ処理で本文を返さないだけなので、GET の分類に含める。
            if "GET" in methods:
                methods.discard("HEAD")
            entries.update((method, path) for method in methods)
            continue
        if isinstance(route, Host):
            # Host の先は、同じパスでも別の入口になる。ホスト名を `//ホスト名/パス` の形でパスの前に付けて区別する。
            path = f"//{route.host}{prefix}"
        sub_routes = getattr(route, "routes", None) if isinstance(route, (Mount, Host)) else None
        if sub_routes:
            entries |= _route_entries(sub_routes, path)
        else:
            entries.add((ANY_METHOD, path or "/"))
    return entries


class InboundAuthInvariantTests(unittest.TestCase):
    def test_every_route_is_classified(self) -> None:
        classified = {e.entry for e in SIGNED_ENTRIES} | set(DIRECT_ENTRIES) | set(PUBLIC_ENTRIES)

        self.assertEqual(_route_entries() - classified, set(), "分類表にない入口がある。署名必須・直接・公開のどれかに足す")
        self.assertEqual(classified - _route_entries(), set(), "分類表に、もう存在しない入口が残っている")

    def test_signed_entries_reject_any_request_when_key_is_not_configured(self) -> None:
        for entry in SIGNED_ENTRIES:
            for label, headers in (("unsigned", None), ("signed", entry.sign(entry.body))):
                with self.subTest(entry=entry.entry, request=label):
                    with _temporary_env({entry.key_env: None, "SLACK_BOT_TOKEN": None}):
                        status, body = _post(entry.path, entry.body, entry.content_type, headers, method=entry.method)
                    self.assertEqual(status, 401)
                    self.assertEqual(body.get("status"), "unauthorized")

    def test_signed_entries_reject_unsigned_requests(self) -> None:
        for entry in SIGNED_ENTRIES:
            with self.subTest(entry=entry.entry):
                with _temporary_env({entry.key_env: entry.key_value, "SLACK_BOT_TOKEN": None}):
                    status, body = _post(entry.path, entry.body, entry.content_type, method=entry.method)
                self.assertEqual(status, 401)
                self.assertEqual(body.get("status"), "unauthorized")

    def test_direct_entries_are_closed_without_env(self) -> None:
        payload = _json({"platform": "api", "workspace_id": "W1", "channel_id": "C1", "user_id": "U1", "text": "/ask hi", "message_id": f"m-{uuid4()}"})
        for (method, path), env in DIRECT_ENTRIES.items():
            with self.subTest(entry=(method, path)):
                with _temporary_env({env: None}):
                    status, _ = _post(path, payload, "application/json", method=method)
                self.assertEqual(status, 404)


if __name__ == "__main__":
    unittest.main()
