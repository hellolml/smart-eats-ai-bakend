from __future__ import annotations

import math
import hashlib
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from app.context_engine.types import MemoryRecord, namespace_tuple


def _embed(text: str) -> dict[str, float]:
    vec: dict[str, float] = {}
    for token in text.lower().split():
        vec[token] = vec.get(token, 0.0) + 1.0
    for ch in text:
        if ch.strip() and ord(ch) > 127:
            vec[ch] = vec.get(ch, 0.0) + 1.0
    if not vec:
        for ch in text:
            if ch.strip():
                vec[ch] = vec.get(ch, 0.0) + 1.0
    return vec


def _embed_dense(text: str, dimensions: int = 384) -> list[float]:
    values = [0.0 for _ in range(dimensions)]
    for token in text.lower().split() or list(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        values[idx] += sign
    norm = math.sqrt(sum(value * value for value in values))
    if norm:
        values = [value / norm for value in values]
    return values


def _score(query: str, content: str) -> float:
    q = _embed(query)
    c = _embed(content)
    if not q or not c:
        return 0.0
    dot = sum(value * c.get(key, 0.0) for key, value in q.items())
    qn = math.sqrt(sum(value * value for value in q.values()))
    cn = math.sqrt(sum(value * value for value in c.values()))
    return dot / (qn * cn) if qn and cn else 0.0


class InMemoryVectorMemoryStore:
    def __init__(self) -> None:
        self._items: list[MemoryRecord] = []

    async def put(
        self,
        *,
        namespace,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        item = MemoryRecord(
            id=str(uuid4()),
            namespace=namespace_tuple(namespace),
            content=content,
            metadata=metadata or {},
            created_at=datetime.now(timezone.utc),
            updated_at=datetime.now(timezone.utc),
        )
        self._items.append(item)
        return item

    async def search(
        self,
        *,
        namespace,
        query: str,
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        ns = namespace_tuple(namespace)
        rows = [item for item in self._items if item.namespace == ns]
        if metadata_filter:
            rows = [
                item
                for item in rows
                if item.metadata.get("status", "active") == "active"
                and all(item.metadata.get(key) == value for key, value in metadata_filter.items())
            ]
        else:
            rows = [item for item in rows if item.metadata.get("status", "active") == "active"]
        scored = [
            MemoryRecord(
                id=item.id,
                namespace=item.namespace,
                content=item.content,
                metadata=item.metadata,
                score=_score(query, item.content),
                created_at=item.created_at,
                updated_at=item.updated_at,
            )
            for item in rows
        ]
        return sorted(scored, key=lambda item: item.score, reverse=True)[:top_k]

    async def update(
        self,
        *,
        namespace,
        memory_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord | None:
        ns = namespace_tuple(namespace)
        for item in self._items:
            if item.id == memory_id and item.namespace == ns and item.metadata.get("status", "active") == "active":
                item.content = content
                item.metadata.update(metadata or {})
                item.updated_at = datetime.now(timezone.utc)
                return item
        return None

    async def delete(self, *, namespace, memory_id: str) -> bool:
        ns = namespace_tuple(namespace)
        for item in self._items:
            if item.id == memory_id and item.namespace == ns and item.metadata.get("status", "active") == "active":
                item.metadata["status"] = "deleted"
                item.updated_at = datetime.now(timezone.utc)
                return True
        return False


class PgVectorMemoryStore:
    """SQL-backed memory store. Postgres deployments should add a pgvector index.

    The portable implementation stores an embedding payload for SQLite tests and
    uses SQL metadata filtering before local scoring. Production can replace the
    scoring query with native pgvector distance without changing callers.
    """

    def __init__(self, db: Any) -> None:
        self.db = db

    async def put(
        self,
        *,
        namespace,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord:
        from app.infra.models.context_engine import ContextMemoryModel

        item = ContextMemoryModel(
            id=str(uuid4()),
            namespace=list(namespace_tuple(namespace)),
            content=content,
            metadata_json=metadata or {},
            embedding_json=_embed(content),
        )
        self.db.add(item)
        await self.db.commit()
        try:
            from sqlalchemy import text

            vector_literal = "[" + ",".join(f"{value:.6f}" for value in _embed_dense(content)) + "]"
            await self.db.execute(
                text("UPDATE context_memories SET embedding = :embedding WHERE id = :id"),
                {"embedding": vector_literal, "id": item.id},
            )
            await self.db.commit()
        except Exception:
            await self.db.rollback()
        return MemoryRecord(
            id=item.id,
            namespace=tuple(item.namespace),
            content=item.content,
            metadata=item.metadata_json or {},
            created_at=item.created_at,
            updated_at=item.updated_at,
        )

    async def search(
        self,
        *,
        namespace,
        query: str,
        top_k: int = 5,
        metadata_filter: dict[str, Any] | None = None,
    ) -> list[MemoryRecord]:
        from sqlalchemy import select

        from app.infra.models.context_engine import ContextMemoryModel

        stmt = select(ContextMemoryModel).where(ContextMemoryModel.namespace == list(namespace_tuple(namespace)))
        rows = (await self.db.execute(stmt)).scalars().all()
        records: list[MemoryRecord] = []
        for row in rows:
            metadata = row.metadata_json or {}
            if metadata.get("status", "active") != "active":
                continue
            if metadata_filter and not all(metadata.get(key) == value for key, value in metadata_filter.items()):
                continue
            records.append(
                MemoryRecord(
                    id=row.id,
                    namespace=tuple(row.namespace or []),
                    content=row.content,
                    metadata=metadata,
                    score=_score(query, row.content),
                    created_at=row.created_at,
                    updated_at=row.updated_at,
                )
            )
        return sorted(records, key=lambda item: item.score, reverse=True)[:top_k]

    async def update(
        self,
        *,
        namespace,
        memory_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> MemoryRecord | None:
        from sqlalchemy import select

        from app.infra.models.context_engine import ContextMemoryModel

        row = (
            await self.db.execute(
                select(ContextMemoryModel).where(
                    ContextMemoryModel.id == memory_id,
                    ContextMemoryModel.namespace == list(namespace_tuple(namespace)),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        merged_metadata = dict(row.metadata_json or {})
        if merged_metadata.get("status", "active") != "active":
            return None
        merged_metadata.update(metadata or {})
        row.content = content
        row.metadata_json = merged_metadata
        row.embedding_json = _embed(content)
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return MemoryRecord(
            id=row.id,
            namespace=tuple(row.namespace or []),
            content=row.content,
            metadata=row.metadata_json or {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )

    async def delete(self, *, namespace, memory_id: str) -> bool:
        from sqlalchemy import select

        from app.infra.models.context_engine import ContextMemoryModel

        row = (
            await self.db.execute(
                select(ContextMemoryModel).where(
                    ContextMemoryModel.id == memory_id,
                    ContextMemoryModel.namespace == list(namespace_tuple(namespace)),
                )
            )
        ).scalar_one_or_none()
        if row is None:
            return False
        metadata = dict(row.metadata_json or {})
        if metadata.get("status") == "deleted":
            return False
        metadata["status"] = "deleted"
        row.metadata_json = metadata
        row.updated_at = datetime.now(timezone.utc)
        await self.db.commit()
        return True
