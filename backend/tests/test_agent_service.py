from datetime import datetime, timezone

from agent.models import InboundEvent
from agent.service import AgentService


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


def test_ask_command_is_accepted() -> None:
    service = AgentService()
    response = service.handle_message(build_event("/ask 今日の予定は？"))
    assert response.status == "accepted"
    assert "回答を準備" in response.text


def test_remind_command_registers_reminder() -> None:
    service = AgentService()
    response = service.handle_message(build_event("/remind 2026-03-01T09:00 資料提出"))
    assert response.status == "completed"
    assert "reminder_id" in response.metadata


def test_remind_all_requires_confirmation() -> None:
    service = AgentService()
    response = service.handle_message(build_event("/remind 2026-03-01T09:00 all hands"))
    assert response.status == "needs_confirmation"

    confirmation_id = response.metadata["confirmation_id"]
    approved = service.confirm_action(confirmation_id, True)
    assert approved.status == "completed"
    assert "reminder_id" in approved.metadata
