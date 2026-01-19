from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools_registry import register_tool
from app.domain.recipe.service import RecipeService
from app.infra.models.fridge import FridgeItem
from app.infra.redis import get_redis


async def _get_redis_client(args: dict[str, Any]) -> redis.Redis | None:
    client = args.get("redis_client")
    if client is not None:
        return client
    async for client in get_redis():
        return client
    return None


@register_tool(
    name="get_fridge_items",
    description=(
        "Fetch the user's fridge items and optional recipe suggestions. "
        "Input: {}. Output: {items:[...],recipes:[...],query:string}. "
        "Example input: {}."
    ),
    input_schema={
        "type": "object",
        "properties": {},
        "required": [],
    },
    output_schema={
        "type": "object",
        "properties": {
            "items": {"type": "array", "items": {"type": "object"}},
            "recipes": {"type": "array", "items": {"type": "object"}},
            "query": {"type": "string"},
        },
    },
)
async def get_fridge_items(args: dict[str, Any]) -> dict[str, Any]:
    db = args.get("db")
    user_id = args.get("user_id")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    if not user_id:
        return {"items": [], "recipes": [], "query": ""}
    result = await db.execute(
        select(FridgeItem).where(FridgeItem.user_id == user_id).limit(20)
    )
    items = result.scalars().all()
    payload_items = [
        {
            "id": item.id,
            "name": item.name,
            "quantity": item.quantity,
            "unit": item.unit,
            "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
            "source": item.source,
        }
        for item in items
    ]
    names = [item["name"] for item in payload_items if item.get("name")]
    query = " ".join(names[:3]) if names else ""
    recipes: list[dict[str, Any]] = []
    redis_client = await _get_redis_client(args)
    if redis_client and query:
        recipes = await RecipeService.search(redis_client, query)
    return {"items": payload_items, "recipes": recipes, "query": query}
