from __future__ import annotations

from typing import Any, AsyncGenerator
import logging

from fastapi import APIRouter, Depends, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.graph import run_chat_stream
from app.api.deps import db_dep, get_optional_user_id, minio_dep, redis_dep
from app.common.config import settings
from app.common.errors import envelope
from app.common.sse import sse_event
from app.domain.app.service import AppBffService

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
    scene: str | None = None
    attachments: list[dict[str, Any]] | None = None
    travel_action: str | None = None
    travel_payload: dict[str, Any] | None = None
    client_context_overrides: dict[str, Any] | None = None
    provider: str | None = None
    model: str | None = None
    resume_from_checkpoint: bool | None = None
    checkpoint_ref: str | None = None
    replay_from_checkpoint: bool | None = None
    resume_payload: dict[str, Any] | None = None


class SessionCreateRequest(BaseModel):
    title: str | None = None
    scene: str | None = None


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


# Chat session endpoints
@router.post("/sessions")
async def create_chat_session(
    request: Request,
    db: db_dep,
    user_id: str | None = Depends(get_optional_user_id),
    payload: SessionCreateRequest | None = None,
):
    user_id = _resolve_user_id(user_id)
    data = await AppBffService.create_chat_session(
        user_id,
        db,
        scene=payload.scene if payload else None,
        title=payload.title if payload else None,
    )
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.get("/sessions")
async def list_chat_sessions(
    request: Request,
    db: db_dep,
    user_id: str | None = Depends(get_optional_user_id),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
    q: str | None = Query(None),
):
    user_id = _resolve_user_id(user_id)
    data = await AppBffService.list_chat_sessions(user_id, db, limit, offset, q)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.get("/sessions/{session_id}/messages")
async def list_chat_messages(
    session_id: str,
    request: Request,
    db: db_dep,
    user_id: str | None = Depends(get_optional_user_id),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    user_id = _resolve_user_id(user_id)
    trace_id = getattr(request.state, "trace_id", "")
    try:
        data = await AppBffService.list_chat_messages(user_id, session_id, db, limit, offset)
    except HTTPException as exc:
        if exc.status_code == 403:
            return envelope({"messages": [], "offset": offset, "limit": limit}, trace_id)
        if exc.status_code == 404:
            return envelope({"messages": [], "offset": offset, "limit": limit}, trace_id)
        raise
    return envelope(data, trace_id)


@router.patch("/sessions/{session_id}")
async def rename_chat_session(
    session_id: str,
    request: Request,
    payload: SessionUpdateRequest,
    db: db_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    if payload.title is None:
        trace_id = getattr(request.state, "trace_id", "")
        return envelope({"updated": False}, trace_id)
    user_id = _resolve_user_id(user_id)
    trace_id = getattr(request.state, "trace_id", "")
    try:
        data = await AppBffService.rename_chat_session(user_id, session_id, payload.title, db)
    except HTTPException as exc:
        if exc.status_code == 404:
            return envelope({"updated": False}, trace_id, code=40401, message="not found")
        if exc.status_code == 403:
            return envelope({"updated": False}, trace_id, code=40301, message="forbidden")
        raise
    return envelope(data, trace_id)


@router.delete("/sessions/{session_id}")
async def delete_chat_session(
    session_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    user_id = _resolve_user_id(user_id)
    trace_id = getattr(request.state, "trace_id", "")
    try:
        data = await AppBffService.delete_chat_session(user_id, session_id, db, redis)
    except HTTPException as exc:
        if exc.status_code == 404:
            return envelope({"deleted": False}, trace_id, code=40401, message="not found")
        if exc.status_code == 403:
            return envelope({"deleted": False}, trace_id, code=40301, message="forbidden")
        raise
    return envelope(data, trace_id)


@router.post("/sessions/{session_id}/stop")
async def stop_chat(
    session_id: str,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    user_id = _resolve_user_id(user_id)
    data = await AppBffService.stop_chat_session(user_id, session_id, db, redis)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.post("/sessions/{session_id}/attachments")
async def upload_chat_attachment(
    session_id: str,
    request: Request,
    db: db_dep,
    minio: minio_dep,
    user_id: str | None = Depends(get_optional_user_id),
    file: UploadFile = File(...),
):
    user_id = _resolve_user_id(user_id)
    await AppBffService.ensure_chat_session_access(user_id, session_id, db, allow_missing=False)
    content = await file.read()
    data = await AppBffService.create_chat_attachment(
        user_id=user_id or "anonymous",
        session_id=session_id,
        filename=file.filename,
        content_type=file.content_type,
        content=content,
        minio=minio,
    )
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.post("/sessions/{session_id}/stream")
async def chat_stream(
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

    raw = payload.model_dump(exclude_unset=True)
    state = await AppBffService.prepare_chat_stream_state(
        session_id=session_id,
        user_id=user_id,
        payload=raw,
        db=db,
        redis_client=redis,
        forwarded_for=request.headers.get("x-forwarded-for"),
        real_ip=request.headers.get("x-real-ip"),
        request_client_host=request.client.host if request.client else None,
        trace_id=getattr(request.state, "trace_id", None),
        rate_limit_key_prefix="chat",
    )

    async def event_stream() -> AsyncGenerator[str, None]:
        async for item in run_chat_stream(request, db, redis, state):
            yield sse_event(item["event"], item["data"])

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Content-Encoding": "identity",
        },
    )
