from __future__ import annotations

from typing import Any

from app.agent.tools_registry import register_tool
from app.infra.external.amap import amap


@register_tool(
    name="plan_route",
    description=(
        "Plan a route between origin and destination. "
        "Input: {origin?:string,destination?:string,origin_lat?:number,origin_lng?:number,"
        "destination_lat?:number,destination_lng?:number,mode?:string,city?:string,strategy?:string}. "
        "Output: {distance_m,duration_s,steps,origin,destination,mode} or {error:string}. "
        "Example input: {\"origin\":\"岳麓区\",\"destination\":\"长沙站\",\"mode\":\"driving\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "origin": {"type": "string"},
            "destination": {"type": "string"},
            "origin_lat": {"type": "number"},
            "origin_lng": {"type": "number"},
            "destination_lat": {"type": "number"},
            "destination_lng": {"type": "number"},
            "mode": {"type": "string"},
            "strategy": {"type": "string"},
            "city": {"type": "string"},
        },
        "required": [],
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
    servers_path = args.get("servers_path")
    context = args.get("context") if isinstance(args.get("context"), dict) else {}
    client_ip = args.get("client_ip")
    city = args.get("city") or _extract_city(context)

    origin_value = args.get("origin") or args.get("from")
    destination_value = args.get("destination") or args.get("to")

    origin = _coerce_location(args.get("origin_lat"), args.get("origin_lng"))
    if not origin:
        origin = _parse_location_value(origin_value)
    if not origin:
        origin = _location_from_context(context)
    if not origin and _is_real_ip(client_ip):
        origin, ip_city = await amap.get_ip_location(
            client_ip,
            servers_path=servers_path,
        )
        if city is None:
            city = ip_city
    if not origin and isinstance(origin_value, str):
        origin = await amap.geocode_address(origin_value, city, servers_path=servers_path)

    destination = _coerce_location(args.get("destination_lat"), args.get("destination_lng"))
    if not destination:
        destination = _parse_location_value(destination_value)
    if not destination and isinstance(destination_value, str):
        destination = await amap.geocode_address(
            destination_value,
            city,
            servers_path=servers_path,
        )

    if not destination:
        return {"error": "missing_destination"}
    if not origin:
        return {"error": "missing_origin"}

    route = await amap.get_route(
        origin,
        destination,
        args.get("mode"),
        args.get("strategy"),
        servers_path=servers_path,
    )
    if not route:
        return {"error": "route_unavailable"}
    return route


def _is_real_ip(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    return value not in {"", "unknown", "127.0.0.1", "::1"}


def _coerce_location(lat: Any, lng: Any) -> dict[str, float] | None:
    if lat is None or lng is None:
        return None
    try:
        return {"lat": float(lat), "lng": float(lng)}
    except (TypeError, ValueError):
        return None


def _parse_location_value(value: Any) -> dict[str, float] | None:
    if isinstance(value, dict):
        return _coerce_location(value.get("lat"), value.get("lng"))
    if isinstance(value, str) and "," in value:
        parts = [part.strip() for part in value.split(",")]
        if len(parts) >= 2:
            try:
                lng = float(parts[0])
                lat = float(parts[1])
                return {"lat": lat, "lng": lng}
            except ValueError:
                return None
    return None


def _location_from_context(context: dict[str, Any]) -> dict[str, float] | None:
    env = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = env.get("location") or context.get("location")
    return _parse_location_value(location)


def _extract_city(context: dict[str, Any]) -> str | None:
    env = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    location = env.get("location") or context.get("location")
    if isinstance(location, dict):
        return location.get("city") or location.get("name")
    if isinstance(location, str):
        return location
    return None
