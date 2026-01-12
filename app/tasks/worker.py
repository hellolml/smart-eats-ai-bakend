from __future__ import annotations

import asyncio
import json
import logging

import redis.asyncio as redis

from app.common.config import settings
from app.tasks.context_summarize import summarize_history

logger = logging.getLogger("worker")


async def run_summary_worker() -> None:
    client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    logger.info("summary worker started queue=%s", settings.CHAT_SUMMARY_QUEUE)
    while True:
        try:
            _, payload = await client.blpop(settings.CHAT_SUMMARY_QUEUE, timeout=5) or (None, None)
            if not payload:
                continue
            data = json.loads(payload)
            await summarize_history(
                client,
                data.get("provider"),
                data.get("session_id"),
                data.get("history") or [],
            )
        except Exception as exc:
            logger.exception("summary worker error=%s", str(exc))
            await asyncio.sleep(1)


if __name__ == "__main__":
    asyncio.run(run_summary_worker())
