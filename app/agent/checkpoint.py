from __future__ import annotations

import inspect
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

from app.common.config import settings

from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.checkpoint.memory import InMemorySaver

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
