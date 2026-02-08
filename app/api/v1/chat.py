from __future__ import annotations

from typing import Any, AsyncGenerator
from uuid import uuid4
import logging

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.graph import run_chat_stream
from app.agent.state import ChatState
from app.agent import history
from app.api.deps import db_dep, get_optional_user_id, redis_dep
from app.common.config import settings
from app.common.errors import envelope
from app.common.rate_limit import ensure_rate_limit
from app.common.sse import sse_event
from app.infra.models.chat import ChatMessage, ChatSession
from sqlalchemy import desc, select

router = APIRouter()
logger = logging.getLogger("chat.api")


def _quick_intent(message: str | None) -> str:
    text = (message or "").strip().lower()
    if not text:
        return "unknown"
    if any(token in text for token in ("出去吃", "外出", "餐厅", "吃饭")):
        return "eat_out"
    if any(token in text for token in ("做饭", "在家做", "菜谱", "食谱", "冰箱")):
        return "cook_home"
    if any(token in text for token in ("路线", "导航", "怎么走")):
        return "route"
    return "chat"


class ChatStreamRequest(BaseModel):
    message: str | None = None
    client_context_overrides: dict[str, Any] | None = None
    provider: str | None = None
    agent_type: str | None = None


class SessionUpdateRequest(BaseModel):
    title: str | None = None


def _resolve_user_id(user_id: str | None) -> str | None:
    if user_id:
        return user_id
    if settings.ENV == "production":
        return None
    return settings.SEED_DEMO_USER_ID


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
    user_id = _resolve_user_id(user_id)
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
    user_id = _resolve_user_id(user_id)
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
    user_id: str | None = Depends(get_optional_user_id),
):
    user_id = _resolve_user_id(user_id)
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        trace_id = getattr(request.state, "trace_id", "")
        return envelope({"updated": False}, trace_id, code=40401, message="not found")
    if user_id and session.user_id != user_id:
        trace_id = getattr(request.state, "trace_id", "")
        return envelope({"updated": False}, trace_id, code=40301, message="forbidden")
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
    redis: redis_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    user_id = _resolve_user_id(user_id)
    stmt = select(ChatSession).where(ChatSession.id == session_id)
    result = await db.execute(stmt)
    session = result.scalar_one_or_none()
    if not session:
        trace_id = getattr(request.state, "trace_id", "")
        return envelope({"deleted": False}, trace_id, code=40401, message="not found")
    if user_id and session.user_id != user_id:
        trace_id = getattr(request.state, "trace_id", "")
        return envelope({"deleted": False}, trace_id, code=40301, message="forbidden")
    session.deleted_at = datetime.utcnow()
    await db.commit()
    await history.clear_session_cache(redis, session_id)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope({"deleted": True}, trace_id)


@router.get("/sessions/{session_id}/messages")
async def list_messages(
    session_id: str,
    request: Request,
    db: db_dep,
    user_id: str | None = Depends(get_optional_user_id),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user_id = _resolve_user_id(user_id)
    stmt = (
        select(ChatMessage)
        .where(ChatMessage.session_id == session_id)
        .order_by(ChatMessage.created_at)
        .offset(offset)
        .limit(limit)
    )
    if user_id:
        session_stmt = select(ChatSession).where(ChatSession.id == session_id)
        session_result = await db.execute(session_stmt)
        session = session_result.scalar_one_or_none()
        if session and session.user_id != user_id:
            trace_id = getattr(request.state, "trace_id", "")
            return envelope({"messages": [], "offset": offset, "limit": limit}, trace_id)
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
    user_id = _resolve_user_id(user_id)
    overrides = payload.client_context_overrides if isinstance(payload.client_context_overrides, dict) else {}
    env = overrides.get("environment") if isinstance(overrides.get("environment"), dict) else {}
    location = env.get("location") if isinstance(env.get("location"), dict) else None
    has_device_location = bool(location and location.get("lat") is not None and location.get("lng") is not None)
    intent = _quick_intent(payload.message)
    logger.info(
        "chat_stream_location session_id=%s intent=%s has_device_location=%s location=%s",
        session_id,
        intent,
        has_device_location,
        location if has_device_location else None,
    )
    forwarded = request.headers.get("x-forwarded-for") or request.headers.get("x-real-ip")
    if forwarded:
        client_ip = forwarded.split(",")[0].strip()
    else:
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
        client_ip=client_ip,
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
