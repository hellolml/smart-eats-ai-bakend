from __future__ import annotations

import time

import redis.asyncio as redis

from app.common.errors import AppError, RATE_LIMITED


async def check_rate_limit(
    redis_client: redis.Redis,
    key: str,
    limit: int,
    window_seconds: int,
) -> bool:
    now_ms = int(time.time() * 1000)
    window_ms = window_seconds * 1000
    min_score = now_ms - window_ms

    pipe = redis_client.pipeline()
    pipe.zremrangebyscore(key, 0, min_score)
    pipe.zadd(key, {str(now_ms): now_ms})
    pipe.zcard(key)
    pipe.expire(key, window_seconds)
    _, _, count, _ = await pipe.execute()
    return count <= limit


async def ensure_rate_limit(
    redis_client: redis.Redis,
    key: str,
    limit: int,
    window_seconds: int,
) -> None:
    allowed = await check_rate_limit(redis_client, key, limit, window_seconds)
    if not allowed:
        raise AppError(code=RATE_LIMITED, message="rate limited", http_status=429)
