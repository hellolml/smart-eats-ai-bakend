from __future__ import annotations

import json
import logging
from typing import Any

from app.infra.mcp import client as mcp_client
from app.infra.mcp import config as mcp_config
from . import amap_config

logger = logging.getLogger("amap.mcp")

_GEOCODE_LEVEL_SCORE = {
    "门址": 4,
    "兴趣点": 3,
    "道路": 2,
    "村庄": 1,
    "未知": 0,
}

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


def _parse_json_payload(payload: Any) -> Any:
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return payload
    if isinstance(payload, list) and payload and all(isinstance(item, str) for item in payload):
        if len(payload) == 1:
            try:
                return json.loads(payload[0])
            except json.JSONDecodeError:
                return payload
    return payload


def _extract_pois(payload: Any) -> list[dict[str, Any]]:
    payload = _parse_json_payload(payload)
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if isinstance(payload, dict):
        for key in ("pois", "data", "results", "items"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
    return []


def _extract_detail(payload: Any) -> dict[str, Any] | None:
    payload = _parse_json_payload(payload)
    if isinstance(payload, dict):
        return payload
    if isinstance(payload, list) and payload:
        first = payload[0]
        return first if isinstance(first, dict) else None
    return None


def _extract_ip_location(payload: Any) -> tuple[dict[str, float] | None, str | None]:
    payload = _parse_json_payload(payload)
    if isinstance(payload, list) and payload:
        payload = payload[0] if isinstance(payload[0], dict) else payload
    if not isinstance(payload, dict):
        return None, None
    location = _parse_location(payload.get("location") or payload.get("loc"))
    if not location and isinstance(payload.get("rectangle"), str):
        parts = payload["rectangle"].split(";")
        if len(parts) == 2:
            left = _parse_location(parts[0])
            right = _parse_location(parts[1])
            if left and right:
                location = {
                    "lat": (left["lat"] + right["lat"]) / 2,
                    "lng": (left["lng"] + right["lng"]) / 2,
                }
    city = payload.get("city") or payload.get("province") or payload.get("adcode")
    return location, city


def _extract_regeo_region(payload: Any) -> dict[str, str] | None:
    payload = _parse_json_payload(payload)
    if isinstance(payload, list) and payload:
        payload = payload[0] if isinstance(payload[0], dict) else payload
    if not isinstance(payload, dict):
        return None

    regeo = payload.get("regeocode")
    if isinstance(regeo, dict):
        payload = regeo

    component = payload.get("addressComponent") if isinstance(payload.get("addressComponent"), dict) else payload

    def _pick(value: Any) -> str | None:
        if isinstance(value, list):
            value = value[0] if value else None
        if isinstance(value, str) and value.strip():
            return value.strip()
        return None

    district = _pick(component.get("district")) or _pick(payload.get("district"))
    city = _pick(component.get("city")) or _pick(payload.get("city"))
    province = _pick(component.get("province")) or _pick(payload.get("province"))
    township = _pick(component.get("township"))

    neighborhood_name = None
    neighborhood = component.get("neighborhood")
    if isinstance(neighborhood, dict):
        neighborhood_name = _pick(neighborhood.get("name"))

    building_name = None
    building = component.get("building")
    if isinstance(building, dict):
        building_name = _pick(building.get("name"))

    street = _pick(component.get("streetNumber"))
    if not street:
        street_number = component.get("streetNumber")
        if isinstance(street_number, dict):
            street_name = _pick(street_number.get("street"))
            number = _pick(street_number.get("number"))
            street = "".join(part for part in [street_name, number] if part)

    village = _pick(component.get("village"))

    region = {
        k: v
        for k, v in {
            "province": province,
            "city": city,
            "district": district,
            "township": township,
            "village": village,
            "street": street,
            "neighborhood": neighborhood_name,
            "building": building_name,
        }.items()
        if v
    }
    return region or None


def _extract_regeo_city(payload: Any) -> str | None:
    region = _extract_regeo_region(payload)
    if not region:
        return None
    return region.get("district") or region.get("city") or region.get("province")


def _extract_geocode(payload: Any) -> dict[str, float] | None:
    payload = _parse_json_payload(payload)
    if isinstance(payload, dict):
        for key in ("geocodes", "geocode", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, dict):
                    return _parse_location(first.get("location"))
        return _parse_location(payload.get("location"))
    if isinstance(payload, list) and payload:
        first = payload[0]
        if isinstance(first, dict):
            return _parse_location(first.get("location"))
    return None


def _normalize_city_name(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    name = value.strip()
    for suffix in ("市", "省", "特别行政区", "自治区"):
        if name.endswith(suffix):
            name = name[: -len(suffix)]
    return name


def _city_matches(candidate: dict[str, Any], expected_city: str | None) -> bool:
    if not expected_city:
        return True
    expected = _normalize_city_name(expected_city)
    if not expected:
        return True
    values = [
        candidate.get("city"),
        candidate.get("province"),
        candidate.get("district"),
    ]
    for value in values:
        normalized = _normalize_city_name(value)
        if not normalized:
            continue
        if expected in normalized or normalized in expected:
            return True
    return False


def _extract_geocode_candidates(payload: Any) -> list[dict[str, Any]]:
    payload = _parse_json_payload(payload)
    if isinstance(payload, dict):
        for key in ("geocodes", "geocode", "data", "results"):
            value = payload.get(key)
            if isinstance(value, list):
                return [item for item in value if isinstance(item, dict)]
        return [payload]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _pick_geocode_location(
    payload: Any,
    city: str | None,
) -> tuple[dict[str, float] | None, bool]:
    candidates = _extract_geocode_candidates(payload)
    valid: list[tuple[int, dict[str, Any], dict[str, float]]] = []
    for item in candidates:
        location = _parse_location(item.get("location"))
        if not location:
            continue
        level = str(item.get("level") or "未知")
        score = _GEOCODE_LEVEL_SCORE.get(level, 0)
        if _city_matches(item, city):
            score += 10
        valid.append((score, item, location))
    if not valid:
        return None, True
    valid.sort(key=lambda row: row[0], reverse=True)
    best_score, best_item, best_location = valid[0]
    level = str(best_item.get("level") or "未知")
    low_confidence = best_score < 10 or _GEOCODE_LEVEL_SCORE.get(level, 0) <= 0
    return best_location, low_confidence


async def _fallback_geocode_from_poi(
    address: str,
    city: str | None,
    *,
    servers_path: str | None,
) -> dict[str, float] | None:
    pois = await text_search(
        keywords=address,
        types=None,
        city=city,
        page_size=1,
        servers_path=servers_path,
    )
    if not pois:
        return None
    first = pois[0]
    poi_id = first.get("id") or first.get("poi_id")
    if isinstance(poi_id, str) and poi_id:
        detail = await search_detail(poi_id, servers_path=servers_path)
        detail_location = _parse_location(detail.get("location") if isinstance(detail, dict) else None)
        if detail_location:
            return detail_location
    return _parse_location(first.get("location"))


def _route_tool_key(mode: str | None) -> str:
    normalized = (mode or "driving").strip().lower()
    if normalized in {"walk", "walking"}:
        return "maps_direction_walking"
    if normalized in {"bike", "bicycling", "cycling"}:
        return "maps_direction_bicycling"
    if normalized in {"transit", "bus", "public"}:
        return "maps_direction_transit_integrated"
    return "maps_direction_driving"


def _normalize_coord_pair(value: dict[str, float]) -> dict[str, float]:
    lat = value.get("lat")
    lng = value.get("lng")
    if lat is None or lng is None:
        return value
    if abs(lat) > 90 and abs(lng) <= 90:
        return {"lat": lng, "lng": lat}
    return value


def _extract_route(payload: Any) -> dict[str, Any] | None:
    payload = _parse_json_payload(payload)
    route = payload
    if isinstance(payload, dict):
        route = payload.get("route") or payload.get("data") or payload
    if not isinstance(route, dict):
        return None
    paths = route.get("paths")
    if isinstance(paths, list) and paths:
        path = paths[0] if isinstance(paths[0], dict) else None
        if path:
            steps = []
            raw_steps = path.get("steps")
            if isinstance(raw_steps, list):
                for step in raw_steps:
                    if not isinstance(step, dict):
                        continue
                    instruction = step.get("instruction") or step.get("action") or step.get("road")
                    if instruction:
                        steps.append(str(instruction))
            return {
                "distance_m": path.get("distance"),
                "duration_s": path.get("duration"),
                "steps": steps,
            }
    transits = route.get("transits")
    if isinstance(transits, list) and transits:
        transit = transits[0] if isinstance(transits[0], dict) else None
        if transit:
            return {
                "distance_m": transit.get("distance"),
                "duration_s": transit.get("duration"),
                "segments": transit.get("segments"),
            }
    return None


def _resolve_mcp_context(
    tool_key: str,
    servers_path: str | None,
) -> tuple[dict[str, Any] | None, str, str | None]:
    servers = mcp_config.load_servers_from_file(servers_path)
    server = (amap_config.default_server_name() or "amap").strip() or "amap"
    if servers and server not in servers and len(servers) == 1:
        server = next(iter(servers))
    tool_name = amap_config.resolve_tool_name(
        servers,
        server=server,
        tool_key=tool_key,
        default_tool=amap_config.default_tool_name(tool_key),
    )
    if not tool_name:
        tool_name = tool_key
    return servers, server, tool_name


async def _call_mcp_tool(
    servers: dict[str, Any] | None,
    server: str,
    tool_name: str,
    args: dict[str, Any],
) -> Any | None:
    try:
        payload = await mcp_client.call_tool(servers, server, tool_name, args)
    except Exception:
        logger.exception("MCP tool call failed tool=%s args=%s", tool_name, args)
        return None
    return mcp_client.extract_payload(payload)


async def _fetch_tool_payload(
    tool_key: str,
    args: dict[str, Any],
    servers_path: str | None,
) -> Any | None:
    servers, server, tool_name = _resolve_mcp_context(tool_key, servers_path)
    if not servers or not tool_name:
        logger.warning(
            "MCP context missing tool_key=%s servers=%s tool=%s",
            tool_key,
            bool(servers),
            tool_name,
        )
        return None
    payload = await _call_mcp_tool(servers, server, tool_name, args)
    if payload is not None:
        logger.info("MCP payload tool=%s raw=%s", tool_name, payload)
    return payload


def _weather_fallback(city: str | None) -> dict[str, Any]:
    return {"city": city or "unknown", "status": "sunny", "temperature_c": 26}


def _extract_weather_payload(payload: Any) -> dict[str, Any]:
    parsed = _parse_json_payload(payload)
    if not isinstance(parsed, dict):
        return {}

    weather_payload = parsed

    lives = parsed.get("lives")
    if isinstance(lives, list) and lives and isinstance(lives[0], dict):
        weather_payload = lives[0]

    forecasts = parsed.get("forecasts")
    if isinstance(forecasts, list) and forecasts and isinstance(forecasts[0], dict):
        first = forecasts[0]
        first_casts = first.get("casts")
        if isinstance(first_casts, list) and first_casts and isinstance(first_casts[0], dict):
            weather_payload = first_casts[0]
        else:
            first_forecasts = first.get("forecasts")
            if isinstance(first_forecasts, list) and first_forecasts and isinstance(first_forecasts[0], dict):
                weather_payload = first_forecasts[0]
            else:
                weather_payload = first

    if isinstance(parsed.get("data"), dict):
        data = parsed["data"]
        data_lives = data.get("lives")
        if isinstance(data_lives, list) and data_lives and isinstance(data_lives[0], dict):
            weather_payload = data_lives[0]
        else:
            data_forecasts = data.get("forecasts")
            if isinstance(data_forecasts, list) and data_forecasts and isinstance(data_forecasts[0], dict):
                weather_payload = data_forecasts[0]
            else:
                weather_payload = data

    return weather_payload if isinstance(weather_payload, dict) else {}


async def get_ip_location(
    ip: str,
    *,
    servers_path: str | None,
) -> tuple[dict[str, float] | None, str | None]:
    args = {"ip": ip}
    payload = await _fetch_tool_payload("maps_ip_location", args, servers_path)
    if payload is None:
        return None, None
    return _extract_ip_location(payload)


async def reverse_geocode_region(
    location: dict[str, float],
    *,
    servers_path: str | None,
) -> dict[str, str] | None:
    normalized = _normalize_coord_pair(location)
    lat = normalized.get("lat")
    lng = normalized.get("lng")
    if lat is None or lng is None:
        return None

    args = {"location": f"{lng},{lat}", "extensions": "all"}
    # 优先使用当前 MCP 实际可用工具名，避免无效探测导致噪声报错
    for tool_key in ("maps_regeocode", "maps_reverse_geocode"):
        payload = await _fetch_tool_payload(tool_key, args, servers_path)
        if payload is None:
            continue
        region = _extract_regeo_region(payload)
        if region:
            return region
    return None


async def reverse_geocode_city(
    location: dict[str, float],
    *,
    servers_path: str | None,
) -> str | None:
    region = await reverse_geocode_region(location, servers_path=servers_path)
    if not region:
        return None
    return region.get("city") or region.get("province") or region.get("district")


async def geocode_address(
    address: str,
    city: str | None,
    *,
    servers_path: str | None,
) -> dict[str, float] | None:
    args: dict[str, Any] = {"address": address}
    if city:
        args["city"] = city
    payload = await _fetch_tool_payload("maps_geo", args, servers_path)
    if payload is None:
        return None
    location, low_confidence = _pick_geocode_location(payload, city)
    if location and not low_confidence:
        return location
    fallback = await _fallback_geocode_from_poi(address, city, servers_path=servers_path)
    return fallback or location


async def text_search(
    keywords: str,
    types: str | None,
    *,
    city: str | None = None,
    location: str | None = None,
    page_size: int = 5,
    servers_path: str | None,
) -> list[dict[str, Any]]:
    args: dict[str, Any] = {
        "keywords": keywords,
        "types": types,
        "city": city,
        "location": location,
        "page_size": page_size,
    }
    args = {key: value for key, value in args.items() if value not in (None, "")}
    payload = await _fetch_tool_payload("maps_text_search", args, servers_path)
    if payload is None:
        return []
    return _extract_pois(payload)


async def around_search(
    location: str,
    keywords: str | None,
    types: str | None,
    *,
    radius: int | None = None,
    page_size: int = 5,
    servers_path: str | None,
) -> list[dict[str, Any]]:
    args: dict[str, Any] = {
        "location": location,
        "keywords": keywords,
        "types": types,
        "radius": radius,
        "page_size": page_size,
    }
    args = {key: value for key, value in args.items() if value not in (None, "")}
    payload = await _fetch_tool_payload("maps_around_search", args, servers_path)
    if payload is None:
        return []
    return _extract_pois(payload)


async def get_weather(
    city: str | None,
    *,
    servers_path: str | None,
) -> dict[str, Any]:
    args: dict[str, Any] = {"city": city or ""}
    args = {key: value for key, value in args.items() if value not in (None, "")}
    payload = await _fetch_tool_payload("maps_weather", args, servers_path)
    if payload is None:
        return _weather_fallback(city)

    weather_payload = _extract_weather_payload(payload)

    status = (
        weather_payload.get("weather")
        or weather_payload.get("status")
        or weather_payload.get("text")
        or weather_payload.get("dayweather")
        or weather_payload.get("nightweather")
    )
    temperature = (
        weather_payload.get("temperature")
        or weather_payload.get("temp")
        or weather_payload.get("daytemp")
        or weather_payload.get("nighttemp")
    )
    payload_city = (
        weather_payload.get("city")
        or weather_payload.get("province")
        or weather_payload.get("district")
    )

    if temperature is not None:
        try:
            temperature = float(temperature)
        except (TypeError, ValueError):
            temperature = None

    if temperature is None and weather_payload.get("daytemp_float") is not None:
        try:
            temperature = float(weather_payload.get("daytemp_float"))
        except (TypeError, ValueError):
            temperature = None

    return {
        "city": payload_city or city or "",
        "status": str(status).strip() if isinstance(status, str) and status.strip() else "sunny",
        "temperature_c": temperature,
        "raw": payload,
    }


async def search_detail(
    poi_id: str,
    *,
    servers_path: str | None,
) -> dict[str, Any] | None:
    args = {"id": poi_id}
    payload = await _fetch_tool_payload("maps_search_detail", args, servers_path)
    if payload is None:
        return None
    return _extract_detail(payload)


async def get_route(
    origin: dict[str, float],
    destination: dict[str, float],
    mode: str | None,
    strategy: str | None,
    *,
    servers_path: str | None,
) -> dict[str, Any] | None:
    tool_key = _route_tool_key(mode)
    origin = _normalize_coord_pair(origin)
    destination = _normalize_coord_pair(destination)
    args = {
        "origin": f"{origin.get('lng')},{origin.get('lat')}",
        "destination": f"{destination.get('lng')},{destination.get('lat')}",
    }
    if strategy:
        args["strategy"] = strategy
    payload = await _fetch_tool_payload(tool_key, args, servers_path)
    if payload is None:
        normalized_mode = (mode or "driving").strip().lower()
        if normalized_mode in {"walk", "walking", "bike", "bicycling", "cycling"}:
            fallback_payload = await _fetch_tool_payload(
                "maps_direction_driving",
                args,
                servers_path,
            )
            if fallback_payload is None:
                return None
            route = _extract_route(fallback_payload)
            if not route:
                return None
            route["origin"] = origin
            route["destination"] = destination
            route["mode"] = "driving"
            route["fallback_from"] = normalized_mode
            return route
        return None
    route = _extract_route(payload)
    if not route:
        return None
    route["origin"] = origin
    route["destination"] = destination
    route["mode"] = (mode or "driving").strip().lower()
    return route
