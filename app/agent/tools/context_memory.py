from __future__ import annotations

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.agent.tools_registry import register_tool
from app.context_engine.agentic_memory import AgenticMemoryService
from app.context_engine.memory import PgVectorMemoryStore
from app.context_engine.memory_extractor import MemoryPolicy
from app.context_engine.source_events import SourceEventRetriever
from app.context_engine.stores import SqlConversationStore


def _service(db: AsyncSession) -> AgenticMemoryService:
    return AgenticMemoryService(memory_store=PgVectorMemoryStore(db), policy=MemoryPolicy())


def _namespace(args: dict[str, Any]) -> tuple[str, str]:
    user_id = args.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise RuntimeError("user_id unavailable")
    return ("user", user_id)


@register_tool(
    name="memory_search",
    description="Search long-term user memories relevant to the current task.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["query"],
    },
    output_schema={"type": "array", "items": {"type": "object"}},
)
async def memory_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    db = args.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    return await _service(db).memory_search(
        namespace=_namespace(args),
        query=str(args.get("query") or ""),
        top_k=int(args.get("top_k") or 5),
    )


@register_tool(
    name="memory_write",
    description="Write an explicit durable user preference, constraint, fact, profile item, or habit.",
    input_schema={
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "kind": {"type": "string"},
            "confidence": {"type": "number"},
        },
        "required": ["content", "kind"],
    },
    output_schema={"type": "object"},
)
async def memory_write(args: dict[str, Any]) -> dict[str, Any]:
    db = args.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    return await _service(db).memory_write(
        namespace=_namespace(args),
        content=str(args.get("content") or ""),
        kind=str(args.get("kind") or ""),
        source="agent_tool",
        confidence=float(args.get("confidence") or 0.8),
    )


@register_tool(
    name="memory_update",
    description="Update an existing long-term memory when the user corrects or supersedes it.",
    input_schema={
        "type": "object",
        "properties": {
            "memory_id": {"type": "string"},
            "content": {"type": "string"},
        },
        "required": ["memory_id", "content"],
    },
    output_schema={"type": "object"},
)
async def memory_update(args: dict[str, Any]) -> dict[str, Any]:
    db = args.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    return await _service(db).memory_update(
        namespace=_namespace(args),
        memory_id=str(args.get("memory_id") or ""),
        content=str(args.get("content") or ""),
    )


@register_tool(
    name="memory_forget",
    description="Delete a long-term memory when the user asks to forget it.",
    input_schema={
        "type": "object",
        "properties": {
            "memory_id": {"type": "string"},
        },
        "required": ["memory_id"],
    },
    output_schema={"type": "object"},
)
async def memory_forget(args: dict[str, Any]) -> dict[str, Any]:
    db = args.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    return await _service(db).memory_forget(
        namespace=_namespace(args),
        memory_id=str(args.get("memory_id") or ""),
    )


@register_tool(
    name="source_event_search",
    description="Search original persisted conversation events when summaries are not detailed enough.",
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string"},
            "top_k": {"type": "integer"},
        },
        "required": ["query"],
    },
    output_schema={"type": "array", "items": {"type": "object"}},
)
async def source_event_search(args: dict[str, Any]) -> list[dict[str, Any]]:
    db = args.get("db")
    if not isinstance(db, AsyncSession):
        raise RuntimeError("db session unavailable")
    session_id = args.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("session_id unavailable")
    hits = await SourceEventRetriever(SqlConversationStore(db)).search_events(
        namespace=("thread", session_id),
        thread_id=session_id,
        query=str(args.get("query") or ""),
        top_k=int(args.get("top_k") or 8),
    )
    return [
        {
            "event_id": hit.event_id,
            "content_preview": hit.content_preview,
            "score": hit.score,
            "metadata": hit.metadata,
        }
        for hit in hits
    ]
