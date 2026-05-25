from __future__ import annotations

import inspect
import time
from typing import Any, Awaitable, Callable
from uuid import uuid4

from app.context_engine.memory_extractor import MemoryExtractor, MemoryPolicy
from app.context_engine.renderers import render_condensation_summary
from app.context_engine.tokenizer import ApproxTokenCounter
from app.context_engine.types import CompactionRun, ContextCondensation, ContextEvent
from app.context_engine.view import ViewBuilder

SummaryFn = Callable[[list[ContextEvent], dict[str, Any] | None], dict[str, Any] | Awaitable[dict[str, Any]]]


class Condenser:
    def __init__(
        self,
        *,
        store,
        token_counter: ApproxTokenCounter,
        summarizer: SummaryFn | None = None,
        keep_head: int = 2,
        keep_tail: int = 8,
        min_events: int = 3,
        model: str | None = None,
        prompt_version: str = "context-condense-v1",
        memory_store=None,
        memory_policy: MemoryPolicy | None = None,
    ) -> None:
        self.store = store
        self.token_counter = token_counter
        self.summarizer = summarizer or self._default_summarizer
        self.keep_head = keep_head
        self.keep_tail = keep_tail
        self.min_events = min_events
        self.model = model
        self.prompt_version = prompt_version
        self.memory_store = memory_store
        self.memory_policy = memory_policy or MemoryPolicy()

    async def condense(self, thread_id: str, *, memory_namespace=None) -> ContextCondensation | None:
        events = await self.store.list_events(thread_id)
        view = await ViewBuilder(self.store).build(thread_id)
        covered = view.covered_event_ids
        candidate = self._select_candidate(events, covered)
        if len(candidate) < self.min_events:
            return None

        started = time.monotonic()
        token_before = sum(self.token_counter.count_event(item) for item in candidate)
        error_type = None
        error_message = None
        try:
            previous = {"summaries": [item.summary_json for item in await self.store.list_condensations(thread_id)]}
            result = self.summarizer(candidate, previous)
            if inspect.isawaitable(result):
                result = await result
            summary_json = self._normalize_summary(result)
            status = "completed"
        except Exception as exc:
            error_type = type(exc).__name__
            error_message = str(exc)
            summary_json = self._normalize_summary({"segment_summary": f"摘要生成失败：{exc}"})
            status = "failed"

        summary = render_condensation_summary(summary_json)
        if not summary:
            summary = "这段历史已压缩。"
        token_after = self.token_counter.count_text(summary)
        first_idx = events.index(candidate[0])
        covered_event_ids = [item.id for item in candidate] if status == "completed" else []
        condensation = await self.store.save_condensation(
            thread_id=thread_id,
            summary=summary,
            summary_json=summary_json,
            covered_event_ids=covered_event_ids,
            summary_offset=first_idx,
            status=status,
            model=self.model,
            prompt_version=self.prompt_version,
            token_before=token_before,
            token_after=token_after,
        )
        written_memories = []
        if status == "completed" and self.memory_store is not None and memory_namespace is not None:
            written_memories = await MemoryExtractor(
                memory_store=self.memory_store,
                policy=self.memory_policy,
            ).persist_from_summary(
                summary_json,
                namespace=memory_namespace,
                condensation_id=condensation.id,
            )
        await self._save_compaction_run(
            thread_id=thread_id,
            condensation=condensation,
            candidate=candidate,
            token_before=token_before,
            token_after=token_after,
            latency_ms=int((time.monotonic() - started) * 1000),
            status=status,
            error_type=error_type,
            error_message=error_message,
            memory_write_count=len(written_memories),
        )
        return condensation

    def _select_candidate(self, events: list[ContextEvent], covered: set[str]) -> list[ContextEvent]:
        if len(events) <= self.keep_head + self.keep_tail:
            return []
        middle = events[self.keep_head : max(self.keep_head, len(events) - self.keep_tail)]
        segments: list[list[ContextEvent]] = []
        current: list[ContextEvent] = []
        for event in middle:
            if event.id in covered or event.pinned or event.critical or event.type == "condensation":
                if current:
                    segments.append(current)
                    current = []
                continue
            current.append(event)
        if current:
            segments.append(current)
        if not segments:
            return []
        return max(segments, key=len)

    def _normalize_summary(self, raw: dict[str, Any]) -> dict[str, Any]:
        segment_summary = str(raw.get("segment_summary") or raw.get("summary") or "").strip()
        return {
            "summary": segment_summary,
            "segment_summary": segment_summary,
            "user_goals": self._list(raw.get("user_goals")),
            "stable_preferences": self._list(raw.get("stable_preferences")),
            "decisions": self._list(raw.get("decisions") or raw.get("decisions_made")),
            "tool_results": self._list(raw.get("tool_results")),
            "open_questions_at_segment_end": self._list(
                raw.get("open_questions_at_segment_end") or raw.get("open_questions")
            ),
            "task_state_at_segment_end": self._list(
                raw.get("task_state_at_segment_end") or raw.get("current_task_state")
            ),
            "important_entities": self._list(raw.get("important_entities")),
            "do_not_repeat": self._list(raw.get("do_not_repeat")),
            "memory_candidates": raw.get("memory_candidates") if isinstance(raw.get("memory_candidates"), list) else [],
        }

    @staticmethod
    def _list(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _default_summarizer(events: list[ContextEvent], previous: dict[str, Any] | None = None) -> dict[str, Any]:
        lines = []
        for event in events:
            role = event.role or event.type
            content = (event.content or "").strip()
            if content:
                lines.append(f"{role}: {content}")
        text = "\n".join(lines)
        return {"segment_summary": text[:1200]}

    async def _save_compaction_run(
        self,
        *,
        thread_id: str,
        condensation: ContextCondensation,
        candidate: list[ContextEvent],
        token_before: int,
        token_after: int,
        latency_ms: int,
        status: str,
        error_type: str | None,
        error_message: str | None,
        memory_write_count: int,
    ) -> None:
        if not hasattr(self.store, "save_compaction_run"):
            return
        compression_ratio = token_after / token_before if token_before else 0.0
        quality_score = self._quality_score(condensation.summary_json, status=status)
        await self.store.save_compaction_run(
            CompactionRun(
                id=str(uuid4()),
                thread_id=thread_id,
                condensation_id=condensation.id,
                model=self.model,
                prompt_version=self.prompt_version,
                input_event_count=len(candidate),
                input_token_estimate=token_before,
                output_token_estimate=token_after,
                compression_ratio=compression_ratio,
                latency_ms=latency_ms,
                status=status,
                error_type=error_type,
                error_message=error_message,
                quality_score=quality_score,
                metadata={"memory_write_count": memory_write_count},
            )
        )

    @staticmethod
    def _quality_score(summary_json: dict[str, Any], *, status: str) -> float:
        if status != "completed":
            return 0.0
        fields = [
            "segment_summary",
            "user_goals",
            "stable_preferences",
            "decisions",
            "tool_results",
            "open_questions_at_segment_end",
            "task_state_at_segment_end",
            "important_entities",
            "do_not_repeat",
        ]
        filled = 0
        for field in fields:
            value = summary_json.get(field)
            if isinstance(value, list) and value:
                filled += 1
            elif isinstance(value, str) and value.strip():
                filled += 1
        return round(filled / len(fields), 3)
