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
        from app.infra.db import init_db

        await init_db()
        _initialized_urls.add(database_url)
        return

    _session_factory_for_url(database_url)
    engine = _engine_cache[database_url]
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    _initialized_urls.add(database_url)
