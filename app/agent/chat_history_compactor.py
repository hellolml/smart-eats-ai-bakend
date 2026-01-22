from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.agent.llm_adapters import OpenAIWriter
from app.common.config import settings

logger = logging.getLogger("agent.history")


def _estimate_tokens(text: str) -> int:
    if not text:
        return 0
    ascii_count = sum(1 for ch in text if ord(ch) < 128)
    non_ascii_count = len(text) - ascii_count
    return int(ascii_count / 4) + non_ascii_count


def _estimate_history_tokens(history: list[dict[str, Any]]) -> int:
    tokens = 0
    for item in history:
        content = item.get("content") or ""
        tokens += _estimate_tokens(content) + 4
    return tokens


def _format_middle_messages(messages: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    for idx, item in enumerate(messages, start=1):
        role = item.get("role") or "unknown"
        content = (item.get("content") or "").strip()
        if role == "tool":
            name = item.get("name") or "tool"
            role = f"tool:{name}"
        lines.append(f"[{idx}] {role}: {content}")
    return "\n".join(lines)


@lru_cache(maxsize=1)
def _load_prompt_template() -> str:
    path = Path(__file__).with_name("auto_compact_summary_prompt.md")
    return path.read_text(encoding="utf-8")


async def compact_history(
    provider: str | None,
    history: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], str | None]:
    min_messages = settings.CHAT_COMPACT_MIN_MESSAGES
    if len(history) < min_messages:
        return history, None
    token_limit = settings.LLM_MODEL_CONTEXT_SIZE
    if token_limit <= 0:
        return history, None
    total_tokens = _estimate_history_tokens(history)
    ratio = total_tokens / token_limit
    trigger_ratio = settings.CHAT_COMPACT_TRIGGER_RATIO
    if ratio < trigger_ratio:
        return history, None

    tail_ratio = settings.CHAT_COMPACT_TAIL_RATIO
    tail_count = max(1, int(len(history) * tail_ratio))
    tail_count = min(tail_count, max(len(history) - 2, 1))
    head = history[:2]
    tail = history[-tail_count:]
    middle = history[2:-tail_count]
    if not middle:
        return history, None

    middle_text = _format_middle_messages(middle)
    template = _load_prompt_template()
    prompt = template.format(history=middle_text)
    writer = OpenAIWriter(provider=provider)
    chunks: list[str] = []
    try:
        async for delta in writer.stream("你是对话摘要器。", prompt):
            chunks.append(delta)
    except Exception as exc:
        logger.warning("history_compact failed error=%s", str(exc))
        return history, None
    summary = "".join(chunks).strip()
    if not summary:
        logger.warning("history_compact empty_summary tokens=%s ratio=%.2f", total_tokens, ratio)
        return history, None

    summary_message = {"role": "user", "content": summary, "auto_compact": True}
    compacted = head + [summary_message] + tail
    logger.info(
        "history_compact before=%s after=%s tokens=%s limit=%s ratio=%.2f",
        len(history),
        len(compacted),
        total_tokens,
        token_limit,
        ratio,
    )
    return compacted, summary
