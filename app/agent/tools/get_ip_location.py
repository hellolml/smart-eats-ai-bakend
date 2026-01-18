from __future__ import annotations

from typing import Any

import httpx
import redis.asyncio as redis

from app.agent.tools.location_cache import cache_location
from app.agent.tools_registry import register_tool
from app.infra.external.amap import amap


@register_tool(
    name="get_ip_location",
    description="Resolve IP address to location",
    args_schema={
        "type": "object",
        "properties": {"ip": {"type": "string"}},
        "required": [],
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
    if location:
        await cache_location(redis_client, session_id, location.get("lat"), location.get("lng"), city)
        return {"lat": location.get("lat"), "lng": location.get("lng"), "city": city}
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
