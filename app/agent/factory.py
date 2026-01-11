from __future__ import annotations

import redis.asyncio as redis
from langgraph.graph import StateGraph
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.agent_registry import AgentConfig


def build_agent_graph(
    db: AsyncSession,
    redis_client: redis.Redis,
    agent_config: AgentConfig,
    provider: str | None = None,
) -> StateGraph:
    from app.agent.graph import build_langgraph

    return build_langgraph(
        db=db,
        redis_client=redis_client,
        provider=provider,
        agent_config=agent_config,
    )
