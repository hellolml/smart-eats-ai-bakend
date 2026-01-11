from __future__ import annotations

import asyncio
from typing import Any

from sqlalchemy import select

from app.infra.db import AsyncSessionLocal
from app.infra.models.restaurant import RestaurantCache


async def summarize_tags(raw: dict[str, Any] | None) -> list[str]:
    if not raw:
        return ["popular", "local"]
    return ["popular", "value"]


async def run_summary(record_id: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RestaurantCache).where(RestaurantCache.id == record_id)
        )
        record = result.scalar_one_or_none()
        if record is None:
            return
        record.tags = await summarize_tags(record.raw_json)
        await session.commit()


if __name__ == "__main__":
    # Example usage: python app/tasks/restaurant_summarize.py <record_id>
    import sys

    if len(sys.argv) == 2:
        asyncio.run(run_summary(sys.argv[1]))
