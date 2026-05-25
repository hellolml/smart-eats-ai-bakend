from __future__ import annotations

from typing import Any

from app.context_engine.memory_extractor import MemoryPolicy


class AgenticMemoryService:
    def __init__(self, *, memory_store, policy: MemoryPolicy | None = None) -> None:
        self.memory_store = memory_store
        self.policy = policy or MemoryPolicy()

    async def memory_search(self, *, namespace, query: str, top_k: int = 5) -> list[dict[str, Any]]:
        rows = await self.memory_store.search(namespace=namespace, query=query, top_k=top_k)
        return [self._serialize(item) for item in rows if item.metadata.get("status", "active") == "active"]

    async def memory_write(
        self,
        *,
        namespace,
        content: str,
        kind: str,
        source: str = "agent",
        confidence: float = 0.8,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        candidate = self.policy.normalize_candidate(
            {
                "kind": kind,
                "content": content,
                "confidence": confidence,
                "ttl": (metadata or {}).get("ttl", "none"),
            }
        )
        if candidate is None:
            return {"status": "rejected", "reason": "policy_rejected"}
        item = await self.memory_store.put(
            namespace=namespace,
            content=candidate.content,
            metadata={
                **self.policy.metadata_for(candidate, source=source),
                **(metadata or {}),
            },
        )
        return {"status": "written", "memory": self._serialize(item)}

    async def memory_update(
        self,
        *,
        namespace,
        memory_id: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        item = await self.memory_store.update(
            namespace=namespace,
            memory_id=memory_id,
            content=content,
            metadata=metadata or {},
        )
        if item is None:
            return {"status": "not_found"}
        return {"status": "updated", "memory": self._serialize(item)}

    async def memory_forget(self, *, namespace, memory_id: str) -> dict[str, Any]:
        deleted = await self.memory_store.delete(namespace=namespace, memory_id=memory_id)
        return {"status": "deleted" if deleted else "not_found"}

    @staticmethod
    def _serialize(item) -> dict[str, Any]:
        return {
            "id": item.id,
            "content": item.content,
            "metadata": item.metadata,
            "score": item.score,
        }
