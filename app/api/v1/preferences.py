from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import db_dep, get_current_user_id, redis_dep
from app.common.errors import envelope
from app.domain.preferences.service import get_or_create_taste_profile, record_preference_event
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


class TasteProfileResponse(BaseModel):
    user_id: str
    dislikes: list[str] = Field(default_factory=list)
    allergens: list[str] = Field(default_factory=list)
    diet_goal: str | None = None
    budget_range: str | None = None
    spice_level: int | None = Field(default=None, ge=0, le=5)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)
    updated_at: str | None = None


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


class TasteProfilePatchRequest(BaseModel):
    dislikes: list[str] | None = None
    allergens: list[str] | None = None
    diet_goal: Literal["fat_loss", "muscle_gain", "sugar_control", "balanced"] | None = None
    budget_range: Literal["low", "medium", "high"] | None = None
    spice_level: int | None = Field(default=None, ge=0, le=5)
    confidence: float | None = Field(default=None, ge=0.0, le=1.0)
    confirm_sensitive: bool = False


async def _invalidate_context_cache(redis_client: Any, user_id: str) -> None:
    pattern = f"context:user:{user_id}:*"
    keys = []
    async for key in redis_client.scan_iter(match=pattern):
        keys.append(key)
    if keys:
        await redis_client.delete(*keys)


def _to_taste_profile_response(profile) -> dict[str, Any]:
    return TasteProfileResponse(
        user_id=profile.user_id,
        dislikes=profile.dislikes or [],
        allergens=profile.allergens or [],
        diet_goal=profile.diet_goal,
        budget_range=profile.budget_range,
        spice_level=profile.spice_level,
        confidence=float(profile.confidence or 0.0),
        updated_at=profile.updated_at.isoformat() if profile.updated_at else None,
    ).model_dump()


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


@router.get("/preferences/taste-profile")
async def get_taste_profile(
    request: Request,
    db: db_dep,
    user_id: str = Depends(get_current_user_id),
):
    profile = await get_or_create_taste_profile(db, user_id)
    await db.commit()
    trace_id = getattr(request.state, "trace_id", "")
    return envelope(_to_taste_profile_response(profile), trace_id)


@router.patch("/preferences/taste-profile")
async def patch_taste_profile(
    payload: TasteProfilePatchRequest,
    request: Request,
    db: db_dep,
    redis: redis_dep,
    user_id: str = Depends(get_current_user_id),
):
    profile = await get_or_create_taste_profile(db, user_id)
    updates = payload.model_dump(exclude_unset=True)

    if "allergens" in updates and updates["allergens"] is not None and not payload.confirm_sensitive:
        raise HTTPException(status_code=409, detail="allergens update requires confirm_sensitive=true")

    updates.pop("confirm_sensitive", None)
    for field, value in updates.items():
        setattr(profile, field, value)

    await record_preference_event(
        db,
        user_id=user_id,
        event_name="preference_applied",
        payload={"source": "api_patch", "fields": sorted(list(updates.keys()))},
    )
    await db.commit()
    await db.refresh(profile)
    await _invalidate_context_cache(redis, user_id)

    trace_id = getattr(request.state, "trace_id", "")
    return envelope(_to_taste_profile_response(profile), trace_id)
