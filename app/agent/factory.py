from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from langgraph.graph import StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agents.smart_eats import build_smart_eats_graph
from app.agent.legacy_runtime import build_legacy_agent_graph
from app.common.config import settings


def _select_graph_runtime() -> str:
    runtime = (settings.AGENT_GRAPH_RUNTIME or "legacy").strip().lower()
    return runtime if runtime in {"legacy", "official"} else "legacy"


def build_agent_graph(
    db: AsyncSession,
    redis_client: redis.Redis,
    agent_config: Any,
    provider: str | None = None,
) -> StateGraph:
    agent_name = getattr(agent_config, "name", None)
    if agent_name == "smart_eats":
        return build_smart_eats_graph(
            db=db,
            redis_client=redis_client,
            provider=provider,
        )

    return build_legacy_agent_graph(
        db=db,
        redis_client=redis_client,
        provider=provider,
        agent_config=agent_config,
        runtime=_select_graph_runtime(),
    )
