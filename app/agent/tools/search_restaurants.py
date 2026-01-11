from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools_registry import register_tool
from app.infra.models.restaurant import RestaurantCache


@register_tool(
    name="search_restaurants",
    description="Search restaurants by keyword and location",
    args_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tag": {"type": "string"},
            "lat": {"type": "number"},
            "lng": {"type": "number"},
            "sort": {"type": "string"},
        },
        "required": [],
    },
)
async def search_restaurants(args: dict[str, Any]) -> list[dict[str, Any]]:
    db = args.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    query = args.get("query") or ""
    stmt = select(RestaurantCache)
    if query:
        stmt = stmt.where(RestaurantCache.name.contains(query))
    result = await db.execute(stmt.limit(10))
    rows = result.scalars().all()
    filtered = [row for row in rows if not (row.raw_json or {}).get("mock")]
    if not filtered:
        result = await db.execute(select(RestaurantCache).limit(10))
        rows = result.scalars().all()
        filtered = [row for row in rows if not (row.raw_json or {}).get("mock")]
    if not filtered:
        return []
    return [
        {
            "id": row.id,
            "provider": row.provider,
            "provider_id": row.provider_id,
            "name": row.name,
            "geo": row.geo,
            "rating": row.rating,
            "price": row.price,
            "tags": row.tags or [],
            "raw": row.raw_json,
        }
        for row in filtered[:5]
    ]
