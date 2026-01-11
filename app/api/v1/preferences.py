from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy import select

from app.api.deps import db_dep, get_current_user_id, redis_dep
from app.common.errors import envelope
from app.infra.models.preference import UserPreference, UserProfile

router = APIRouter()


class ProfileResponse(BaseModel):
    user_id: str
    health_goal: str | None = None
    current_state: str | None = None
    height: float | None = None
    weight: float | None = None
    dietary_style: str | None = None


class PreferenceResponse(BaseModel):
    user_id: str
    taste_tags: list[str]
    avoid_ingredients: list[str]
    allergens: list[str]
    spicy_level: int | None = None
    budget_level: int | None = None


class UpdateProfileRequest(BaseModel):
    health_goal: str | None = None
    current_state: str | None = None
    height: float | None = None
    weight: float | None = None
    dietary_style: str | None = None


class UpdatePreferenceRequest(BaseModel):
    taste_tags: list[str] | None = None
    avoid_ingredients: list[str] | None = None
    allergens: list[str] | None = None
    spicy_level: int | None = None
    budget_level: int | None = None


async def _invalidate_context_cache(redis_client: Any, user_id: str) -> None:
    pattern = f"context:user:{user_id}:*"
    keys = []
    async for key in redis_client.scan_iter(match=pattern):
        keys.append(key)
    if keys:
        await redis_client.delete(*keys)


@router.get("/users/me/profile")
async def get_profile(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)
        await db.commit()
        await db.refresh(profile)

    trace_id = getattr(request.state, "trace_id", "")
    data = ProfileResponse(
        user_id=profile.user_id,
        health_goal=profile.health_goal,
        current_state=profile.current_state,
        height=profile.height,
        weight=profile.weight,
        dietary_style=profile.dietary_style,
    ).model_dump()
    return envelope(data, trace_id)


@router.patch("/users/me/profile")
async def update_profile(
    payload: UpdateProfileRequest,
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(select(UserProfile).where(UserProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = UserProfile(user_id=user_id)
        db.add(profile)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(profile, field, value)

    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    data = ProfileResponse(
        user_id=profile.user_id,
        health_goal=profile.health_goal,
        current_state=profile.current_state,
        height=profile.height,
        weight=profile.weight,
        dietary_style=profile.dietary_style,
    ).model_dump()
    return envelope(data, trace_id)


@router.get("/users/me/preferences")
async def get_preferences(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    pref = result.scalar_one_or_none()
    if pref is None:
        pref = UserPreference(
            user_id=user_id,
            taste_tags=[],
            avoid_ingredients=[],
            allergens=[],
        )
        db.add(pref)
        await db.commit()
        await db.refresh(pref)

    trace_id = getattr(request.state, "trace_id", "")
    data = PreferenceResponse(
        user_id=pref.user_id,
        taste_tags=pref.taste_tags or [],
        avoid_ingredients=pref.avoid_ingredients or [],
        allergens=pref.allergens or [],
        spicy_level=pref.spicy_level,
        budget_level=pref.budget_level,
    ).model_dump()
    return envelope(data, trace_id)


@router.patch("/users/me/preferences")
async def update_preferences(
    payload: UpdatePreferenceRequest,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    result = await db.execute(
        select(UserPreference).where(UserPreference.user_id == user_id)
    )
    pref = result.scalar_one_or_none()
    if pref is None:
        pref = UserPreference(
            user_id=user_id,
            taste_tags=[],
            avoid_ingredients=[],
            allergens=[],
        )
        db.add(pref)

    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(pref, field, value)

    await db.commit()
    await _invalidate_context_cache(redis, user_id)

    trace_id = getattr(request.state, "trace_id", "")
    data = PreferenceResponse(
        user_id=pref.user_id,
        taste_tags=pref.taste_tags or [],
        avoid_ingredients=pref.avoid_ingredients or [],
        allergens=pref.allergens or [],
        spicy_level=pref.spicy_level,
        budget_level=pref.budget_level,
    ).model_dump()
    return envelope(data, trace_id)
