from __future__ import annotations

from typing import Any
import re

import redis.asyncio as redis
import logging

from app.agent.tools_registry import register_tool
from app.domain.restaurant.service import RestaurantService
from app.agent.tools.location_cache import cache_location, load_cached_location
from app.agent.tools.restaurant_cache import cache_restaurants
from app.infra.external.amap import amap

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
    last_user_message = args.get("last_user_message")
    context = args.get("context") if isinstance(args.get("context"), dict) else {}
    servers_path = args.get("servers_path")
    if isinstance(query, str) and not query.strip():
        query = None
    if _looks_like_address(last_user_message):
        city = _extract_city_from_text(last_user_message) or _extract_city_from_context(context)
        location = await amap.geocode_address(
            _normalize_address_text(last_user_message),
            city,
            servers_path=servers_path,
        )
        if location:
            lat = location.get("lat")
            lng = location.get("lng")
            await cache_location(redis_client, session_id, lat, lng, city)
    cached = None
    if isinstance(session_id, str):
        cached = await load_cached_location(redis_client, session_id)
        if cached and (lat is None or lng is None):
            lat = lat or cached.get("lat")
            lng = lng or cached.get("lng")
    if lat is None or lng is None:
        if cached:
            lat = cached.get("lat")
            lng = cached.get("lng")
        if lat is None or lng is None:
            return {"error": "missing_location"}
    if query is None:
        query = tag or "美食"
    if isinstance(query, str) and query.strip() in _GENERIC_QUERIES:
        query = "美食"
    results = await RestaurantService.search(
        redis_client,
        query,
        tag,
        lat,
        lng,
        args.get("sort"),
    )
    if not results and cached:
        cached_lat = _normalize_coord(cached.get("lat"))
        cached_lng = _normalize_coord(cached.get("lng"))
        if cached_lat is not None and cached_lng is not None:
            if not _coords_close(lat, lng, cached_lat, cached_lng):
                results = await RestaurantService.search(
                    redis_client,
                    query,
                    tag,
                    cached_lat,
                    cached_lng,
                    args.get("sort"),
                )
    await cache_restaurants(redis_client, session_id, results)
    return results


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


def _coords_close(lat1: float | None, lng1: float | None, lat2: float | None, lng2: float | None) -> bool:
    if lat1 is None or lng1 is None or lat2 is None or lng2 is None:
        return False
    return abs(lat1 - lat2) < 0.0001 and abs(lng1 - lng2) < 0.0001


def _looks_like_address(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    text = value.strip()
    if not text or text in _GENERIC_QUERIES:
        return False
    tokens = ("省", "市", "区", "县", "街", "路", "巷", "号", "小区", "村", "镇", "乡", "大道", "广场", "站")
    if any(token in text for token in tokens):
        return True
    return bool(re.search(r"\d+(\s*(号|栋|单元|室|楼))?", text))


def _normalize_address_text(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip()


def _extract_city_from_text(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    text = value.strip()
    match = re.search(r"([\u4e00-\u9fa5]{2,8}市)", text)
    if match:
        return match.group(1)
    return None


def _extract_city_from_context(context: dict[str, Any]) -> str | None:
    env = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = env.get("location") or context.get("location")
    if isinstance(location, dict):
        return location.get("city") or location.get("name")
    if isinstance(location, str):
        return location
    return None


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
