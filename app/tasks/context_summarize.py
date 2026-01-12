from __future__ import annotations

from typing import Any
import json

import redis.asyncio as redis

from app.agent.llm_adapters import OpenAIWriter
from app.common.config import settings


def _render_history(history: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for item in history:
        role = item.get("role", "unknown")
        if role == "tool":
            name = item.get("name") or "tool"
            content = item.get("content") or ""
            lines.append(f"[tool:{name}] {content}")
        else:
            content = item.get("content") or ""
            lines.append(f"[{role}] {content}")
    return "\n".join(lines)


async def summarize_history(
    redis_client: redis.Redis,
    provider: str | None,
    session_id: str,
    history: list[dict[str, Any]],
) -> None:
    system = (
        "你是对话摘要器。请用中文简要总结对话，保留用户偏好、关键结论、未解决问题。"
        "限制在 120 字以内。"
    )
    user = _render_history(history)
    writer = OpenAIWriter(provider=provider)
    chunks: list[str] = []
    async for delta in writer.stream(system, user):
        chunks.append(delta)
    summary = "".join(chunks).strip()
    if not summary:
        return
    summary_key = f"chat:summary:{session_id}"
    sig_key = f"chat:summary:{session_id}:sig"
    await redis_client.set(summary_key, summary, ex=settings.CHAT_SUMMARY_TTL_SECONDS)


async def enqueue_summary(
    redis_client: redis.Redis,
    provider: str | None,
    session_id: str,
    history: list[dict[str, Any]],
) -> None:
    payload = {
        "provider": provider,
        "session_id": session_id,
        "history": history,
    }
    await redis_client.rpush(settings.CHAT_SUMMARY_QUEUE, json.dumps(payload, ensure_ascii=True))
