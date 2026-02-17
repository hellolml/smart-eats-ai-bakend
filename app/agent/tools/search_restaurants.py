from __future__ import annotations

from typing import Any

import redis.asyncio as redis

from app.agent.tools_registry import register_tool
from app.domain.restaurant.service import RestaurantService
from app.agent.tools.restaurant_cache import cache_restaurants


def _normalize_coord(value: Any) -> float | None:
    """将坐标值转换为浮点数，无效值返回 None"""
    if value is None:
        return None
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    if value == 0:
        return None
    return value


def _extract_location_from_context(context: Any) -> tuple[float | None, float | None]:
    if not isinstance(context, dict):
        return None, None
    environment = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = environment.get("location") if isinstance(environment.get("location"), dict) else {}
    return _normalize_coord(location.get("lat")), _normalize_coord(location.get("lng"))


@register_tool(
    name="search_restaurants",
    description=(
        "Search restaurants by keyword and coordinates. "
        "Input: {query?:string,tag?:string,lat:number,lng:number,city?:string,sort?:string}. "
        "Output: list of restaurants or {error:string}. "
        "IMPORTANT: lat/lng are required. Call get_ip_location or geocode_location first if missing. "
        "Example input: {\"query\":\"火锅\",\"lat\":28.17,\"lng\":112.93}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "tag": {"type": "string"},
            "lat": {"type": "number"},
            "lng": {"type": "number"},
            "city": {"type": "string"},
            "sort": {"type": "string"},
        },
        "required": ["lat", "lng"],
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
    """
    搜索餐厅（原子化工具）
    
    位置获取逻辑已移除，由 Agent 负责先调用 get_ip_location 或 geocode_location 获取坐标。
    """
    redis_client = args.get("redis_client")
    if not isinstance(redis_client, redis.Redis):
        raise RuntimeError("redis client unavailable")
    
    session_id = args.get("session_id")
    
    # 优先使用显式坐标，缺失时回退到上下文里的设备定位
    lat = _normalize_coord(args.get("lat"))
    lng = _normalize_coord(args.get("lng"))
    if lat is None or lng is None:
        ctx_lat, ctx_lng = _extract_location_from_context(args.get("context"))
        lat = lat if lat is not None else ctx_lat
        lng = lng if lng is not None else ctx_lng

    if lat is None or lng is None:
        return {"error": "missing_location"}
    
    # 获取搜索参数
    query = args.get("query")
    if isinstance(query, str) and not query.strip():
        query = None
    if query is None:
        query = args.get("tag") or "美食"
    
    tag = args.get("tag")
    city = args.get("city")
    sort = args.get("sort")
    
    # 执行搜索
    results = await RestaurantService.search(
        redis_client,
        query,
        tag,
        lat,
        lng,
        sort,
        city,
    )
    
    # 缓存结果（用于后续路线规划）
    await cache_restaurants(redis_client, session_id, results)
    
    return results
