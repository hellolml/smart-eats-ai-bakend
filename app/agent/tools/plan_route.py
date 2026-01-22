from __future__ import annotations

from typing import Any

import redis.asyncio as redis
from app.agent.tools_registry import register_tool
from app.agent.tools.restaurant_cache import load_cached_restaurants
from app.infra.external.amap import amap


@register_tool(
    name="plan_route",
    description=(
        "Plan a route between origin and destination. "
        "Input: {origin?:string,destination?:string,origin_lat?:number,origin_lng?:number,"
        "destination_lat?:number,destination_lng?:number,destination_poi_id?:string,mode?:string,city?:string,strategy?:string}. "
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
            "destination_poi_id": {"type": "string"},
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
    last_user_message = args.get("last_user_message")
    redis_client = args.get("redis_client")
    session_id = args.get("session_id")

    origin_value = args.get("origin") or args.get("from")
    destination_value = args.get("destination") or args.get("to")
    if not destination_value and isinstance(last_user_message, str) and last_user_message.strip():
        destination_value = last_user_message.strip()
    destination_poi_id = args.get("destination_poi_id") or args.get("poi_id")

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
    if not destination:
        poi_id = destination_poi_id or _match_poi_id(context, destination_value)
        if not poi_id and isinstance(redis_client, redis.Redis):
            cached = await load_cached_restaurants(redis_client, session_id)
            poi_id = _match_poi_id_in_results(cached, destination_value)
        if poi_id:
            detail = await amap.search_detail(poi_id, servers_path=servers_path)
            destination = _extract_location_from_detail(detail)
            if city is None:
                city = _extract_city_from_detail(detail)
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
        lat_value = float(lat)
        lng_value = float(lng)
    except (TypeError, ValueError):
        return None
    # Swap obvious lat/lng inversion (lat must be within [-90, 90]).
    if abs(lat_value) > 90 and abs(lng_value) <= 90:
        lat_value, lng_value = lng_value, lat_value
    return {"lat": lat_value, "lng": lng_value}


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
    return _extract_city_from_observations(context)


def _extract_city_from_observations(context: dict[str, Any]) -> str | None:
    observations = context.get("observations")
    if not isinstance(observations, list):
        return None
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        tool = obs.get("tool")
        result = obs.get("result")
        if tool in {"get_ip_location", "geocode_location"} and isinstance(result, dict):
            city = result.get("city")
            if isinstance(city, str) and city:
                return city
    return None


def _match_poi_id(context: dict[str, Any], destination_value: Any) -> str | None:
    if not isinstance(destination_value, str) or not destination_value.strip():
        return None
    target = destination_value.strip().lower()
    observations = context.get("observations")
    if not isinstance(observations, list):
        return None
    for obs in observations:
        if not isinstance(obs, dict):
            continue
        if obs.get("tool") != "search_restaurants":
            continue
        result = obs.get("result")
        return _match_poi_id_in_results(result, destination_value)
    return None


def _match_poi_id_in_results(results: Any, destination_value: Any) -> str | None:
    if not isinstance(destination_value, str) or not destination_value.strip():
        return None
    target = destination_value.strip().lower()
    if not isinstance(results, list):
        return None
    for item in results:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        name_lower = name.lower()
        if target == name_lower or target in name_lower or name_lower in target:
            provider_id = item.get("provider_id")
            if isinstance(provider_id, str) and provider_id:
                return provider_id
    return None


def _extract_location_from_detail(detail: Any) -> dict[str, float] | None:
    if not isinstance(detail, dict):
        return None
    location = detail.get("location") or detail.get("geo")
    if isinstance(location, dict):
        return _coerce_location(location.get("lat"), location.get("lng"))
    if isinstance(location, str) and "," in location:
        parts = [part.strip() for part in location.split(",")]
        if len(parts) >= 2:
            try:
                lng = float(parts[0])
                lat = float(parts[1])
                return {"lat": lat, "lng": lng}
            except ValueError:
                return None
    if "lat" in detail and "lng" in detail:
        return _coerce_location(detail.get("lat"), detail.get("lng"))
    return None


def _extract_city_from_detail(detail: Any) -> str | None:
    if not isinstance(detail, dict):
        return None
    for key in ("city", "cityname", "city_name"):
        value = detail.get(key)
        if isinstance(value, str) and value:
            return value
    return None
