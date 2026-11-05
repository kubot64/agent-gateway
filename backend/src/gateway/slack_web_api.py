from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib import error, request


class SlackAPIError(RuntimeError):
    pass


@dataclass
class SlackWebAPIClient:
    bot_token: str
    timeout_seconds: int = 10

    def post_message(self, *, channel: str, text: str, thread_ts: str | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {"channel": channel, "text": text}
        if thread_ts:
            payload["thread_ts"] = thread_ts

        req = request.Request(
            url="https://slack.com/api/chat.postMessage",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.bot_token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, error.HTTPError, TimeoutError, OSError) as exc:
            raise SlackAPIError(f"Slack API request failed: {exc}") from exc

        if not bool(data.get("ok", False)):
            raise SlackAPIError(f"Slack API error: {data.get('error', 'unknown_error')}")
        return data
