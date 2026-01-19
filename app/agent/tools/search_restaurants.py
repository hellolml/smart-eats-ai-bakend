from __future__ import annotations

from typing import Any

import redis.asyncio as redis
import logging

from app.agent.tools_registry import register_tool
from app.domain.restaurant.service import RestaurantService
from app.agent.tools.location_cache import load_cached_location

logger = logging.getLogger("amap.mcp")

@register_tool(
    name="search_restaurants",
    description=(
        "Search restaurants by keyword and coordinates. "
        "Input: {query?:string,tag?:string,lat:number,lng:number,sort?:string}. "
        "Output: list of restaurants or {error:string}. "
        "Example input: {\"query\":\"火锅\",\"lat\":28.17,\"lng\":112.93}."
    ),
    input_schema={
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
    output_schema={
        "oneOf": [
            {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "id": {"type": "string"},
                        "provider": {"type": "string"},
                        "provider_id": {"type": "string"},
                        "name": {"type": "string"},
                        "geo": {"type": "object"},
                        "rating": {"type": ["number", "null"]},
                        "price": {"type": ["number", "null"]},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                },
            },
            {"type": "object", "properties": {"error": {"type": "string"}}},
        ]
    },
)
async def search_restaurants(args: dict[str, Any]) -> list[dict[str, Any]] | dict[str, Any]:
    redis_client = args.get("redis_client")
    if not isinstance(redis_client, redis.Redis):
        raise RuntimeError("redis client unavailable")
    session_id = args.get("session_id")
    query = args.get("query")
    tag = args.get("tag")
    lat = _normalize_coord(args.get("lat"))
    lng = _normalize_coord(args.get("lng"))
    if isinstance(query, str) and not query.strip():
        query = None
    if (lat is None or lng is None) and isinstance(session_id, str):
        cached = await load_cached_location(redis_client, session_id)
        if cached:
            lat = lat or cached.get("lat")
            lng = lng or cached.get("lng")
    if lat is None or lng is None:
        return {"error": "missing_location"}
    if query is None:
        query = tag or "美食"
    if isinstance(query, str) and query.strip() in _GENERIC_QUERIES:
        query = "美食"
    return await RestaurantService.search(
        redis_client,
        query,
        tag,
        lat,
        lng,
        args.get("sort"),
    )


def _normalize_coord(value: Any) -> float | None:
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    return value


_GENERIC_QUERIES = {
    "出去吃",
    "外出用餐",
    "找餐厅",
    "附近餐厅",
    "吃饭",
    "还有其他的吗",
    "还有其他的吗?",
    "还有其他的吗？",
    "还有别的",
    "再推荐",
    "其他",
}


def _is_local_ip(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    return value in {"", "unknown", "127.0.0.1", "::1"}
