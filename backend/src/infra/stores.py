from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, ContextManager
from uuid import uuid4

from app.models import ConversationMessage, Reminder


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
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_pk INTEGER PRIMARY KEY AUTOINCREMENT,
                    platform TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    thread_id TEXT,
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    platform TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (platform, workspace_id, user_id)
                )
                """
            )

    def health_check(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1").fetchone()
            return True
        except sqlite3.Error:
            return False


class PostgresDatabase:
    def __init__(self, dsn: str) -> None:
        self.dsn = dsn
        self._ensure_schema()

    @contextmanager
    def connect(self) -> ContextManager[Any]:
        conn = _psycopg_connect(self.dsn)
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
                    remind_at TIMESTAMPTZ NOT NULL,
                    message TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    log_id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL,
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
                    created_at TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS command_metrics (
                    metric_id TEXT PRIMARY KEY,
                    created_at TIMESTAMPTZ NOT NULL,
                    platform TEXT,
                    intent TEXT,
                    status TEXT NOT NULL,
                    duration_ms INTEGER NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    message_pk BIGSERIAL PRIMARY KEY,
                    platform TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    thread_id TEXT,
                    user_id TEXT NOT NULL,
                    message_id TEXT NOT NULL,
                    text TEXT NOT NULL,
                    ts TIMESTAMPTZ NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_preferences (
                    platform TEXT NOT NULL,
                    workspace_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL,
                    PRIMARY KEY (platform, workspace_id, user_id)
                )
                """
            )

    def health_check(self) -> bool:
        try:
            with self.connect() as conn:
                conn.execute("SELECT 1")
            return True
        except Exception:
            return False


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

    def health_check(self) -> bool:
        return self.database.health_check()


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

    def summary(self) -> dict[str, object]:
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
            by_intent_rows = conn.execute(
                """
                SELECT COALESCE(intent, 'unknown') AS intent, COUNT(*)
                FROM command_metrics
                GROUP BY COALESCE(intent, 'unknown')
                """
            ).fetchall()
            by_status_rows = conn.execute(
                """
                SELECT status, COUNT(*)
                FROM command_metrics
                GROUP BY status
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
            "by_intent": {str(row[0]): int(row[1]) for row in by_intent_rows},
            "by_status": {str(row[0]): int(row[1]) for row in by_status_rows},
        }


class SQLiteConversationStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def append(self, message: ConversationMessage) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_messages (
                    platform, workspace_id, channel_id, thread_id, user_id, message_id, text, ts
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message.platform,
                    message.workspace_id,
                    message.channel_id,
                    message.thread_id,
                    message.user_id,
                    message.message_id,
                    message.text,
                    message.ts.astimezone(timezone.utc).isoformat(),
                ),
            )

    def recent(
        self,
        *,
        platform: str,
        workspace_id: str,
        channel_id: str,
        thread_id: str | None,
        limit: int = 20,
    ) -> list[ConversationMessage]:
        with self.database.connect() as conn:
            if thread_id is None:
                rows = conn.execute(
                    """
                    SELECT platform, workspace_id, channel_id, thread_id, user_id, message_id, text, ts
                    FROM conversation_messages
                    WHERE platform = ? AND workspace_id = ? AND channel_id = ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    (platform, workspace_id, channel_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT platform, workspace_id, channel_id, thread_id, user_id, message_id, text, ts
                    FROM conversation_messages
                    WHERE platform = ? AND workspace_id = ? AND channel_id = ? AND thread_id = ?
                    ORDER BY ts DESC
                    LIMIT ?
                    """,
                    (platform, workspace_id, channel_id, thread_id, limit),
                ).fetchall()
        rows = list(reversed(rows))
        return [
            ConversationMessage(
                platform=str(r[0]),
                workspace_id=str(r[1]),
                channel_id=str(r[2]),
                thread_id=str(r[3]) if r[3] is not None else None,
                user_id=str(r[4]),
                message_id=str(r[5]),
                text=str(r[6]),
                ts=datetime.fromisoformat(str(r[7])),
            )
            for r in rows
        ]


class SQLiteUserPreferenceStore:
    def __init__(self, database: SQLiteDatabase) -> None:
        self.database = database

    def get_mode(self, *, platform: str, workspace_id: str, user_id: str) -> str:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT mode
                FROM user_preferences
                WHERE platform = ? AND workspace_id = ? AND user_id = ?
                """,
                (platform, workspace_id, user_id),
            ).fetchone()
        return str(row[0]) if row else "brief"

    def set_mode(self, *, platform: str, workspace_id: str, user_id: str, mode: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (
                    platform, workspace_id, user_id, mode, updated_at
                ) VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(platform, workspace_id, user_id)
                DO UPDATE SET mode = excluded.mode, updated_at = excluded.updated_at
                """,
                (
                    platform,
                    workspace_id,
                    user_id,
                    mode,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )


class PostgresReminderStore:
    def __init__(self, database: PostgresDatabase) -> None:
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
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    reminder.reminder_id,
                    reminder.platform,
                    reminder.channel_id,
                    reminder.user_id,
                    reminder.remind_at.astimezone(timezone.utc),
                    reminder.message,
                    reminder.created_at.astimezone(timezone.utc),
                ),
            )
        return reminder

    def get(self, reminder_id: str) -> Reminder | None:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT reminder_id, platform, channel_id, user_id, remind_at, message, created_at
                FROM reminders
                WHERE reminder_id = %s
                """,
                (reminder_id,),
            ).fetchone()
        if row is None:
            return None
        return Reminder(
            reminder_id=str(row[0]),
            platform=str(row[1]),
            channel_id=str(row[2]),
            user_id=str(row[3]),
            remind_at=row[4],
            message=str(row[5]),
            created_at=row[6],
        )


class PostgresAuditLogStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def log(self, *, action: str, status: str, platform: str | None, user_id: str | None, details: str) -> str:
        log_id = str(uuid4())
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs (
                    log_id, created_at, action, status, platform, user_id, details
                ) VALUES (%s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    log_id,
                    datetime.now(timezone.utc),
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


class PostgresIdempotencyStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def mark_processed(self, idempotency_key: str) -> bool:
        with self.database.connect() as conn:
            cursor = conn.execute(
                """
                INSERT INTO inbound_events (
                    idempotency_key, created_at
                ) VALUES (%s, %s)
                ON CONFLICT (idempotency_key) DO NOTHING
                """,
                (idempotency_key, datetime.now(timezone.utc)),
            )
            return int(cursor.rowcount) > 0

    def health_check(self) -> bool:
        return self.database.health_check()


class PostgresCommandMetricStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def record(self, *, platform: str | None, intent: str | None, status: str, duration_ms: int) -> str:
        metric_id = str(uuid4())
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO command_metrics (
                    metric_id, created_at, platform, intent, status, duration_ms
                ) VALUES (%s, %s, %s, %s, %s, %s)
                """,
                (
                    metric_id,
                    datetime.now(timezone.utc),
                    platform,
                    intent,
                    status,
                    duration_ms,
                ),
            )
        return metric_id

    def summary(self) -> dict[str, object]:
        with self.database.connect() as conn:
            total = conn.execute("SELECT COUNT(*) FROM command_metrics").fetchone()
            errors = conn.execute(
                """
                SELECT COUNT(*) FROM command_metrics
                WHERE status IN ('integration_error', 'validation_error', 'failed', 'unauthorized')
                """
            ).fetchone()
            avg = conn.execute("SELECT AVG(duration_ms) FROM command_metrics").fetchone()
            p95 = conn.execute(
                """
                SELECT COALESCE(PERCENTILE_CONT(0.95) WITHIN GROUP (ORDER BY duration_ms), 0)
                FROM command_metrics
                """
            ).fetchone()
            by_intent_rows = conn.execute(
                """
                SELECT COALESCE(intent, 'unknown') AS intent, COUNT(*)
                FROM command_metrics
                GROUP BY COALESCE(intent, 'unknown')
                """
            ).fetchall()
            by_status_rows = conn.execute(
                """
                SELECT status, COUNT(*)
                FROM command_metrics
                GROUP BY status
                """
            ).fetchall()

        return {
            "total": int(total[0] if total else 0),
            "errors": int(errors[0] if errors else 0),
            "avg_duration_ms": int(avg[0] if avg and avg[0] is not None else 0),
            "p95_duration_ms": int(p95[0] if p95 and p95[0] is not None else 0),
            "by_intent": {str(row[0]): int(row[1]) for row in by_intent_rows},
            "by_status": {str(row[0]): int(row[1]) for row in by_status_rows},
        }


class PostgresConversationStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def append(self, message: ConversationMessage) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_messages (
                    platform, workspace_id, channel_id, thread_id, user_id, message_id, text, ts
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    message.platform,
                    message.workspace_id,
                    message.channel_id,
                    message.thread_id,
                    message.user_id,
                    message.message_id,
                    message.text,
                    message.ts.astimezone(timezone.utc),
                ),
            )

    def recent(
        self,
        *,
        platform: str,
        workspace_id: str,
        channel_id: str,
        thread_id: str | None,
        limit: int = 20,
    ) -> list[ConversationMessage]:
        with self.database.connect() as conn:
            if thread_id is None:
                rows = conn.execute(
                    """
                    SELECT platform, workspace_id, channel_id, thread_id, user_id, message_id, text, ts
                    FROM conversation_messages
                    WHERE platform = %s AND workspace_id = %s AND channel_id = %s
                    ORDER BY ts DESC
                    LIMIT %s
                    """,
                    (platform, workspace_id, channel_id, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT platform, workspace_id, channel_id, thread_id, user_id, message_id, text, ts
                    FROM conversation_messages
                    WHERE platform = %s AND workspace_id = %s AND channel_id = %s AND thread_id = %s
                    ORDER BY ts DESC
                    LIMIT %s
                    """,
                    (platform, workspace_id, channel_id, thread_id, limit),
                ).fetchall()
        rows = list(reversed(rows))
        return [
            ConversationMessage(
                platform=str(r[0]),
                workspace_id=str(r[1]),
                channel_id=str(r[2]),
                thread_id=str(r[3]) if r[3] is not None else None,
                user_id=str(r[4]),
                message_id=str(r[5]),
                text=str(r[6]),
                ts=r[7],
            )
            for r in rows
        ]


class PostgresUserPreferenceStore:
    def __init__(self, database: PostgresDatabase) -> None:
        self.database = database

    def get_mode(self, *, platform: str, workspace_id: str, user_id: str) -> str:
        with self.database.connect() as conn:
            row = conn.execute(
                """
                SELECT mode
                FROM user_preferences
                WHERE platform = %s AND workspace_id = %s AND user_id = %s
                """,
                (platform, workspace_id, user_id),
            ).fetchone()
        return str(row[0]) if row else "brief"

    def set_mode(self, *, platform: str, workspace_id: str, user_id: str, mode: str) -> None:
        with self.database.connect() as conn:
            conn.execute(
                """
                INSERT INTO user_preferences (
                    platform, workspace_id, user_id, mode, updated_at
                ) VALUES (%s, %s, %s, %s, %s)
                ON CONFLICT (platform, workspace_id, user_id)
                DO UPDATE SET mode = EXCLUDED.mode, updated_at = EXCLUDED.updated_at
                """,
                (
                    platform,
                    workspace_id,
                    user_id,
                    mode,
                    datetime.now(timezone.utc),
                ),
            )


class RedisIdempotencyStore:
    def __init__(self, *, redis_url: str, prefix: str = "agent:idempotency:", ttl_seconds: int = 24 * 60 * 60) -> None:
        self.redis_url = redis_url
        self.prefix = prefix
        self.ttl_seconds = ttl_seconds
        self.client = _redis_from_url(redis_url)

    def mark_processed(self, idempotency_key: str) -> bool:
        key = f"{self.prefix}{idempotency_key}"
        result = self.client.set(key, "1", ex=self.ttl_seconds, nx=True)
        return bool(result)

    def health_check(self) -> bool:
        try:
            return bool(self.client.ping())
        except Exception:
            return False


class CompositeIdempotencyStore:
    def __init__(self, *, primary: Any, fallback: Any) -> None:
        self.primary = primary
        self.fallback = fallback

    def mark_processed(self, idempotency_key: str) -> bool:
        try:
            return bool(self.primary.mark_processed(idempotency_key))
        except Exception:
            return bool(self.fallback.mark_processed(idempotency_key))

    def health_check(self) -> bool:
        primary_check = getattr(self.primary, "health_check", None)
        fallback_check = getattr(self.fallback, "health_check", None)
        primary_ok = bool(primary_check()) if callable(primary_check) else True
        fallback_ok = bool(fallback_check()) if callable(fallback_check) else True
        return primary_ok and fallback_ok


def _psycopg_connect(dsn: str) -> Any:
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("psycopg is required for PostgreSQL backend") from exc
    return psycopg.connect(dsn)


def _redis_from_url(redis_url: str) -> Any:
    try:
        import redis
    except ImportError as exc:
        raise RuntimeError("redis package is required for Redis idempotency backend") from exc
    return redis.Redis.from_url(redis_url)
