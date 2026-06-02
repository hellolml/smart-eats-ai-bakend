from __future__ import annotations

from datetime import datetime
from typing import AsyncGenerator, Any
from uuid import uuid4

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.agent.graph import run_chat_stream
from app.agent.state import ChatState
from app.api.deps import db_dep, get_current_user_id, redis_dep
from app.common.config import settings
from app.common.errors import envelope
from app.common.sse import sse_event
from app.domain.context.service import ContextService

router = APIRouter()


class TodayDecisionRequest(BaseModel):
    location: dict[str, Any] | None = None
    mood: str | None = None
    budget: str | None = None
    provider: str | None = None


@router.get("/overview")
async def overview(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    snapshot = await ContextService.build(
        db=db,
        user_id=user_id,
        scene="today_decision",
    )
    now = datetime.now()
    data = {
        "nickname": snapshot.get("user", {}).get("nickname"),
        "goal": snapshot.get("user", {}).get("goal"),
        "current_state": snapshot.get("user", {}).get("current_state"),
        "time_of_day": now.strftime("%H:%M"),
        "weekday": now.strftime("%A"),
        "weather": snapshot.get("environment", {}).get("weather"),
        "summary": "ready",  # placeholder for MVP
    }
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.post("/decision/stream")
async def decision_stream(
    request: Request,
    payload: TodayDecisionRequest,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    payload = payload or TodayDecisionRequest()
    if not user_id and settings.ENV != "production":
        user_id = settings.SEED_DEMO_USER_ID
    overrides = {
        "environment": {"location": payload.location} if payload.location else {},
        "constraints": {"budget": payload.budget} if payload.budget else {},
        "user": {"current_state": payload.mood} if payload.mood else {},
    }
    session_id = str(uuid4())
    state = ChatState(
        session_id=session_id,
        user_id=user_id,
        message="today decision",
        trace_id=getattr(request.state, "trace_id", None),
        context_overrides=overrides,
        provider=payload.provider,
        scene="today_decision",
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
