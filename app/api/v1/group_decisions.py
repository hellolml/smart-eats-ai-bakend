from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from app.api.deps import db_dep, get_current_user_id
from app.common.errors import envelope
from app.domain.group_decision.service import GroupDecisionService

router = APIRouter()


class GroupDecisionOption(BaseModel):
    title: str
    item_type: str = "restaurant"
    meta: dict[str, Any] = Field(default_factory=dict)


class GroupDecisionCreateRequest(BaseModel):
    title: str = "今晚吃什么"
    city: str | None = None
    options: list[GroupDecisionOption] = Field(default_factory=list, min_length=2, max_length=12)
    expires_hours: int = Field(default=24, ge=1, le=168)


class GroupDecisionVoteRequest(BaseModel):
    voter_name: str = Field(min_length=1, max_length=64)
    voter_key: str = Field(min_length=1, max_length=64)
    note: str | None = Field(default=None, max_length=300)


@router.post("/group-decisions")
async def create_group_decision(
    payload: GroupDecisionCreateRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await GroupDecisionService.create_session(
        db,
        creator_user_id=user_id,
        title=payload.title,
        options=[item.model_dump() for item in payload.options],
        city=payload.city,
        base_url=str(request.base_url),
        expires_hours=payload.expires_hours,
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/group-decisions/{session_id}/vote")
async def vote_group_decision(
    session_id: str,
    item_id: str,
    payload: GroupDecisionVoteRequest,
    request: Request,
    db: db_dep,
):
    data = await GroupDecisionService.submit_vote(
        db,
        session_id=session_id,
        item_id=item_id,
        voter_name=payload.voter_name,
        voter_key=payload.voter_key,
        note=payload.note,
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.post("/group-decisions/{session_id}/close")
async def close_group_decision(
    session_id: str,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    data = await GroupDecisionService.close_session(
        db,
        session_id=session_id,
        actor_user_id=user_id,
        base_url=str(request.base_url),
    )
    return envelope(data, getattr(request.state, "trace_id", ""))


@router.get("/group-decisions/{session_id}/result")
async def get_group_decision_result(
    session_id: str,
    request: Request,
    db: db_dep,
):
    data = await GroupDecisionService.get_result(
        db,
        session_id=session_id,
        base_url=str(request.base_url),
    )
    return envelope(data, getattr(request.state, "trace_id", ""))
