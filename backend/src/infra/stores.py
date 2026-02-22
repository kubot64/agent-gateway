from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from agent.models import Reminder


class SQLiteDatabase:
    def __init__(self, db_path: str) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._ensure_schema()

    @contextmanager
    def connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def _ensure_schema(self) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS reminders (
                    reminder_id TEXT PRIMARY KEY,
                    platform TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    remind_at TEXT NOT NULL,
                    message TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    log_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    action TEXT NOT NULL,
                    status TEXT NOT NULL,
                    platform TEXT,
                    user_id TEXT,
                    details TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS inbound_events (
                    idempotency_key TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_metrics (
                    metric_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    platform TEXT,
                    intent TEXT,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL
                )
                """
            )


class SQLiteReminderStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def create(self, *, platform: str, channel_id: str, user_id: str, remind_at: datetime, message: str) -> Reminder:
        reminder = Reminder(
            reminder_id=str(uuid4()),
            platform=platform,
            channel_id=channel_id,
            user_id=user_id,
            remind_at=remind_at,
            message=message,
        )
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO reminders (
                    reminder_id, platform, channel_id, user_id, remind_at, message, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    reminder.reminder_id,
                    reminder.platform,
                    reminder.channel_id,
                    reminder.user_id,
                    reminder.remind_at.astimezone(timezone.utc).isoformat(),
                    reminder.message,
                    reminder.created_at.astimezone(timezone.utc).isoformat(),
                ),
            )
        return reminder

    def get(self, reminder_id: str) -> Reminder | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT reminder_id, platform, channel_id, user_id, remind_at, message, created_at
                FROM reminders
                WHERE reminder_id = ?
                """,
                (reminder_id,),
            ).fetchone()
        if row is None:
            return None
        remind_at = datetime.fromisoformat(str(row[4]))
        created_at = datetime.fromisoformat(str(row[6]))
        return Reminder(
            reminder_id=str(row[0]),
            platform=str(row[1]),
            channel_id=str(row[2]),
            user_id=str(row[3]),
            remind_at=remind_at,
            message=str(row[5]),
            created_at=created_at,
        )


class SQLiteAuditLogStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def log(self, *, action: str, status: str, platform: str | None, user_id: str | None, details: str) -> str:
        log_id = str(uuid4())
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    log_id, created_at, action, status, platform, user_id, details
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    log_id,
                    datetime.now(timezone.utc).isoformat(),
                    action,
                    status,
                    platform,
                    user_id,
                    details,
                ),
            )
        return log_id

    def count(self) -> int:
        with self.database.connect() as conn:
            row = conn.execute("SELECT COUNT(*) FROM audit_logs").fetchone()
        return int(row[0] if row else 0)


class SQLiteIdempotencyStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def mark_processed(self, idempotency_key: str) -> bool:
        try:
            with self.database.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO inbound_events (
                        idempotency_key, created_at
                    ) VALUES (?, ?)
                    """,
                    (
                        idempotency_key,
                        datetime.now(timezone.utc).isoformat(),
                    ),
                )
            return True
        except sqlite3.IntegrityError:
            return False


class SQLiteCommandMetricStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def record(self, *, platform: str | None, intent: str | None, status: str, duration_ms: int) -> str:
        metric_id = str(uuid4())
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO command_metrics (
                    metric_id, created_at, platform, intent, status, duration_ms
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    metric_id,
                    datetime.now(timezone.utc).isoformat(),
                    platform,
                    intent,
                    status,
                    duration_ms,
                ),
            )
        return metric_id

    def summary(self) -> dict[str, int]:
        with self.database.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM command_metrics").fetchone()
            errors = conn.execute(
                """
                SELECT COUNT(*) FROM command_metrics
                WHERE status IN ('integration_error', 'validation_error', 'failed', 'unauthorized')
                """
            ).fetchone()
            avg = conn.execute("SELECT AVG(duration_ms) FROM command_metrics").fetchone()
            p95_row = conn.execute(
                """
                SELECT duration_ms FROM command_metrics
                ORDER BY duration_ms
                """
            ).fetchall()

        durations = [int(row[0]) for row in p95_row]
        p95 = 0
        if durations:
            idx = max(0, int(len(durations) * 0.95) - 1)
            p95 = durations[idx]

        return {
            "total": int(total[0] if total else 0),
            "errors": int(errors[0] if errors else 0),
            "avg_duration_ms": int(avg[0] if avg and avg[0] is not None else 0),
            "p95_duration_ms": p95,
        }
