from __future__ import annotations

import logging
from typing import Any

import redis.asyncio as redis
from langgraph.graph import StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.config import settings

logger = logging.getLogger("agent")


def select_legacy_graph_runtime() -> str:
    runtime = (settings.AGENT_GRAPH_RUNTIME or "legacy").strip().lower()
    return runtime if runtime in {"legacy", "official"} else "legacy"


def build_legacy_agent_graph(
    db: AsyncSession,
    redis_client: redis.Redis,
    agent_config: Any,
    provider: str | None = None,
    runtime: str | None = None,
) -> StateGraph:
    """Legacy graph runtime adapter.

    This module exists to keep graph.py focused on stream orchestration while
    legacy builders are being phased out.
    """
    runtime = (runtime or select_legacy_graph_runtime()).strip().lower()
    if runtime not in {"legacy", "official"}:
        runtime = "legacy"

    # Lazy import to avoid circular imports and keep legacy dependency explicit.
    from app.agent.graph import build_langgraph, build_langgraph_official

    builder = build_langgraph_official if runtime == "official" else build_langgraph
    logger.info(
        "agent_graph_runtime mode=%s phase=legacy_dispatch agent=%s",
        runtime,
        getattr(agent_config, "name", None),
    )
    return builder(
        db=db,
        redis_client=redis_client,
        provider=provider,
        agent_config=agent_config,
    )
