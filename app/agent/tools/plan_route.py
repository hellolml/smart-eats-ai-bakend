from __future__ import annotations

from typing import Any
import math

from app.agent.tools_registry import register_tool
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


@register_tool(
    name="plan_route",
    description=(
        "Plan a route between origin and destination coordinates. "
        "Input: {origin_lat:number,origin_lng:number,destination_lat:number,destination_lng:number,mode?:string,strategy?:string}. "
        "Output: {distance_m,duration_s,steps,origin,destination,mode} or {error:string}. "
        "IMPORTANT: All coordinates are required. Call geocode_location first if you only have addresses. "
        "Example input: {\"origin_lat\":28.17,\"origin_lng\":112.93,\"destination_lat\":28.20,\"destination_lng\":112.97,\"mode\":\"driving\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "origin_lat": {"type": "number"},
            "origin_lng": {"type": "number"},
            "destination_lat": {"type": "number"},
            "destination_lng": {"type": "number"},
            "mode": {"type": "string", "enum": ["walking", "bicycling", "transit", "driving"]},
            "strategy": {"type": "string"},
        },
        "required": ["origin_lat", "origin_lng", "destination_lat", "destination_lng"],
    },
    output_schema={
        "type": "object",
        "properties": {
            "distance_m": {"type": ["number", "string", "null"]},
            "duration_s": {"type": ["number", "string", "null"]},
            "steps": {"type": "array", "items": {"type": "string"}},
            "origin": {"type": "object"},
            "destination": {"type": "object"},
            "mode": {"type": "string"},
            "error": {"type": "string"},
        },
    },
)
async def plan_route(args: dict[str, Any]) -> dict[str, Any]:
    """
    规划路线（原子化工具）
    
    位置获取/地理编码逻辑已移除，由 Agent 负责先调用 geocode_location 获取坐标。
    """
    servers_path = args.get("servers_path")
    
    # 获取起点坐标
    origin = _coerce_location(args.get("origin_lat"), args.get("origin_lng"))
    if not origin:
        return {"error": "missing_origin"}
    
    # 获取终点坐标
    destination = _coerce_location(args.get("destination_lat"), args.get("destination_lng"))
    if not destination:
        return {"error": "missing_destination"}
    
    # 确定出行模式
    mode = _normalize_mode(args.get("mode"))
    if mode is None:
        # 如果用户未指定，根据距离自动选择
        direct_distance = _haversine_meters(origin, destination)
        mode = "walking" if direct_distance <= 2000 else "driving"
    
    # 调用高德地图获取路线
    route = await amap.get_route(
        origin,
        destination,
        mode,
        args.get("strategy"),
        servers_path=servers_path,
    )
    
    if not route:
        return {"error": "route_unavailable"}
    
    return route
