from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import db_dep, get_current_user_id
from app.common.errors import envelope
from app.infra.models.user import User

router = APIRouter()


class UpdateProfileRequest(BaseModel):
    nickname: str | None = None
    avatar_url: str | None = None


@router.get("/me")
async def me(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    trace_id = getattr(request.state, "trace_id", "")
    data = {
        "id": user.id,
        "nickname": user.nickname,
        "avatar_url": user.avatar_url,
        "email": user.email,
        "phone": user.phone,
    }
    return envelope(data, trace_id)


@router.patch("/me")
async def update_profile(
    payload: UpdateProfileRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(status_code=404, detail="user not found")

    if payload.nickname is not None:
        user.nickname = payload.nickname
    if payload.avatar_url is not None:
        user.avatar_url = payload.avatar_url

    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    data = {
        "id": user.id,
        "nickname": user.nickname,
        "avatar_url": user.avatar_url,
        "email": user.email,
        "phone": user.phone,
    }
    return envelope(data, trace_id)
