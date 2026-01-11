from __future__ import annotations

from typing import AsyncGenerator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
    return create_async_engine(url, pool_pre_ping=True, echo=False)


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
