from __future__ import annotations

from typing import Any

import redis.asyncio as redis

from app.agent.tools.location_cache import cache_location
from app.agent.tools_registry import register_tool
from app.infra.external.amap import amap


@register_tool(
    name="geocode_location",
    description=(
        "Geocode a place name to coordinates. "
        "Input: {query:string, city?:string}. "
        "Output: {lat:number,lng:number,city?:string,query:string} or {error:string}. "
        "Example input: {\"query\":\"长沙岳麓区黄鹤小区\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "city": {"type": "string"},
        },
        "required": ["query"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "lat": {"type": "number"},
            "lng": {"type": "number"},
            "city": {"type": "string"},
            "query": {"type": "string"},
            "error": {"type": "string"},
        },
    },
)
async def geocode_location(args: dict[str, Any]) -> dict[str, Any]:
    redis_client = args.get("redis_client")
    if not isinstance(redis_client, redis.Redis):
        raise RuntimeError("redis client unavailable")
    session_id = args.get("session_id")
    query = args.get("query") or ""
    if not query.strip():
        return {"error": "missing_query"}
    context = args.get("context") if isinstance(args.get("context"), dict) else {}
    city = args.get("city") or _extract_city(context)
    query, city = _normalize_query_city(query, city)
    location = await amap.geocode_address(query, city, servers_path=args.get("servers_path"))
    if not location:
        return {"error": "not_found"}
    await cache_location(redis_client, session_id, location.get("lat"), location.get("lng"), city)
    return {
        "lat": location.get("lat"),
        "lng": location.get("lng"),
        "city": city,
        "query": query,
    }


def _normalize_query_city(query: str, city: str | None) -> tuple[str, str | None]:
    query = query.strip()
    if not query:
        return query, city
    for sep in (",", "，"):
        if sep not in query:
            continue
        parts = [part.strip() for part in query.split(sep) if part.strip()]
        if not parts:
            return query, city
        if city is None and len(parts) > 1:
            city = parts[-1]
        query = parts[0]
        return query, city
    return query, city


def _extract_city(context: dict[str, Any]) -> str | None:
    env = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = env.get("location") or context.get("location")
    if isinstance(location, dict):
        return location.get("city") or location.get("name")
    if isinstance(location, str):
        return location
    return None
