from typing import Any

from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.agent.langgraph_context import (
    forget_user_memory,
    load_user_memories,
    search_source_events,
    update_user_memory,
    write_user_memory,
)
from app.agent.tools.native import RuntimeContext


def _store(runtime_context: dict[str, Any]) -> Any:
    store = runtime_context.get("langgraph_store")
    if store is None:
        raise RuntimeError("langgraph store unavailable")
    return store


def _user_id(runtime_context: dict[str, Any]) -> str:
    user_id = runtime_context.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        raise RuntimeError("user_id unavailable")
    return user_id


def _runtime_unavailable(ctx: dict[str, Any]) -> str | None:
    if ctx.get("langgraph_store") is None:
        return "store_unavailable"
    user_id = ctx.get("user_id")
    if not isinstance(user_id, str) or not user_id:
        return "user_id_unavailable"
    return None


class MemorySearchArgs(BaseModel):
    query: str = Field(..., description="Search query for long-term user memories.")
    top_k: int | None = Field(default=None, description="Maximum number of memories to return.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _memory_search(
    query: str,
    top_k: int | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ctx = runtime_context or {}
    unavailable = _runtime_unavailable(ctx)
    if unavailable:
        return [{"error": unavailable}]
    return await load_user_memories(
        _store(ctx),
        user_id=_user_id(ctx),
        query=str(query or ""),
        limit=int(top_k or 5),
    )


memory_search_tool = StructuredTool.from_function(
    coroutine=_memory_search,
    name="memory_search",
    description="Search long-term user memories relevant to the current task.",
    args_schema=MemorySearchArgs,
    infer_schema=False,
)


class MemoryWriteArgs(BaseModel):
    content: str = Field(..., description="Memory content to store.")
    kind: str = Field(..., description="Memory category, such as preference, constraint, fact, or habit.")
    confidence: float | None = Field(default=None, description="Confidence score for the memory.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _memory_write(
    content: str,
    kind: str,
    confidence: float | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    unavailable = _runtime_unavailable(ctx)
    if unavailable:
        return {"error": unavailable}
    return await write_user_memory(
        _store(ctx),
        user_id=_user_id(ctx),
        content=str(content or ""),
        kind=str(kind or ""),
        confidence=float(confidence or 0.8),
        metadata={"source": "agent_tool"},
    )


memory_write_tool = StructuredTool.from_function(
    coroutine=_memory_write,
    name="memory_write",
    description="Write an explicit durable user preference, constraint, fact, profile item, or habit.",
    args_schema=MemoryWriteArgs,
    infer_schema=False,
)


class MemoryUpdateArgs(BaseModel):
    memory_id: str = Field(..., description="Identifier of the memory to update.")
    content: str = Field(..., description="Replacement memory content.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _memory_update(
    memory_id: str,
    content: str,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    unavailable = _runtime_unavailable(ctx)
    if unavailable:
        return {"error": unavailable}
    return await update_user_memory(
        _store(ctx),
        user_id=_user_id(ctx),
        memory_id=str(memory_id or ""),
        content=str(content or ""),
    )


memory_update_tool = StructuredTool.from_function(
    coroutine=_memory_update,
    name="memory_update",
    description="Update an existing long-term memory when the user corrects or supersedes it.",
    args_schema=MemoryUpdateArgs,
    infer_schema=False,
)


class MemoryForgetArgs(BaseModel):
    memory_id: str = Field(..., description="Identifier of the memory to delete.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _memory_forget(
    memory_id: str,
    runtime_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    ctx = runtime_context or {}
    unavailable = _runtime_unavailable(ctx)
    if unavailable:
        return {"error": unavailable}
    return await forget_user_memory(
        _store(ctx),
        user_id=_user_id(ctx),
        memory_id=str(memory_id or ""),
    )


memory_forget_tool = StructuredTool.from_function(
    coroutine=_memory_forget,
    name="memory_forget",
    description="Delete a long-term memory when the user asks to forget it.",
    args_schema=MemoryForgetArgs,
    infer_schema=False,
)


class SourceEventSearchArgs(BaseModel):
    query: str = Field(..., description="Search query for persisted source events.")
    top_k: int | None = Field(default=None, description="Maximum number of source events to return.")
    runtime_context: RuntimeContext = Field(default_factory=dict)


async def _source_event_search(
    query: str,
    top_k: int | None = None,
    runtime_context: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    ctx = runtime_context or {}
    session_id = ctx.get("session_id")
    if not isinstance(session_id, str) or not session_id:
        return [{"error": "session_id_unavailable"}]
    if ctx.get("langgraph_store") is None:
        return [{"error": "store_unavailable"}]
    return await search_source_events(
        _store(ctx),
        thread_id=session_id,
        query=str(query or ""),
        top_k=int(top_k or 8),
    )


source_event_search_tool = StructuredTool.from_function(
    coroutine=_source_event_search,
    name="source_event_search",
    description="Search original persisted conversation events when summaries are not detailed enough.",
    args_schema=SourceEventSearchArgs,
    infer_schema=False,
)
