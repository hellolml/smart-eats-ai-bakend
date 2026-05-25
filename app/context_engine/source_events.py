from __future__ import annotations

from typing import Any

from app.context_engine.memory import _score
from app.context_engine.types import SourceEventHit


class SourceEventRetriever:
    def __init__(self, store) -> None:
        self.store = store

    async def search_events(
        self,
        *,
        namespace,
        query: str,
        thread_id: str | None = None,
        top_k: int = 8,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[SourceEventHit]:
        resolved_thread_id = thread_id or self._thread_id_from_namespace(namespace)
        if not resolved_thread_id:
            return []
        rows = await self.store.list_events(resolved_thread_id)
        hits: list[SourceEventHit] = []
        for event in rows:
            metadata = dict(event.payload or {})
            if metadata_filter and not all(metadata.get(key) == value for key, value in metadata_filter.items()):
                continue
            content = event.content or ""
            score = _score(query, content)
            if query.strip() and query.strip() in content:
                score += 1.0
            elif score < 0.35:
                continue
            if score <= 0:
                continue
            hits.append(
                SourceEventHit(
                    event_id=event.id,
                    thread_id=event.thread_id,
                    content_preview=content[:500],
                    score=score,
                    metadata={
                        "type": event.type,
                        "role": event.role,
                        **metadata,
                    },
                )
            )
        return sorted(hits, key=lambda item: item.score, reverse=True)[:top_k]

    @staticmethod
    def _thread_id_from_namespace(namespace) -> str | None:
        if isinstance(namespace, str):
            return namespace
        if isinstance(namespace, (list, tuple)) and len(namespace) >= 2 and namespace[0] == "thread":
            return str(namespace[1])
        return None
