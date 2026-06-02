from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.common.config import settings

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import InMemorySaver
try:
    from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
except ImportError:  # pragma: no cover - optional production dependency
    AsyncPostgresSaver = None


def _postgres_uri(raw: str | None) -> str:
    uri = raw or settings.DATABASE_URL
    return (
        uri.replace("postgresql+asyncpg://", "postgresql://", 1)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )

@asynccontextmanager
async def checkpointer_context() -> AsyncIterator[Any | None]:
    backend = (settings.LANGGRAPH_CHECKPOINT_BACKEND or "sqlite").lower()

    if backend in {"none", "off", "disable", "disabled"}:
        yield None
        return

    if backend in {"memory", "inmemory"}:
        saver: InMemorySaver | None = None
        try:
            saver = InMemorySaver()
            yield saver
        finally:
            saver = None
        return

    if backend in {"postgres", "postgresql", "pg"}:
        if AsyncPostgresSaver is None:
            raise RuntimeError(
                "LANGGRAPH_CHECKPOINT_BACKEND=postgres requires langgraph-checkpoint-postgres"
            )
        async with AsyncPostgresSaver.from_conn_string(
            _postgres_uri(settings.LANGGRAPH_CHECKPOINT_DB or settings.DATABASE_URL)
        ) as saver:
            if hasattr(saver, "setup"):
                maybe_setup = saver.setup()
                if inspect.isawaitable(maybe_setup):
                    await maybe_setup
            yield saver
        return

    if AsyncSqliteSaver:
        async with AsyncSqliteSaver.from_conn_string(settings.LANGGRAPH_CHECKPOINT_DB) as saver:
            if hasattr(saver, "setup"):
                maybe_setup = saver.setup()
                if inspect.isawaitable(maybe_setup):
                    await maybe_setup
            yield saver
        return

    if SqliteSaver:
        with SqliteSaver.from_conn_string(settings.LANGGRAPH_CHECKPOINT_DB) as saver:
            if hasattr(saver, "setup"):
                maybe_setup = saver.setup()
                if inspect.isawaitable(maybe_setup):
                    await maybe_setup
            yield saver
        return

    yield None
