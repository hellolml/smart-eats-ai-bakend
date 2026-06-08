from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.agent.tools.native import RuntimeContext
from app.domain.recipe.service import RecipeService
from app.infra.db import AsyncSessionLocal
from app.infra.redis import get_redis
from app.infra.models.recipe import Recipe


async def _get_redis_client(runtime_context: dict[str, Any]) -> redis.Redis:
    client = runtime_context.get("redis_client")
    if client is not None:
        return client
    async for client in get_redis():
        return client
    raise RuntimeError("redis client unavailable")


class SearchRecipesArgs(BaseModel):
    query: str = Field(..., description="Recipe search keyword.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _search_recipes(
    query: str,
    runtime_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ctx = runtime_context or {}
    query = query or "home"
    if ctx.get("db") is not None:
        async with AsyncSessionLocal() as db:
            return await _search_recipes_in_db(db, query)
    redis_client = await _get_redis_client(ctx)
    return await RecipeService.search(redis_client, query)


async def _search_recipes_in_db(db: Any, query: str) -> list[dict[str, Any]]:
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


search_recipes_tool = StructuredTool.from_function(
    coroutine=_search_recipes,
    name="search_recipes",
    description="Search recipes by keyword. Input: {query:string}. Output: list of recipes.",
    args_schema=SearchRecipesArgs,
    infer_schema=False,
)
