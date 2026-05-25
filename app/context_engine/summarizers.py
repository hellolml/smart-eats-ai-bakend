from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Protocol

from app.context_engine.types import ContextEvent


class CondenseModel(Protocol):
    async def summarize(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        response_schema: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        ...


class LLMStructuredSummarizer:
    def __init__(
        self,
        *,
        model: CondenseModel,
        prompt_path: Path | None = None,
        timeout_seconds: float = 20.0,
    ) -> None:
        self.model = model
        self.prompt_path = prompt_path or Path(__file__).with_name("prompts").joinpath("condense_v1.md")
        self.timeout_seconds = timeout_seconds

    async def __call__(
        self,
        events: list[ContextEvent],
        previous: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        template = self.prompt_path.read_text(encoding="utf-8")
        prompt = template.replace(
            "{previous_summaries}",
            json.dumps(previous or {}, ensure_ascii=False),
        ).replace("{events}", self._format_events(events))
        return await self.model.summarize(
            system="You are a context condensation engine. Return only valid JSON.",
            messages=[{"role": "user", "content": prompt}],
            response_schema=CONDENSE_RESPONSE_SCHEMA,
            timeout_seconds=self.timeout_seconds,
        )

    @staticmethod
    def _format_events(events: list[ContextEvent]) -> str:
        lines: list[str] = []
        for idx, event in enumerate(events, start=1):
            role = event.role or event.type
            content = (event.content or "").strip()
            if not content:
                continue
            lines.append(f"[{idx}] id={event.id} type={event.type} role={role}\n{content}")
        return "\n\n".join(lines)


class WriterCondenseModel:
    def __init__(self, *, provider: str | None = None) -> None:
        self.provider = provider

    async def summarize(
        self,
        *,
        system: str,
        messages: list[dict[str, Any]],
        response_schema: dict[str, Any],
        timeout_seconds: float,
    ) -> dict[str, Any]:
        from app.agent.llm_adapters import OpenAIWriter

        prompt = "\n\n".join(str(item.get("content") or "") for item in messages)
        writer = OpenAIWriter(provider=self.provider)
        chunks: list[str] = []
        async for delta in writer.stream(system, prompt):
            chunks.append(delta)
        return _parse_json_object("".join(chunks))


def _parse_json_object(text: str) -> dict[str, Any]:
    raw = text.strip()
    if raw.startswith("```"):
        raw = raw.strip("`").strip()
        if raw.lower().startswith("json"):
            raw = raw[4:].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        start = raw.find("{")
        end = raw.rfind("}")
        if start < 0 or end <= start:
            raise
        data = json.loads(raw[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("condense model returned non-object JSON")
    return data


CONDENSE_RESPONSE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "segment_summary": {"type": "string"},
        "user_goals": {"type": "array", "items": {"type": "string"}},
        "stable_preferences": {"type": "array", "items": {"type": "string"}},
        "decisions": {"type": "array", "items": {"type": "string"}},
        "tool_results": {"type": "array", "items": {"type": "string"}},
        "open_questions_at_segment_end": {"type": "array", "items": {"type": "string"}},
        "task_state_at_segment_end": {"type": "array", "items": {"type": "string"}},
        "important_entities": {"type": "array", "items": {"type": "string"}},
        "do_not_repeat": {"type": "array", "items": {"type": "string"}},
        "memory_candidates": {"type": "array", "items": {"type": "object"}},
    },
    "required": ["segment_summary"],
}
