from __future__ import annotations

from typing import Any
import logging

import redis.asyncio as redis
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.domain.restaurant.service import RestaurantService
from app.agent.tools.restaurant_cache import cache_restaurants
from app.agent.tools.native import RuntimeContext
from app.infra.external.amap import amap

logger = logging.getLogger("agent.tools.location")


def _format_region(region: dict[str, Any] | None) -> str:
    if not isinstance(region, dict):
        return ""
    parts = [
        str(region.get("province") or "").strip(),
        str(region.get("city") or "").strip(),
        str(region.get("district") or "").strip(),
        str(region.get("township") or "").strip(),
        str(region.get("village") or "").strip(),
        str(region.get("street") or "").strip(),
        str(region.get("neighborhood") or "").strip(),
        str(region.get("building") or "").strip(),
    ]
    return " ".join([part for part in parts if part]).strip()


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


class SearchRestaurantsArgs(BaseModel):
    query: str | None = Field(default=None, description="Restaurant keyword or cuisine.")
    tag: str | None = Field(default=None, description="Restaurant category tag.")
    lat: float = Field(..., description="Latitude.")
    lng: float = Field(..., description="Longitude.")
    city: str | None = Field(default=None, description="Optional city hint.")
    sort: str | None = Field(default=None, description="Optional sort mode.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _search_restaurants(
    lat: float,
    lng: float,
    query: str | None = None,
    tag: str | None = None,
    city: str | None = None,
    sort: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]] | dict[str, Any]:
    """
    搜索餐厅（原子化工具）
    
    位置获取逻辑已移除，由 Agent 负责先调用 get_ip_location 或 geocode_location 获取坐标。
    """
    ctx = runtime_context or {}
    redis_client = ctx.get("redis_client")
    if not isinstance(redis_client, redis.Redis):
        raise RuntimeError("redis client unavailable")
    
    session_id = ctx.get("session_id")
    
    # 优先使用显式坐标，缺失时回退到上下文里的设备定位
    lat = _normalize_coord(lat)
    lng = _normalize_coord(lng)
    location_source = "tool_args"
    if lat is None or lng is None:
        ctx_lat, ctx_lng = _extract_location_from_context(ctx.get("context"))
        lat = lat if lat is not None else ctx_lat
        lng = lng if lng is not None else ctx_lng
        if lat is not None and lng is not None:
            location_source = "context"

    if lat is None or lng is None:
        logger.info(
            "location_used_for_search session_id=%s source=none error=missing_location",
            session_id,
        )
        return {"error": "missing_location"}

    region = await amap.reverse_geocode_region(
        {"lat": lat, "lng": lng},
        servers_path=ctx.get("servers_path"),
    )
    readable_address = _format_region(region)
    logger.info(
        "location_used_for_search session_id=%s source=%s lat=%s lng=%s address=%s",
        session_id,
        location_source,
        lat,
        lng,
        readable_address,
    )
    
    # 获取搜索参数
    if isinstance(query, str) and not query.strip():
        query = None
    if query is None:
        query = tag or "美食"
    
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


search_restaurants_tool = StructuredTool.from_function(
    coroutine=_search_restaurants,
    name="search_restaurants",
    description=(
        "Search restaurants by keyword and coordinates. "
        "Input: {query?:string,tag?:string,lat:number,lng:number,city?:string,sort?:string}. "
        "IMPORTANT: lat/lng are required. Call get_ip_location or geocode_location first if missing."
    ),
    args_schema=SearchRestaurantsArgs,
    infer_schema=False,
)
