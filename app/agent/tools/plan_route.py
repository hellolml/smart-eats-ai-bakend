from __future__ import annotations

from typing import Any
import math

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.native import RuntimeContext
from app.infra.external.amap import amap


def _coerce_location(lat: Any, lng: Any) -> dict[str, float] | None:
    """将经纬度转换为标准格式"""
    if lat is None or lng is None:
        return None
    try:
        lat_value = float(lat)
        lng_value = float(lng)
    except (TypeError, ValueError):
        return None
    # Swap obvious lat/lng inversion (lat must be within [-90, 90]).
    if abs(lat_value) > 90 and abs(lng_value) <= 90:
        lat_value, lng_value = lng_value, lat_value
    return {"lat": lat_value, "lng": lng_value}


def _normalize_mode(value: Any) -> str | None:
    """标准化出行模式"""
    if not isinstance(value, str):
        return None
    mode = value.strip().lower()
    if not mode:
        return None
    if mode in {"walk", "walking"}:
        return "walking"
    if mode in {"bike", "bicycling", "cycling"}:
        return "bicycling"
    if mode in {"transit", "bus", "public"}:
        return "transit"
    if mode in {"drive", "driving", "car"}:
        return "driving"
    return mode


def _haversine_meters(origin: dict[str, float], destination: dict[str, float]) -> float:
    """计算两点之间的直线距离（米）"""
    lat1 = math.radians(float(origin["lat"]))
    lng1 = math.radians(float(origin["lng"]))
    lat2 = math.radians(float(destination["lat"]))
    lng2 = math.radians(float(destination["lng"]))
    dlat = lat2 - lat1
    dlng = lng2 - lng1
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return 6371000 * c


class PlanRouteArgs(BaseModel):
    origin_lat: float = Field(..., description="Origin latitude.")
    origin_lng: float = Field(..., description="Origin longitude.")
    destination_lat: float = Field(..., description="Destination latitude.")
    destination_lng: float = Field(..., description="Destination longitude.")
    mode: str | None = Field(default=None, description="walking, bicycling, transit, or driving.")
    strategy: str | None = Field(default=None, description="Optional routing strategy.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _plan_route(
    origin_lat: float,
    origin_lng: float,
    destination_lat: float,
    destination_lng: float,
    mode: str | None = None,
    strategy: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    规划路线（原子化工具）
    
    位置获取/地理编码逻辑已移除，由 Agent 负责先调用 geocode_location 获取坐标。
    """
    ctx = runtime_context or {}
    servers_path = ctx.get("servers_path")
    
    # 获取起点坐标
    origin = _coerce_location(origin_lat, origin_lng)
    if not origin:
        return {"error": "missing_origin"}
    
    # 获取终点坐标
    destination = _coerce_location(destination_lat, destination_lng)
    if not destination:
        return {"error": "missing_destination"}
    
    # 确定出行模式
    mode = _normalize_mode(mode)
    if mode is None:
        # 如果用户未指定，根据距离自动选择
        direct_distance = _haversine_meters(origin, destination)
        mode = "walking" if direct_distance <= 2000 else "driving"
    
    # 调用高德地图获取路线
    route = await amap.get_route(
        origin,
        destination,
        mode,
        strategy,
        servers_path=servers_path,
    )
    
    if not route:
        return {"error": "route_unavailable"}
    
    return route


plan_route_tool = StructuredTool.from_function(
    coroutine=_plan_route,
    name="plan_route",
    description=(
        "Plan a route between origin and destination coordinates. "
        "Input: {origin_lat,origin_lng,destination_lat,destination_lng,mode?,strategy?}. "
        "IMPORTANT: all coordinates are required."
    ),
    args_schema=PlanRouteArgs,
    infer_schema=False,
)
