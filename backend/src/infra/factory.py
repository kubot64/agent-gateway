from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.service import AgentService
from infra.stores import (
    CompositeIdempotencyStore,
    PostgresAuditLogStore,
    PostgresCommandMetricStore,
    PostgresConversationStore,
    PostgresDatabase,
    PostgresIdempotencyStore,
    PostgresReminderStore,
    PostgresUserPreferenceStore,
    RedisIdempotencyStore,
    SQLiteAuditLogStore,
    SQLiteCommandMetricStore,
    SQLiteConversationStore,
    SQLiteDatabase,
    SQLiteIdempotencyStore,
    SQLiteReminderStore,
    SQLiteUserPreferenceStore,
)


@dataclass
class AppInfra:
    database: Any
    service: AgentService
    idempotency_store: Any


def build_infra(*, default_sqlite_path: str) -> AppInfra:
    db_backend = os.getenv("DATABASE_BACKEND", "sqlite").strip().lower()
    database: PostgresDatabase | SQLiteDatabase
    db_idempotency_store: PostgresIdempotencyStore | SQLiteIdempotencyStore
    idempotency_store: CompositeIdempotencyStore | PostgresIdempotencyStore | SQLiteIdempotencyStore
    if db_backend == "postgres":
        dsn = os.getenv("POSTGRES_DSN", "").strip()
        if not dsn:
            raise ValueError("POSTGRES_DSN is required when DATABASE_BACKEND=postgres")
        database = PostgresDatabase(dsn=dsn)
        service = AgentService(
            reminder_store=PostgresReminderStore(database),
            audit_store=PostgresAuditLogStore(database),
            metric_store=PostgresCommandMetricStore(database),
            conversation_store=PostgresConversationStore(database),
            preference_store=PostgresUserPreferenceStore(database),
        )
        db_idempotency_store = PostgresIdempotencyStore(database)
    else:
        sqlite_path = os.getenv("SQLITE_PATH", "").strip() or default_sqlite_path
        database = SQLiteDatabase(db_path=sqlite_path)
        service = AgentService(
            reminder_store=SQLiteReminderStore(database),
            audit_store=SQLiteAuditLogStore(database),
            metric_store=SQLiteCommandMetricStore(database),
            conversation_store=SQLiteConversationStore(database),
            preference_store=SQLiteUserPreferenceStore(database),
        )
        db_idempotency_store = SQLiteIdempotencyStore(database)

    idempotency_backend = os.getenv("IDEMPOTENCY_BACKEND", db_backend).strip().lower()
    if idempotency_backend == "redis":
        redis_url = os.getenv("REDIS_URL", "").strip()
        if not redis_url:
            raise ValueError("REDIS_URL is required when IDEMPOTENCY_BACKEND=redis")
        ttl_seconds = int(os.getenv("IDEMPOTENCY_TTL_SECONDS", "86400").strip() or "86400")
        prefix = os.getenv("IDEMPOTENCY_KEY_PREFIX", "agent:idempotency:").strip() or "agent:idempotency:"
        redis_store = RedisIdempotencyStore(redis_url=redis_url, ttl_seconds=ttl_seconds, prefix=prefix)
        idempotency_store = CompositeIdempotencyStore(primary=redis_store, fallback=db_idempotency_store)
    else:
        idempotency_store = db_idempotency_store

    return AppInfra(
        database=database,
        service=service,
        idempotency_store=idempotency_store,
    )


def default_sqlite_path_from_file(file_path: str) -> str:
    backend_dir = Path(file_path).resolve().parents[2]
    return str(backend_dir / "data" / "agent.db")
