from __future__ import annotations

from datetime import datetime, timedelta, timezone

from .models import Command, Intent


class CommandParser:
    """Parse slash commands and simple natural-language reminders."""

    def parse(self, text: str) -> Command:
        cleaned = text.strip()
        if not cleaned:
            return Command(intent=Intent.UNKNOWN)

        lowered = cleaned.lower()
        if lowered.startswith("/ask"):
            question = cleaned[4:].strip()
            return Command(intent=Intent.ASK, payload={"question": question})

        if lowered.startswith("/summarize"):
            target = cleaned[len("/summarize") :].strip() or "current_thread"
            return Command(intent=Intent.SUMMARIZE, payload={"target": target})

        if lowered.startswith("/remind"):
            return self._parse_remind_payload(cleaned[len("/remind") :].strip())

        if lowered.startswith("/task"):
            return self._parse_task_payload(cleaned[len("/task") :].strip())

        if lowered.startswith("/mode"):
            return self._parse_mode_payload(cleaned[len("/mode") :].strip())

        # very small natural language rule for kickoff implementation
        if "リマインド" in cleaned and "明日" in cleaned:
            remind_at = datetime.now(timezone.utc) + timedelta(days=1)
            remind_at = remind_at.replace(hour=0, minute=0, second=0, microsecond=0)
            return Command(
                intent=Intent.REMIND,
                payload={"remind_at": remind_at, "message": cleaned},
                requires_confirmation=False,
            )

        return Command(intent=Intent.UNKNOWN)

    def _parse_remind_payload(self, payload: str) -> Command:
        """
        Supported format for MVP:
        /remind YYYY-MM-DDTHH:MM message...
        """
        if not payload:
            return Command(intent=Intent.REMIND, payload={}, requires_confirmation=False)

        head, *rest = payload.split(" ", 1)
        message = rest[0].strip() if rest else ""

        try:
            remind_at = datetime.fromisoformat(head)
            if remind_at.tzinfo is None:
                remind_at = remind_at.replace(tzinfo=timezone.utc)
        except ValueError:
            return Command(intent=Intent.REMIND, payload={"raw": payload}, requires_confirmation=False)

        requires_confirmation = "all" in message.lower() or "全員" in message
        return Command(
            intent=Intent.REMIND,
            payload={"remind_at": remind_at, "message": message},
            requires_confirmation=requires_confirmation,
        )

    def _parse_task_payload(self, payload: str) -> Command:
        """
        Supported format for MVP:
        /task add task title...
        """
        cleaned = payload.strip()
        if not cleaned:
            return Command(intent=Intent.TASK, payload={})

        lowered = cleaned.lower()
        if lowered.startswith("add "):
            title = cleaned[4:].strip()
            return Command(intent=Intent.TASK, payload={"action": "add", "title": title})

        return Command(intent=Intent.TASK, payload={"raw": cleaned})

    def _parse_mode_payload(self, payload: str) -> Command:
        mode = payload.strip().lower()
        if mode in {"brief", "detailed"}:
            return Command(intent=Intent.MODE, payload={"mode": mode})
        return Command(intent=Intent.MODE, payload={"raw": payload.strip()})
