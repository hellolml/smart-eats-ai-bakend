from __future__ import annotations

from typing import Any
import logging

import httpx
import redis.asyncio as redis
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.tools.location_cache import cache_location
from app.agent.tools.native import RuntimeContext
from app.infra.external.amap import amap

logger = logging.getLogger("agent.tools.location")


class GetIpLocationArgs(BaseModel):
    ip: str | None = Field(default=None, description="Optional client IP address.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _get_ip_location(
    ip: str | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    redis_client = ctx.get("redis_client")
    if not isinstance(redis_client, redis.Redis):
        raise RuntimeError("redis client unavailable")

    session_id = ctx.get("session_id")
    servers_path = ctx.get("servers_path")

    # 1) Prefer frontend device location, same priority as /home/overview
    context = ctx.get("context") if isinstance(ctx.get("context"), dict) else {}
    device_location = _extract_device_location(context)
    if device_location:
        region = await amap.reverse_geocode_region(device_location, servers_path=servers_path)
        city = None
        if isinstance(region, dict):
            city = region.get("district") or region.get("city") or region.get("province")
        readable_address = _format_region(region)
        logger.info(
            "location_tool_result session_id=%s source=device lat=%s lng=%s city=%s address=%s",
            session_id,
            device_location.get("lat"),
            device_location.get("lng"),
            city,
            readable_address,
        )
        await cache_location(redis_client, session_id, device_location.get("lat"), device_location.get("lng"), city)
        return {
            "lat": device_location.get("lat"),
            "lng": device_location.get("lng"),
            "city": city,
            "location_source": "device",
            "address": readable_address,
            "region": region,
        }

    # 2) Fallback to AMap IP location (MCP)
    ip = ip or ctx.get("client_ip")
    if _is_local_ip(ip):
        ip = await _fetch_public_ip()
    if not isinstance(ip, str) or not ip:
        return {"error": "missing_ip"}

    location, city = await amap.get_ip_location(ip, servers_path=servers_path)
    if location:
        region = await amap.reverse_geocode_region(location, servers_path=servers_path)
        readable_address = _format_region(region)
        logger.info(
            "location_tool_result session_id=%s source=amap_ip ip=%s lat=%s lng=%s city=%s address=%s",
            session_id,
            ip,
            location.get("lat"),
            location.get("lng"),
            city,
            readable_address,
        )
        await cache_location(redis_client, session_id, location.get("lat"), location.get("lng"), city)
        return {
            "lat": location.get("lat"),
            "lng": location.get("lng"),
            "city": city,
            "location_source": "amap_ip",
            "address": readable_address,
            "region": region,
        }

    logger.info("location_tool_result session_id=%s source=amap_ip ip=%s error=missing_location", session_id, ip)
    return {"error": "missing_location"}


get_ip_location_tool = StructuredTool.from_function(
    coroutine=_get_ip_location,
    name="get_ip_location",
    description=(
        "Resolve current location using device coordinates first, then IP. "
        "Input: {ip?:string}. Output: {lat,lng,city,location_source,address,region} or {error}."
    ),
    args_schema=GetIpLocationArgs,
    infer_schema=False,
)


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


def _extract_device_location(context: dict[str, Any]) -> dict[str, float] | None:
    env = context.get("environment") if isinstance(context.get("environment"), dict) else {}
    env_location = env.get("location") if isinstance(env.get("location"), dict) else {}
    top_location = context.get("location") if isinstance(context.get("location"), dict) else {}
    location = env_location or top_location
    lat = location.get("lat")
    lng = location.get("lng")
    try:
        lat = float(lat)
        lng = float(lng)
    except (TypeError, ValueError):
        return None
    if lat == 0 or lng == 0:
        return None
    return {"lat": lat, "lng": lng}


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
