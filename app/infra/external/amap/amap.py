from __future__ import annotations

import json
import logging
from typing import Any

from app.infra.mcp import client as mcp_client
from app.infra.mcp import config as mcp_config
from . import amap_config

logger = logging.getLogger("amap.mcp")

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
    return _extract_geocode(payload)


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
    status = None
    temperature = None
    if isinstance(payload, dict):
        status = payload.get("weather") or payload.get("status")
        temperature = payload.get("temperature") or payload.get("temp")
        if temperature is not None:
            try:
                temperature = float(temperature)
            except (TypeError, ValueError):
                temperature = None
    return {
        "city": city or "",
        "status": status or "unknown",
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
        return None
    route = _extract_route(payload)
    if not route:
        return None
    route["origin"] = origin
    route["destination"] = destination
    route["mode"] = (mode or "driving").strip().lower()
    return route
