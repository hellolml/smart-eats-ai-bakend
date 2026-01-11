from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools_registry import register_tool
from app.domain.recipe.service import RecipeService
from app.infra.redis import get_redis
from app.infra.models.recipe import Recipe


async def _get_redis_client(args: dict[str, Any]) -> redis.Redis:
    client = args.get("redis_client")
    if client is not None:
        return client
    async for client in get_redis():
        return client
    raise RuntimeError("redis client unavailable")


@register_tool(
    name="search_recipes",
    description="Search recipes by keyword",
    args_schema={
        "type": "object",
        "properties": {"query": {"type": "string"}},
        "required": ["query"],
    },
)
async def search_recipes(args: dict[str, Any]) -> list[dict[str, Any]]:
    query = args.get("query") or "home"
    db = args.get("db")
    if isinstance(db, AsyncSession):
        result = await db.execute(
            select(Recipe).where(Recipe.title.contains(query)).limit(5)
        )
        rows = result.scalars().all()
        if not rows:
            result = await db.execute(select(Recipe).limit(5))
            rows = result.scalars().all()
        return [
            {
                "id": row.id,
                "title": row.title,
                "image_url": row.image_url,
                "cook_time_min": row.cook_time_min,
                "calories": row.calories,
                "tags": row.tags or [],
            }
            for row in rows
        ]
    redis_client = await _get_redis_client(args)
    return await RecipeService.search(redis_client, query)
