from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.common.config import settings

try:
    from langgraph.store.memory import InMemoryStore
except ImportError:  # pragma: no cover
    InMemoryStore = None

try:
    from langgraph.store.postgres.aio import AsyncPostgresStore
except ImportError:  # pragma: no cover - optional production dependency
    AsyncPostgresStore = None


def _postgres_uri(raw: str | None) -> str:
    uri = raw or settings.DATABASE_URL
    return (
        uri.replace("postgresql+asyncpg://", "postgresql://", 1)
        .replace("postgresql+psycopg://", "postgresql://", 1)
    )


async def _setup(resource: Any) -> None:
    if hasattr(resource, "setup"):
        maybe_setup = resource.setup()
        if inspect.isawaitable(maybe_setup):
            await maybe_setup


@asynccontextmanager
async def langgraph_store_context() -> AsyncIterator[Any | None]:
    backend = (settings.LANGGRAPH_STORE_BACKEND or "memory").lower()
    if backend in {"none", "off", "disable", "disabled"}:
        yield None
        return

    if backend in {"postgres", "postgresql", "pg"}:
        if AsyncPostgresStore is None:
            raise RuntimeError("LANGGRAPH_STORE_BACKEND=postgres requires langgraph-checkpoint-postgres")
        async with AsyncPostgresStore.from_conn_string(
            _postgres_uri(settings.LANGGRAPH_STORE_DB or settings.DATABASE_URL)
        ) as store:
            await _setup(store)
            yield store
        return

    if InMemoryStore is None:
        yield None
        return
    yield InMemoryStore()
