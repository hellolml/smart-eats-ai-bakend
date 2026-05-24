from __future__ import annotations

import json
import logging
from typing import Any, Sequence
from uuid import uuid4

import redis.asyncio as redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.models.memory import UserMemory

logger = logging.getLogger("agent")

_MEMORY_CACHE_TTL = 300  # 5 分钟
_MEMORY_CACHE_LIMIT = 10  # 缓存最近 10 条


def _cache_key(user_id: str) -> str:
    return f"user:memories:{user_id}"


async def store_memory(
    db: AsyncSession,
    user_id: str | None,
    text: str,
    redis_client: redis.Redis | None = None,
) -> None:
    if not user_id:
        return
    content = text.strip()
    if not content:
        return
    memory = UserMemory(
        id=str(uuid4()),
        user_id=user_id,
        content=content,
    )
    db.add(memory)
    await db.commit()
    # 写入后失效缓存
    if redis_client:
        try:
            await redis_client.delete(_cache_key(user_id))
        except Exception:
            pass


async def put_user_memory(
    namespace: tuple[str, ...] | str,
    value: str | dict[str, Any],
    *,
    db: AsyncSession,
    redis_client: redis.Redis | None = None,
) -> None:
    user_id = _user_id_from_namespace(namespace)
    content = value.get("content") if isinstance(value, dict) else value
    await store_memory(db, user_id, str(content or ""), redis_client=redis_client)


async def search_user_memories(
    namespace: tuple[str, ...] | str,
    query: str,
    *,
    db: AsyncSession,
    redis_client: redis.Redis | None = None,
    limit: int = 3,
) -> list[dict[str, str]]:
    user_id = _user_id_from_namespace(namespace)
    rows = await search_memories(db, user_id, query, limit=limit, redis_client=redis_client)
    return [{"namespace": user_id or "", "content": item} for item in rows]


class UserMemoryStoreAdapter:
    def __init__(
        self,
        *,
        db: AsyncSession,
        redis_client: redis.Redis | None = None,
        namespace: tuple[str, ...] | str,
    ) -> None:
        self.db = db
        self.redis_client = redis_client
        self.namespace = namespace

    async def put(self, value: str | dict[str, Any]) -> None:
        await put_user_memory(
            self.namespace,
            value,
            db=self.db,
            redis_client=self.redis_client,
        )

    async def search(self, query: str, *, limit: int = 3) -> list[dict[str, str]]:
        return await search_user_memories(
            self.namespace,
            query,
            db=self.db,
            redis_client=self.redis_client,
            limit=limit,
        )


def _user_id_from_namespace(namespace: tuple[str, ...] | str) -> str | None:
    if isinstance(namespace, str):
        return namespace or None
    if not namespace:
        return None
    return str(namespace[-1] or "") or None


async def search_memories(
    db: AsyncSession,
    user_id: str | None,
    query: str,
    limit: int = 3,
    redis_client: redis.Redis | None = None,
) -> list[str]:
    if not user_id:
        return []

    # 优先从 Redis 缓存读取
    if redis_client:
        try:
            cached = await redis_client.get(_cache_key(user_id))
            if cached:
                all_memories: list[str] = json.loads(cached)
                return all_memories[:limit]
        except Exception:
            pass

    # 缓存未命中，查 DB（取最近的记忆，不做模糊匹配，由 LLM 判断相关性）
    stmt = (
        select(UserMemory)
        .where(UserMemory.user_id == user_id)
        .order_by(UserMemory.created_at.desc())
        .limit(_MEMORY_CACHE_LIMIT)
    )
    result = await db.execute(stmt)
    rows: Sequence[UserMemory] = result.scalars().all()
    all_memories = [row.content for row in rows]

    # 写入 Redis 缓存
    if redis_client and all_memories:
        try:
            await redis_client.set(
                _cache_key(user_id),
                json.dumps(all_memories, ensure_ascii=False),
                ex=_MEMORY_CACHE_TTL,
            )
        except Exception:
            pass

    return all_memories[:limit]
