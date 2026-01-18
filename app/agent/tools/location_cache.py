from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from app.common.config import settings


async def load_cached_location(
    redis_client: redis.Redis,
    session_id: str | None,
) -> dict[str, Any] | None:
    if not session_id:
        return None
    try:
        raw = await redis_client.get(f"chat:location:{session_id}")
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


async def cache_location(
    redis_client: redis.Redis,
    session_id: str | None,
    lat: float | None,
    lng: float | None,
    city: str | None,
) -> None:
    if not session_id or lat is None or lng is None:
        return
    payload = {"lat": lat, "lng": lng, "city": city or ""}
    try:
        await redis_client.setex(
            f"chat:location:{session_id}",
            settings.CONTEXT_SNAPSHOT_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=True),
        )
    except Exception:
        return
