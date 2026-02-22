from __future__ import annotations

from typing import Any, Mapping


def build_slack_response(agent_response: Mapping[str, Any]) -> dict[str, Any]:
    status = str(agent_response.get("status", "unknown"))
    text = str(agent_response.get("text", ""))
    metadata = dict(agent_response.get("metadata", {}) or {})

    response: dict[str, Any] = {
        "response_type": "ephemeral",
        "text": text,
        "metadata": metadata,
        "status": status,
    }

    if status == "needs_confirmation":
        confirmation_id = str(metadata.get("confirmation_id", "")).strip()
        if confirmation_id:
            response["blocks"] = _build_confirmation_blocks(text=text, confirmation_id=confirmation_id)

    return response


def _build_confirmation_blocks(*, text: str, confirmation_id: str) -> list[dict[str, Any]]:
    return [
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": text,
            },
        },
        {
            "type": "actions",
            "elements": [
                {
                    "type": "button",
                    "action_id": "confirm",
                    "text": {"type": "plain_text", "text": "Confirm"},
                    "style": "primary",
                    "value": confirmation_id,
                },
                {
                    "type": "button",
                    "action_id": "cancel",
                    "text": {"type": "plain_text", "text": "Cancel"},
                    "style": "danger",
                    "value": confirmation_id,
                },
            ],
        },
    ]
