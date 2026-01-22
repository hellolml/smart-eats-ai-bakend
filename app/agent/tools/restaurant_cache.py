from __future__ import annotations

import json
from typing import Any

import redis.asyncio as redis

from app.common.config import settings


async def cache_restaurants(
    redis_client: redis.Redis,
    session_id: str | None,
    items: list[dict[str, Any]] | None,
) -> None:
    if not session_id or not items:
        return
    payload = []
    for item in items:
        if not isinstance(item, dict):
            continue
        provider_id = item.get("provider_id")
        name = item.get("name") or item.get("title")
        if not provider_id or not name:
            continue
        payload.append(
            {
                "provider_id": provider_id,
                "name": name,
                "geo": item.get("geo"),
            }
        )
    if not payload:
        return
    try:
        await redis_client.setex(
            f"chat:restaurants:{session_id}",
            settings.CONTEXT_SNAPSHOT_TTL_SECONDS,
            json.dumps(payload, ensure_ascii=True),
        )
    except Exception:
        return


async def load_cached_restaurants(
    redis_client: redis.Redis,
    session_id: str | None,
) -> list[dict[str, Any]] | None:
    if not session_id:
        return None
    try:
        raw = await redis_client.get(f"chat:restaurants:{session_id}")
    except Exception:
        return None
    if not raw:
        return None
    try:
        data = json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return None
    return data if isinstance(data, list) else None
