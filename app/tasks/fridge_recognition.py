from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from app.common.config import settings
from app.infra.db import AsyncSessionLocal
from app.infra.models.fridge import RecognitionJob
from app.infra.redis import get_redis


async def _publish_event(job_id: str, event: dict) -> None:
    async for redis_client in get_redis():
        key = f"fridge:recognition:events:{job_id}"
        await redis_client.rpush(key, json.dumps(event, ensure_ascii=True))
        await redis_client.expire(key, settings.RECOGNITION_EVENT_TTL_SECONDS)
        break


async def process_job(job_id: str) -> None:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RecognitionJob).where(RecognitionJob.id == job_id)
        )
        job = result.scalar_one_or_none()
        if job is None or job.status not in {"queued", "running"}:
            return

        job.status = "running"
        await session.commit()
        await _publish_event(job_id, {"event": "status", "data": "running"})

        await _publish_event(job_id, {"event": "progress", "data": {"percent": 50}})
        job.result_json = {
            "items": [
                {"name": "egg", "quantity": 2, "unit": "pcs"},
                {"name": "tomato", "quantity": 3, "unit": "pcs"},
            ],
            "request_id": str(uuid4()),
        }
        job.status = "success"
        job.finished_at = datetime.now(timezone.utc)
        await session.commit()
        await _publish_event(job_id, {"event": "progress", "data": {"percent": 100}})
        await _publish_event(job_id, {"event": "final", "data": job.result_json})


async def process_queued_jobs(limit: int = 10) -> int:
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(RecognitionJob)
            .where(RecognitionJob.status == "queued")
            .limit(limit)
        )
        jobs = result.scalars().all()

    for job in jobs:
        await process_job(job.id)
    return len(jobs)


if __name__ == "__main__":
    asyncio.run(process_queued_jobs())
