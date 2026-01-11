from __future__ import annotations

from typing import Any, AsyncGenerator
from uuid import uuid4

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.checkpoint import checkpointer_context
from app.agent.factory import build_agent_graph
from app.agent.graph import run_chat_stream
from app.agent.agent_registry import get_agent_config
from app.agent.state import ChatState
from app.api.deps import db_dep, get_optional_user_id, redis_dep
from app.common.config import settings
from app.common.errors import envelope
from app.common.rate_limit import ensure_rate_limit
from app.common.sse import sse_event
from app.infra.models.chat import ChatMessage, ChatSession
from sqlalchemy import desc, select

router = APIRouter()


class ChatStreamRequest(BaseModel):
    message: str | None = None
    client_context_overrides: dict[str, Any] | None = None
    provider: str | None = None
    agent_type: str | None = None
    resume: bool = False
    checkpoint_id: str | None = None
    replay: bool = False
    resume_payload: dict[str, Any] | None = None


class SessionUpdateRequest(BaseModel):
    title: str | None = None


class UpdateStateRequest(BaseModel):
    values: dict[str, Any]
    as_node: str | None = None
    checkpoint_id: str | None = None


@router.get("/providers")
async def list_providers(request: Request):
    raw = settings.LLM_PROVIDERS or ""
    providers = [item.strip() for item in raw.split(",") if item.strip()]
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"providers": providers}, trace_id)


@router.post("/sessions")
async def create_session(
    request: Request,
    db: db_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    session_id = str(uuid4())
    session = ChatSession(
        id=session_id,
        user_id=user_id,
        scene="chat",
        title="新会话",
    )
    db.add(session)
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"session_id": session_id, "title": session.title}, trace_id)


@router.get("/sessions")
async def list_sessions(
    request: Request,
    db: db_dep,
    user_id: str | None = Depends(get_optional_user_id),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None),
):
    tz = timezone(timedelta(hours=8))
    stmt = (
        select(ChatSession)
        .where(ChatSession.deleted_at.is_(None))
        .order_by(desc(ChatSession.created_at))
        .offset(offset)
        .limit(limit)
    )
    if user_id:
        stmt = stmt.where(ChatSession.user_id == user_id)
    if q:
        stmt = stmt.where(ChatSession.title.contains(q))
    result = await db.execute(stmt)
    rows = result.scalars().all()
    updated = False
    for row in rows:
        if not row.title or row.title == "新会话":
            msg_stmt = (
                select(ChatMessage)
                .where(ChatMessage.session_id == row.id, ChatMessage.role == "user")
                .order_by(ChatMessage.created_at)
                .limit(1)
            )
            msg_result = await db.execute(msg_stmt)
            msg = msg_result.scalar_one_or_none()
            if msg and msg.content:
                title = msg.content.strip().replace("\n", " ")
                row.title = title[:24] if len(title) > 24 else title
                updated = True
    if updated:
        await db.commit()
    data = []
    for row in rows:
        created_at = row.created_at
        if created_at and created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        data.append(
            {
                "session_id": row.id,
                "scene": row.scene,
                "title": row.title,
                "created_at": created_at.astimezone(tz).isoformat() if created_at else None,
            }
        )
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"sessions": data, "offset": offset, "limit": limit}, trace_id)


@router.patch("/sessions/{session_id}")
async def rename_session(
    session_id: str,
    request: Request,
    payload: SessionUpdateRequest,
    db: db_dep,
):
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        trace_id = getattr(request.state, "trace_id", "")
        return envelope({"updated": False}, trace_id, code=40401, message="not found")
    if payload.title is not None:
        session.title = payload.title
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"updated": True, "title": session.title}, trace_id)


@router.delete("/sessions/{session_id}")
async def delete_session(
    session_id: str,
    request: Request,
    db: db_dep,
):
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        trace_id = getattr(request.state, "trace_id", "")
        return envelope({"deleted": False}, trace_id, code=40401, message="not found")
    session.deleted_at = datetime.utcnow()
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"deleted": True}, trace_id)


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    db: db_dep,
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .offset(offset)
        .limit(limit)
    )
    result = await db.execute(stmt)
    rows = result.scalars().all()
    data = [
        {
            "id": row.id,
            "role": row.role,
            "content": row.content,
            "tool_name": row.tool_name,
            "tool_payload": row.tool_payload_json,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }
        for row in rows
    ]
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"messages": data, "offset": offset, "limit": limit}, trace_id)


@router.post("/sessions/{session_id}/stop")
async def stop_session(session_id: str, request: Request, redis: redis_dep):
    key = f"chat:cancel:{session_id}"
    await redis.setex(key, settings.CHAT_CANCEL_TTL, "1")
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"stopped": True}, trace_id)


@router.post("/sessions/{session_id}/pause")
async def pause_session(session_id: str, request: Request, redis: redis_dep):
    key = f"chat:pause:{session_id}"
    await redis.setex(key, settings.CHAT_PAUSE_TTL, "1")
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"paused": True}, trace_id)


@router.get("/sessions/{session_id}/checkpoints")
async def list_checkpoints(
    session_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    provider: str | None = Query(None),
    agent_type: str | None = Query(None),
):
    async with checkpointer_context() as checkpointer:
        if not checkpointer:
            trace_id = getattr(request.state, "trace_id", "")
            return envelope({"checkpoints": []}, trace_id)
        agent_config = get_agent_config(agent_type)
        graph = build_agent_graph(
            db=db,
            redis_client=redis,
            agent_config=agent_config,
            provider=provider,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": session_id}}
        if hasattr(graph, "aget_state_history"):
            history = [item async for item in graph.aget_state_history(config)]
        else:
            history = list(graph.get_state_history(config))
    data = []
    for item in history:
        created_at = getattr(item, "created_at", None)
        if created_at is not None and not hasattr(created_at, "isoformat"):
            created_at = str(created_at)
        created_at_value = created_at.isoformat() if hasattr(created_at, "isoformat") else created_at
        checkpoint_id = item.config.get("configurable", {}).get("checkpoint_id")
        data.append(
            {
                "checkpoint_id": checkpoint_id,
                "created_at": created_at_value,
                "next": list(item.next) if getattr(item, "next", None) is not None else [],
                "metadata": item.metadata,
            }
        )
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"checkpoints": data}, trace_id)


@router.get("/sessions/{session_id}/state")
async def get_checkpoint_state(
    session_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    provider: str | None = Query(None),
    agent_type: str | None = Query(None),
    checkpoint_id: str | None = Query(None),
):
    async with checkpointer_context() as checkpointer:
        if not checkpointer:
            trace_id = getattr(request.state, "trace_id", "")
            return envelope({"state": None}, trace_id)
        agent_config = get_agent_config(agent_type)
        graph = build_agent_graph(
            db=db,
            redis_client=redis,
            agent_config=agent_config,
            provider=provider,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": session_id}}
        if checkpoint_id:
            config["configurable"]["checkpoint_id"] = checkpoint_id
        if hasattr(graph, "aget_state"):
            snapshot = await graph.aget_state(config)
        else:
            snapshot = graph.get_state(config)
    payload = {
        "values": snapshot.values if snapshot else None,
        "next": list(snapshot.next) if snapshot and snapshot.next else [],
        "checkpoint_id": snapshot.config.get("configurable", {}).get("checkpoint_id")
        if snapshot
        else None,
        "metadata": snapshot.metadata if snapshot else None,
    }
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"state": payload}, trace_id)


@router.post("/sessions/{session_id}/state")
async def update_checkpoint_state(
    session_id: str,
    request: Request,
    payload: UpdateStateRequest,
    db: db_dep,
    redis: redis_dep,
    provider: str | None = Query(None),
    agent_type: str | None = Query(None),
):
    async with checkpointer_context() as checkpointer:
        if not checkpointer:
            trace_id = getattr(request.state, "trace_id", "")
            return envelope({"updated": False}, trace_id)
        agent_config = get_agent_config(agent_type)
        graph = build_agent_graph(
            db=db,
            redis_client=redis,
            agent_config=agent_config,
            provider=provider,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": session_id}}
        if payload.checkpoint_id:
            config["configurable"]["checkpoint_id"] = payload.checkpoint_id
        result = graph.update_state(config, payload.values, as_node=payload.as_node)
        if hasattr(result, "__await__"):
            await result
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"updated": True}, trace_id)


@router.post("/sessions/{session_id}/stream")
async def stream_session(
    session_id: str,
    request: Request,
    payload: ChatStreamRequest | None,
    db: db_dep,
    redis: redis_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    payload = payload or ChatStreamRequest()
    client_ip = request.client.host if request.client else "unknown"
    await ensure_rate_limit(
        redis,
        key=f"rl:chat:{client_ip}",
        limit=30,
        window_seconds=60,
    )

    state = ChatState(
        session_id=session_id,
        user_id=user_id,
        message=payload.message,
        trace_id=getattr(request.state, "trace_id", None),
        context_overrides=payload.client_context_overrides,
        provider=payload.provider,
        agent_type=payload.agent_type,
        resume_from_checkpoint=payload.resume,
        checkpoint_ref=payload.checkpoint_id,
        replay_from_checkpoint=payload.replay,
        resume_payload=payload.resume_payload,
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        async for item in run_chat_stream(request, db, redis, state):
            yield sse_event(item["event"], item["data"])

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
