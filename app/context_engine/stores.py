from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.context_engine.types import CompactionRun, ContextCondensation, ContextEvent


class InMemoryConversationStore:
    def __init__(self) -> None:
        self._events: dict[str, list[ContextEvent]] = {}
        self._condensations: dict[str, list[ContextCondensation]] = {}
        self._compaction_runs: dict[str, list[CompactionRun]] = {}

    async def ensure_thread(self, thread_id: str, *, user_id: str | None = None, scene: str = "chat") -> None:
        self._events.setdefault(thread_id, [])
        self._condensations.setdefault(thread_id, [])
        self._compaction_runs.setdefault(thread_id, [])

    async def append_event(self, event: ContextEvent) -> ContextEvent:
        self._events.setdefault(event.thread_id, [])
        if event.created_at is None:
            event.created_at = datetime.now(timezone.utc)
        self._events[event.thread_id].append(event)
        return event

    async def list_events(self, thread_id: str) -> list[ContextEvent]:
        return list(self._events.get(thread_id, []))

    async def save_condensation(
        self,
        *,
        thread_id: str,
        summary: str,
        summary_json: dict[str, Any],
        covered_event_ids: list[str],
        summary_offset: int,
        status: str,
        model: str | None = None,
        prompt_version: str | None = None,
        token_before: int = 0,
        token_after: int = 0,
    ) -> ContextCondensation:
        item = ContextCondensation(
            id=str(uuid4()),
            thread_id=thread_id,
            summary=summary,
            summary_json=summary_json,
            covered_event_ids=list(covered_event_ids),
            summary_offset=summary_offset,
            status=status,
            model=model,
            prompt_version=prompt_version,
            token_before=token_before,
            token_after=token_after,
            created_at=datetime.now(timezone.utc),
        )
        self._condensations.setdefault(thread_id, []).append(item)
        return item

    async def list_condensations(self, thread_id: str) -> list[ContextCondensation]:
        return list(self._condensations.get(thread_id, []))

    async def save_compaction_run(self, run: CompactionRun) -> CompactionRun:
        if run.created_at is None:
            run.created_at = datetime.now(timezone.utc)
        self._compaction_runs.setdefault(run.thread_id, []).append(run)
        return run

    async def list_compaction_runs(self, thread_id: str) -> list[CompactionRun]:
        return list(self._compaction_runs.get(thread_id, []))


class SqlConversationStore:
    def __init__(self, db: Any) -> None:
        self.db = db

    async def ensure_thread(self, thread_id: str, *, user_id: str | None = None, scene: str = "chat") -> None:
        from sqlalchemy import select

        from app.infra.models.context_engine import ContextThread

        result = await self.db.execute(select(ContextThread).where(ContextThread.id == thread_id))
        if result.scalar_one_or_none() is not None:
            return
        self.db.add(ContextThread(id=thread_id, user_id=user_id, scene=scene))
        await self.db.commit()

    async def append_event(self, event: ContextEvent) -> ContextEvent:
        from app.context_engine.memory import _embed, _embed_dense
        from app.infra.models.context_engine import ContextEventEmbeddingModel, ContextEventModel

        if event.created_at is None:
            event.created_at = datetime.now(timezone.utc)
        embedding_id = str(uuid4())
        self.db.add(
            ContextEventModel(
                id=event.id,
                thread_id=event.thread_id,
                type=event.type,
                role=event.role,
                content=event.content,
                payload_json=event.payload,
                token_estimate=event.token_estimate,
                pinned=event.pinned,
                critical=event.critical,
                created_at=event.created_at,
            )
        )
        self.db.add(
            ContextEventEmbeddingModel(
                id=embedding_id,
                event_id=event.id,
                thread_id=event.thread_id,
                namespace=["thread", event.thread_id],
                content_preview=(event.content or "")[:500],
                embedding_json=_embed(event.content or ""),
                metadata_json={"type": event.type, "role": event.role},
                created_at=event.created_at,
            )
        )
        await self.db.commit()
        try:
            from sqlalchemy import text

            vector_literal = "[" + ",".join(f"{value:.6f}" for value in _embed_dense(event.content or "")) + "]"
            await self.db.execute(
                text("UPDATE context_event_embeddings SET embedding = :embedding WHERE id = :id"),
                {"embedding": vector_literal, "id": embedding_id},
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
        return event

    async def list_events(self, thread_id: str) -> list[ContextEvent]:
        from sqlalchemy import select

        from app.infra.models.context_engine import ContextEventModel

        rows = (
            await self.db.execute(
                select(ContextEventModel)
                .where(ContextEventModel.thread_id == thread_id)
                .order_by(ContextEventModel.created_at, ContextEventModel.id)
            )
        ).scalars().all()
        return [
            ContextEvent(
                id=row.id,
                thread_id=row.thread_id,
                type=row.type,
                role=row.role,
                content=row.content,
                payload=row.payload_json or {},
                token_estimate=row.token_estimate or 0,
                pinned=bool(row.pinned),
                critical=bool(row.critical),
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def save_condensation(
        self,
        *,
        thread_id: str,
        summary: str,
        summary_json: dict[str, Any],
        covered_event_ids: list[str],
        summary_offset: int,
        status: str,
        model: str | None = None,
        prompt_version: str | None = None,
        token_before: int = 0,
        token_after: int = 0,
    ) -> ContextCondensation:
        from app.infra.models.context_engine import ContextCondensationModel

        item = ContextCondensation(
            id=str(uuid4()),
            thread_id=thread_id,
            summary=summary,
            summary_json=summary_json,
            covered_event_ids=list(covered_event_ids),
            summary_offset=summary_offset,
            status=status,
            model=model,
            prompt_version=prompt_version,
            token_before=token_before,
            token_after=token_after,
            created_at=datetime.now(timezone.utc),
        )
        self.db.add(
            ContextCondensationModel(
                id=item.id,
                thread_id=thread_id,
                summary=summary,
                summary_json=summary_json,
                covered_event_ids=covered_event_ids,
                summary_offset=summary_offset,
                status=status,
                model=model,
                prompt_version=prompt_version,
                token_before=token_before,
                token_after=token_after,
                created_at=item.created_at,
            )
        )
        await self.db.commit()
        return item

    async def list_condensations(self, thread_id: str) -> list[ContextCondensation]:
        from sqlalchemy import select

        from app.infra.models.context_engine import ContextCondensationModel

        rows = (
            await self.db.execute(
                select(ContextCondensationModel)
                .where(ContextCondensationModel.thread_id == thread_id)
                .order_by(ContextCondensationModel.created_at, ContextCondensationModel.id)
            )
        ).scalars().all()
        return [
            ContextCondensation(
                id=row.id,
                thread_id=row.thread_id,
                summary=row.summary,
                summary_json=row.summary_json or {},
                covered_event_ids=list(row.covered_event_ids or []),
                summary_offset=row.summary_offset,
                status=row.status,
                model=row.model,
                prompt_version=row.prompt_version,
                token_before=row.token_before or 0,
                token_after=row.token_after or 0,
                created_at=row.created_at,
            )
            for row in rows
        ]

    async def save_compaction_run(self, run: CompactionRun) -> CompactionRun:
        from app.infra.models.context_engine import ContextCompactionRunModel

        if run.created_at is None:
            run.created_at = datetime.now(timezone.utc)
        self.db.add(
            ContextCompactionRunModel(
                id=run.id,
                thread_id=run.thread_id,
                condensation_id=run.condensation_id,
                trigger_reason=run.trigger_reason,
                model=run.model,
                prompt_version=run.prompt_version,
                input_event_count=run.input_event_count,
                input_token_estimate=run.input_token_estimate,
                output_token_estimate=run.output_token_estimate,
                compression_ratio=run.compression_ratio,
                latency_ms=run.latency_ms,
                status=run.status,
                error_type=run.error_type,
                error_message=run.error_message,
                quality_score=run.quality_score,
                metadata_json=run.metadata,
                created_at=run.created_at,
            )
        )
        await self.db.commit()
        return run

    async def list_compaction_runs(self, thread_id: str) -> list[CompactionRun]:
        from sqlalchemy import select

        from app.infra.models.context_engine import ContextCompactionRunModel

        rows = (
            await self.db.execute(
                select(ContextCompactionRunModel)
                .where(ContextCompactionRunModel.thread_id == thread_id)
                .order_by(ContextCompactionRunModel.created_at, ContextCompactionRunModel.id)
            )
        ).scalars().all()
        return [
            CompactionRun(
                id=row.id,
                thread_id=row.thread_id,
                condensation_id=row.condensation_id,
                trigger_reason=row.trigger_reason,
                model=row.model,
                prompt_version=row.prompt_version,
                input_event_count=row.input_event_count or 0,
                input_token_estimate=row.input_token_estimate or 0,
                output_token_estimate=row.output_token_estimate or 0,
                compression_ratio=row.compression_ratio or 0.0,
                latency_ms=row.latency_ms or 0,
                status=row.status,
                error_type=row.error_type,
                error_message=row.error_message,
                quality_score=row.quality_score,
                metadata=row.metadata_json or {},
                created_at=row.created_at,
            )
            for row in rows
        ]
