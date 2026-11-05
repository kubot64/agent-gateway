from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Protocol
from urllib import error, request


class TaskClientError(RuntimeError):
    pass


class TaskClient(Protocol):
    def create_task(self, *, title: str, description: str | None = None, creator_id: str | None = None) -> dict[str, str]:
        ...


@dataclass
class NullTaskClient:
    def create_task(self, *, title: str, description: str | None = None, creator_id: str | None = None) -> dict[str, str]:
        raise TaskClientError("Task integration is not configured. Set LINEAR_API_KEY and LINEAR_TEAM_ID.")


@dataclass
class LinearTaskClient:
    api_key: str
    team_id: str
    timeout_seconds: int = 15

    def create_task(self, *, title: str, description: str | None = None, creator_id: str | None = None) -> dict[str, str]:
        mutation = """
        mutation IssueCreate($input: IssueCreateInput!) {
          issueCreate(input: $input) {
            success
            issue {
              id
              identifier
              url
              title
            }
          }
        }
        """
        input_payload: dict[str, Any] = {
            "teamId": self.team_id,
            "title": title,
        }
        if description:
            input_payload["description"] = description

        payload = {
            "query": mutation,
            "variables": {"input": input_payload},
        }
        req = request.Request(
            url="https://api.linear.app/graphql",
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "Authorization": self.api_key,
                "Content-Type": "application/json",
            },
            method="POST",
        )
        try:
            with request.urlopen(req, timeout=self.timeout_seconds) as resp:
                body = json.loads(resp.read().decode("utf-8"))
        except (error.URLError, error.HTTPError, TimeoutError, OSError, ValueError) as exc:
            raise TaskClientError(f"Linear API request failed: {exc}") from exc

        if body.get("errors"):
            raise TaskClientError(f"Linear API error: {body['errors']}")

        issue_create = body.get("data", {}).get("issueCreate", {})
        if not bool(issue_create.get("success", False)):
            raise TaskClientError("Linear issue creation failed.")

        issue = issue_create.get("issue", {}) or {}
        task_id = str(issue.get("identifier") or issue.get("id") or "")
        task_url = str(issue.get("url") or "")
        return {
            "task_id": task_id,
            "url": task_url,
            "provider": "linear",
        }


def build_default_task_client() -> TaskClient:
    api_key = os.getenv("LINEAR_API_KEY", "").strip()
    team_id = os.getenv("LINEAR_TEAM_ID", "").strip()
    if api_key and team_id:
        return LinearTaskClient(api_key=api_key, team_id=team_id)
    return NullTaskClient()
