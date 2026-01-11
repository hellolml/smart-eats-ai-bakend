from __future__ import annotations

from typing import AsyncGenerator

import redis.asyncio as redis

from app.common.config import settings

_redis_client: redis.Redis | None = None


def _get_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


async def get_redis() -> AsyncGenerator[redis.Redis, None]:
    client = _get_client()
    yield client
