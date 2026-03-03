from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from app.api.deps import db_dep, get_optional_user_id, redis_dep
from app.common.errors import envelope
from app.domain.decision.service import DecisionService

router = APIRouter()


class BlindboxRequest(BaseModel):
    query: str | None = None
    city: str | None = None
    lat: float | None = None
    lng: float | None = None
    budget_level: int | None = Field(default=None, ge=1, le=5)
    scene: str | None = None


class QuickFilterStartRequest(BaseModel):
    query: str | None = None


class QuickFilterAnswerRequest(BaseModel):
    flow_id: str
    answer: str
    city: str | None = None
    lat: float | None = None
    lng: float | None = None
    budget_level: int | None = Field(default=None, ge=1, le=5)


@router.post("/blindbox")
async def blindbox_decision(
    payload: BlindboxRequest,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    data = await DecisionService.blindbox(
        db,
        redis,
        user_id=user_id,
        query=payload.query,
        city=payload.city,
        lat=payload.lat,
        lng=payload.lng,
        budget_level=payload.budget_level,
        scene=payload.scene,
    )
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.post("/quick-filter/start")
async def quick_filter_start(
    payload: QuickFilterStartRequest,
    request: Request,
    redis: redis_dep,
):
    data = await DecisionService.quick_filter_start(redis, query=payload.query)
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)


@router.post("/quick-filter/answer")
async def quick_filter_answer(
    payload: QuickFilterAnswerRequest,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str | None = Depends(get_optional_user_id),
):
    data = await DecisionService.quick_filter_answer(
        redis,
        db,
        flow_id=payload.flow_id,
        user_id=user_id,
        answer=payload.answer,
        city=payload.city,
        lat=payload.lat,
        lng=payload.lng,
        budget_level=payload.budget_level,
    )
    if data is None:
        raise HTTPException(status_code=404, detail="quick filter flow not found")
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(data, trace_id)
