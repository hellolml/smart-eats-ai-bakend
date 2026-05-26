from __future__ import annotations

from typing import Any

from app.agent.tools_registry import register_tool
from app.agent.langgraph_context import (
    forget_user_memory,
    load_user_memories,
    search_source_events,
    update_user_memory,
    write_user_memory,
)


def _store(args: dict[str, Any]) -> Any:
    store = args.get("langgraph_store")
    if store is None:
        raise RuntimeError("langgraph store unavailable")
    return store


def _user_id(args: dict[str, Any]) -> str:
    user_id = args.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise RuntimeError("user_id unavailable")
    return user_id


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
    return await load_user_memories(
        _store(args),
        user_id=_user_id(args),
        query=str(args.get("query") or ""),
        limit=int(args.get("top_k") or 5),
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
    return await write_user_memory(
        _store(args),
        user_id=_user_id(args),
        content=str(args.get("content") or ""),
        kind=str(args.get("kind") or ""),
        confidence=float(args.get("confidence") or 0.8),
        metadata={"source": "agent_tool"},
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
    return await update_user_memory(
        _store(args),
        user_id=_user_id(args),
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
    return await forget_user_memory(
        _store(args),
        user_id=_user_id(args),
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
    session_id = args.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        raise RuntimeError("session_id unavailable")
    return await search_source_events(
        _store(args),
        thread_id=session_id,
        query=str(args.get("query") or ""),
        top_k=int(args.get("top_k") or 8),
    )
