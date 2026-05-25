from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

from app.context_engine.types import MemoryCandidate, namespace_tuple


class MemoryPolicy:
    writable_kinds = {"preference", "fact", "constraint", "profile", "habit"}

    def __init__(self, *, min_confidence: float = 0.6) -> None:
        self.min_confidence = min_confidence

    def normalize_candidate(self, raw: Any) -> MemoryCandidate | None:
        if not isinstance(raw, dict):
            return None
        kind = str(raw.get("kind") or "").strip()
        content = str(raw.get("content") or "").strip()
        if kind not in self.writable_kinds or not content:
            return None
        try:
            confidence = float(raw.get("confidence", 0.0))
        except (TypeError, ValueError):
            confidence = 0.0
        if confidence < self.min_confidence:
            return None
        ttl = str(raw.get("ttl") or "none").strip() or "none"
        if ttl == "session":
            return None
        source_event_ids = raw.get("source_event_ids")
        if not isinstance(source_event_ids, list):
            source_event_ids = []
        return MemoryCandidate(
            kind=kind,
            content=content,
            confidence=confidence,
            ttl=ttl,
            source_event_ids=[str(item) for item in source_event_ids if str(item)],
            metadata={key: value for key, value in raw.items() if key not in {"kind", "content"}},
        )

    def metadata_for(
        self,
        candidate: MemoryCandidate,
        *,
        source: str,
        condensation_id: str | None = None,
    ) -> dict[str, Any]:
        metadata = dict(candidate.metadata)
        metadata.update(
            {
                "kind": candidate.kind,
                "source": source,
                "confidence": candidate.confidence,
                "ttl": candidate.ttl,
                "source_event_ids": candidate.source_event_ids,
                "source_condensation_id": condensation_id,
                "status": "active",
            }
        )
        expires_at = self._expires_at(candidate.ttl)
        if expires_at:
            metadata["ttl_expires_at"] = expires_at.isoformat()
        return metadata

    @staticmethod
    def _expires_at(ttl: str) -> datetime | None:
        if ttl == "days_30":
            return datetime.now(timezone.utc) + timedelta(days=30)
        return None


class MemoryExtractor:
    def __init__(self, *, memory_store, policy: MemoryPolicy | None = None) -> None:
        self.memory_store = memory_store
        self.policy = policy or MemoryPolicy()

    async def persist_from_summary(
        self,
        summary_json: dict[str, Any],
        *,
        namespace,
        condensation_id: str | None = None,
    ) -> list[Any]:
        if not namespace_tuple(namespace):
            return []
        written = []
        for raw in summary_json.get("memory_candidates") or []:
            candidate = self.policy.normalize_candidate(raw)
            if candidate is None:
                continue
            record = await self.memory_store.put(
                namespace=namespace,
                content=candidate.content,
                metadata=self.policy.metadata_for(
                    candidate,
                    source="condensation",
                    condensation_id=condensation_id,
                ),
            )
            written.append(record)
        return written
