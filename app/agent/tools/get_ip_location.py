from __future__ import annotations

from typing import Any

import httpx
import redis.asyncio as redis

from app.agent.tools.location_cache import cache_location
from app.agent.tools_registry import register_tool
from app.infra.external.amap import amap


@register_tool(
    name="get_ip_location",
    description=(
        "Resolve IP address to location. Input: {ip:string?}. "
        "Output: {lat:number,lng:number,city:string} or {error:string}. "
        "Example input: {\"ip\":\"8.8.8.8\"}."
    ),
    input_schema={
        "type": "object",
        "properties": {"ip": {"type": "string"}},
        "required": [],
    },
    output_schema={
        "type": "object",
        "properties": {
            "lat": {"type": "number"},
            "lng": {"type": "number"},
            "city": {"type": "string"},
            "error": {"type": "string"},
        },
    },
)
async def get_ip_location(args: dict[str, Any]) -> dict[str, Any]:
    redis_client = args.get("redis_client")
    if not isinstance(redis_client, redis.Redis):
        raise RuntimeError("redis client unavailable")
    session_id = args.get("session_id")
    ip = args.get("ip") or args.get("client_ip")
    if _is_local_ip(ip):
        ip = await _fetch_public_ip()
    if not isinstance(ip, str) or not ip:
        return {"error": "missing_ip"}
    location, city = await amap.get_ip_location(ip, servers_path=args.get("servers_path"))
    location_source = "amap_ip"
    if not location:
        fallback = await _fallback_ip_lookup(ip)
        if fallback:
            location = {"lat": fallback["lat"], "lng": fallback["lng"]}
            city = fallback.get("city") or city
            location_source = "ipwhois"
    if location:
        await cache_location(redis_client, session_id, location.get("lat"), location.get("lng"), city)
        return {
            "lat": location.get("lat"),
            "lng": location.get("lng"),
            "city": city,
            "location_source": location_source,
        }
    return {"error": "missing_location"}


def _is_local_ip(value: Any) -> bool:
    if not isinstance(value, str):
        return True
    return value in {"", "unknown", "127.0.0.1", "::1"}


async def _fetch_public_ip() -> str | None:
    try:
        async with httpx.AsyncClient(timeout=2.0) as client:
            resp = await client.get("https://api.ipify.org?format=json")
            resp.raise_for_status()
            data = resp.json()
            ip = data.get("ip")
            return ip if isinstance(ip, str) and ip else None
    except Exception:
        return None


async def _fallback_ip_lookup(ip: str) -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient(timeout=2.5) as client:
            resp = await client.get(f"https://ipwho.is/{ip}")
            resp.raise_for_status()
            data = resp.json()
    except Exception:
        return None

    if not isinstance(data, dict) or data.get("success") is False:
        return None

    lat = data.get("latitude")
    lng = data.get("longitude")
    city = data.get("city") or data.get("region")
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return None

    return {"lat": lat, "lng": lng, "city": city if isinstance(city, str) else None}
