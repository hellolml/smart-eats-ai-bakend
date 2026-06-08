from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.common.config import settings
from app.infra.models.base import Base


def eval_database_url() -> str:
    return os.getenv("EVAL_DATABASE_URL") or settings.EVAL_DATABASE_URL or settings.DATABASE_URL


def _create_eval_engine(database_url: str):
    engine_kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite+aiosqlite"):
        engine_kwargs["connect_args"] = {"timeout": 30}
        if database_url in {"sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://"}:
            engine_kwargs["poolclass"] = StaticPool
        else:
            engine_kwargs["poolclass"] = NullPool
        engine = create_async_engine(database_url, **engine_kwargs)
        _register_sqlite_pragmas(engine)
        return engine
    engine_kwargs["poolclass"] = NullPool
    return create_async_engine(database_url, **engine_kwargs)


def _register_sqlite_pragmas(engine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()


_engine_cache: dict[str, Any] = {}
_session_factory_cache: dict[str, async_sessionmaker[AsyncSession]] = {}
_initialized_urls: set[str] = set()


def _session_factory_for_url(database_url: str) -> async_sessionmaker[AsyncSession]:
    if database_url not in _session_factory_cache:
        engine = _create_eval_engine(database_url)
        _engine_cache[database_url] = engine
        _session_factory_cache[database_url] = async_sessionmaker(engine, expire_on_commit=False)
    return _session_factory_cache[database_url]


@asynccontextmanager
async def eval_session() -> AsyncIterator[AsyncSession]:
    """Open a session for eval/monitoring data.

    When the eval DB URL equals the application DB URL, reuse the application
    session factory so test and local SQLite schemas stay consistent.
    """
    database_url = eval_database_url()
    if database_url not in _initialized_urls:
        await init_eval_db()
    if database_url == settings.DATABASE_URL:
        from app.infra.db import AsyncSessionLocal

        async with AsyncSessionLocal() as session:
            yield session
        return

    factory = _session_factory_for_url(database_url)
    async with factory() as session:
        yield session


async def init_eval_db() -> None:
    """Create eval platform tables on the configured eval database."""
    from app.infra.models import eval as _eval_models  # noqa: F401

    database_url = eval_database_url()
    if database_url == settings.DATABASE_URL:
        from app.infra.db import engine as app_engine
        from app.infra.db import init_db

        await init_db()
        async with app_engine.begin() as conn:
            await _ensure_eval_schema_columns(conn, database_url)
        _initialized_urls.add(database_url)
        return

    _session_factory_for_url(database_url)
    engine = _engine_cache[database_url]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_eval_schema_columns(conn, database_url)
    _initialized_urls.add(database_url)


async def close_eval_db() -> None:
    for engine in list(_engine_cache.values()):
        await engine.dispose()
    _engine_cache.clear()
    _session_factory_cache.clear()
    _initialized_urls.clear()


def _dialect(database_url: str) -> str:
    if database_url.startswith("postgresql"):
        return "postgresql"
    if database_url.startswith("sqlite+aiosqlite"):
        return "sqlite"
    return ""


def _json_type(dialect: str) -> str:
    return "JSONB" if dialect == "postgresql" else "JSON"


def _bool_type(dialect: str, *, default: bool | None = None) -> str:
    if default is None:
        return "BOOLEAN"
    if default is False:
        return "BOOLEAN DEFAULT FALSE" if dialect == "postgresql" else "BOOLEAN DEFAULT 0"
    return "BOOLEAN DEFAULT TRUE" if dialect == "postgresql" else "BOOLEAN DEFAULT 1"


def _timestamp_type(dialect: str) -> str:
    return "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"


def _eval_column_plan(dialect: str) -> dict[str, list[tuple[str, str]]]:
    json_type = _json_type(dialect)
    timestamp_type = _timestamp_type(dialect)
    return {
        "eval_runs": [
            ("baseline_pin", "VARCHAR(255)"),
            ("tags_json", json_type),
            ("notes", "TEXT"),
            ("owner", "VARCHAR(255)"),
            ("release_marker", "VARCHAR(64)"),
        ],
        "conversation_human_reviews": [
            ("failure_reason", "VARCHAR(255)"),
            ("failure_tags_json", json_type),
            ("corrected_answer", "TEXT"),
            ("expected_behavior", "TEXT"),
            ("review_confidence", "FLOAT"),
            ("dataset_candidate", _bool_type(dialect, default=False)),
        ],
        "conversation_costs": [
            ("cached_tokens", "INTEGER DEFAULT 0"),
            ("reasoning_tokens", "INTEGER DEFAULT 0"),
            ("total_tokens", "INTEGER DEFAULT 0"),
            ("provider", "VARCHAR(128)"),
            ("model_name", "VARCHAR(255)"),
            ("cost_estimated", _bool_type(dialect, default=False)),
            ("pricing_json", json_type),
        ],
        "evaluation_alerts": [
            ("notification_sent", _bool_type(dialect, default=False)),
            ("notification_sent_at", timestamp_type),
            ("notification_error", "VARCHAR(512)"),
        ],
    }


async def _ensure_eval_schema_columns(conn, database_url: str) -> None:
    dialect = _dialect(database_url)
    if dialect not in {"postgresql", "sqlite"}:
        return
    for table, columns in _eval_column_plan(dialect).items():
        if not await _table_exists(conn, dialect, table):
            continue
        existing = await _table_columns(conn, dialect, table)
        for column, column_type in columns:
            if column in existing:
                continue
            sql = (
                f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}"
                if dialect == "postgresql"
                else f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
            )
            await conn.exec_driver_sql(sql)


async def _table_exists(conn, dialect: str, table: str) -> bool:
    if dialect == "postgresql":
        result = await conn.exec_driver_sql(f"SELECT to_regclass('{table}')")
        return result.scalar() is not None
    result = await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return result.scalar() is not None


async def _table_columns(conn, dialect: str, table: str) -> set[str]:
    if dialect == "postgresql":
        result = await conn.exec_driver_sql(
            f"""
            SELECT column_name
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = '{table}'
            """
        )
        return {str(row[0]) for row in result.fetchall()}
    result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
    return {str(row[1]) for row in result.fetchall()}
