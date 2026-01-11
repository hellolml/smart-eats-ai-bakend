from __future__ import annotations

import asyncio

from sqlalchemy import delete

from app.infra.db import AsyncSessionLocal, init_db
from app.infra.models.restaurant import RestaurantCache


async def cleanup() -> None:
    await init_db()
    async with AsyncSessionLocal() as session:
        stmt = delete(RestaurantCache).where(RestaurantCache.raw_json["mock"].as_boolean() == True)  # noqa: E712
        await session.execute(stmt)
        await session.commit()


if __name__ == "__main__":
    asyncio.run(cleanup())
