from __future__ import annotations

from typing import Sequence
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.models.memory import UserMemory


async def store_memory(db: AsyncSession, user_id: str | None, text: str) -> None:
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


async def search_memories(
    db: AsyncSession,
    user_id: str | None,
    query: str,
    limit: int = 3,
) -> list[str]:
    if not user_id:
        return []
    stmt = select(UserMemory).where(UserMemory.user_id == user_id)
    if query:
        stmt = stmt.where(UserMemory.content.contains(query))
    stmt = stmt.order_by(UserMemory.created_at.desc()).limit(limit)
    result = await db.execute(stmt)
    rows: Sequence[UserMemory] = result.scalars().all()
    return [row.content for row in rows]
