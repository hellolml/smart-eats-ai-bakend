from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool, NullPool
from sqlalchemy import event

from app.common.config import settings
from app.infra.models.base import Base


def _create_engine():
    url = settings.DATABASE_URL
    if url.startswith("sqlite+aiosqlite") and url.endswith(":memory:"):
        return create_async_engine(
            url,
            echo=False,
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
    if url.startswith("sqlite+aiosqlite"):
        engine = create_async_engine(
            url,
            pool_pre_ping=True,
            echo=False,
            connect_args={"timeout": 30},
            poolclass=NullPool,
        )
        _register_sqlite_pragmas(engine)
        return engine
    return create_async_engine(url, pool_pre_ping=True, echo=False)


def _register_sqlite_pragmas(engine) -> None:
    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_connection, _connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL;")
        cursor.execute("PRAGMA busy_timeout=30000;")
        cursor.close()


engine = _create_engine()
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


async def init_db() -> None:
    from app.infra.models import (
        auth,
        chat,
        context,
        fridge,
        game,
        preference,
        recipe,
        restaurant,
        user,
    )  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        if settings.DATABASE_URL.startswith("sqlite+aiosqlite"):
            await _ensure_sqlite_columns(conn)


async def _ensure_sqlite_columns(conn) -> None:
    result = await conn.exec_driver_sql("PRAGMA table_info(chat_sessions)")
    cols = {row[1] for row in result.fetchall()}
    if "title" not in cols:
        await conn.exec_driver_sql("ALTER TABLE chat_sessions ADD COLUMN title VARCHAR(255)")
    if "deleted_at" not in cols:
        await conn.exec_driver_sql("ALTER TABLE chat_sessions ADD COLUMN deleted_at DATETIME")

    await conn.exec_driver_sql("DROP TABLE IF EXISTS chat_checkpoints")
