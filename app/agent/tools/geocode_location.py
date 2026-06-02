from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.location_cache import cache_location
from app.agent.tools.native import RuntimeContext
from app.infra.external.amap import amap


class GeocodeLocationArgs(BaseModel):
    query: str = Field(..., description="Place name or address to geocode.")
    city: str | None = Field(default=None, description="Optional city hint.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _geocode_location(
    query: str,
    city: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    redis_client = ctx.get("redis_client")
    if not isinstance(redis_client, redis.Redis):
        raise RuntimeError("redis client unavailable")
    session_id = ctx.get("session_id")
    if not query.strip():
        return {"error": "missing_query"}
    context = ctx.get("context") if isinstance(ctx.get("context"), dict) else {}
    city = city or _extract_city(context)
    query, city = _normalize_query_city(query, city)
    query = _ensure_city_in_query(query, city)
    location = None
    candidates = [query]
    simplified = _strip_parentheses(query)
    if simplified and simplified != query:
        candidates.append(simplified)
    if city and simplified and city not in simplified:
        candidates.append(f"{simplified} {city}")
    for candidate in candidates:
        location = await amap.geocode_address(candidate, city, servers_path=ctx.get("servers_path"))
        if location:
            query = candidate
            break
    if not location:
        poi_location, poi_query = await _fallback_poi_location(query, city, ctx.get("servers_path"))
        if not poi_location:
            return {"error": "not_found"}
        location = poi_location
        query = poi_query
    await cache_location(redis_client, session_id, location.get("lat"), location.get("lng"), city)
    return {
        "lat": location.get("lat"),
        "lng": location.get("lng"),
        "city": city,
        "query": query,
    }


geocode_location_tool = StructuredTool.from_function(
    coroutine=_geocode_location,
    name="geocode_location",
    description=(
        "Geocode a place name to coordinates. Input: {query:string, city?:string}. "
        "Output: {lat,lng,city,query} or {error}."
    ),
    args_schema=GeocodeLocationArgs,
    infer_schema=False,
)


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


def _ensure_city_in_query(query: str, city: str | None) -> str:
    if not city:
        return query
    if city in query:
        return query
    return f"{query} {city}".strip()


def _strip_parentheses(query: str) -> str:
    if not query:
        return query
    for left, right in (("（", "）"), ("(", ")")):
        while left in query and right in query:
            start = query.find(left)
            end = query.find(right, start + 1)
            if end == -1:
                break
            query = (query[:start] + query[end + 1 :]).strip()
    return query.strip()


async def _fallback_poi_location(
    query: str,
    city: str | None,
    servers_path: str | None,
) -> tuple[dict[str, float] | None, str]:
    keywords = query.strip()
    pois = await amap.text_search(
        keywords,
        None,
        city=city,
        page_size=1,
        servers_path=servers_path,
    )
    if not pois:
        return None, query
    poi_id = pois[0].get("id") or pois[0].get("poi_id")
    if not poi_id:
        return None, query
    detail = await amap.search_detail(poi_id, servers_path=servers_path)
    location = _parse_location(detail.get("location") if isinstance(detail, dict) else None)
    if not location:
        location = _parse_location(pois[0].get("location"))
    return location, keywords


def _parse_location(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        lat = value.get("lat")
        lng = value.get("lng")
        if lat is not None and lng is not None:
            return {"lat": float(lat), "lng": float(lng)}
    if isinstance(value, str) and "," in value:
        parts = value.split(",")
        if len(parts) >= 2:
            lng, lat = parts[0].strip(), parts[1].strip()
            try:
                return {"lat": float(lat), "lng": float(lng)}
            except ValueError:
                return None
    return None


def _extract_city(context: dict[str, Any]) -> str | None:
    env = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = env.get("location") or context.get("location")
    if isinstance(location, dict):
        return location.get("city") or location.get("name")
    if isinstance(location, str):
        return location
    return None
