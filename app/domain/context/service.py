from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import redis.asyncio as redis

from app.common.config import settings
from app.infra.models.context import ContextSnapshot
from app.infra.models.fridge import FridgeItem
from app.infra.models.preference import UserPreference, UserProfile
from app.infra.models.user import User


class ContextService:
    @staticmethod
    async def build(
        db: AsyncSession,
        redis_client: redis.Redis,
        user_id: str | None,
        scene: str,
        session_id: str | None = None,
        overrides: dict[str, Any] | None = None,
        force_refresh: bool = False,
    ) -> dict[str, Any]:
        cache_key = f"context:user:{user_id}:{scene}" if user_id else None
        if cache_key:
            cached = await redis_client.get(cache_key)
            if cached and not force_refresh and not overrides:
                return json.loads(cached)

        user = None
        profile = None
        preferences = None
        fridge_items: list[FridgeItem] = []
        if user_id:
            user_result = await db.execute(select(User).where(User.id == user_id))
            user = user_result.scalar_one_or_none()
            profile_result = await db.execute(
                select(UserProfile).where(UserProfile.user_id == user_id)
            )
            profile = profile_result.scalar_one_or_none()
            pref_result = await db.execute(
                select(UserPreference).where(UserPreference.user_id == user_id)
            )
            preferences = pref_result.scalar_one_or_none()
            fridge_result = await db.execute(
                select(FridgeItem).where(FridgeItem.user_id == user_id)
            )
            fridge_items = fridge_result.scalars().all()

        snapshot = {
            "user": {
                "nickname": user.nickname if user else None,
                "goal": profile.health_goal if profile else None,
                "current_state": profile.current_state if profile else None,
                "height": profile.height if profile else None,
                "weight": profile.weight if profile else None,
                "dietary_style": profile.dietary_style if profile else None,
            },
            "preferences": {
                "tastes": preferences.taste_tags if preferences else [],
                "avoid": preferences.avoid_ingredients if preferences else [],
                "allergens": preferences.allergens if preferences else [],
                "budget": preferences.budget_level if preferences else None,
                "spicy_level": preferences.spicy_level if preferences else None,
            },
            "fridge": {
                "top_items": [
                    {
                        "name": item.name,
                        "quantity": item.quantity,
                        "unit": item.unit,
                        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                    }
                    for item in fridge_items[:10]
                ],
                "expiring_soon": [
                    {
                        "name": item.name,
                        "quantity": item.quantity,
                        "unit": item.unit,
                        "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                    }
                    for item in sorted(
                        [it for it in fridge_items if it.expiry_date],
                        key=lambda it: it.expiry_date,
                    )[:5]
                ],
            },
            "environment": {
                "time_of_day": None,
                "weekday": None,
                "location": None,
                "weather": None,
            },
            "history": {
                "last_7_days_summary": None,
            },
            "constraints": {
                "hard_rules": [],
            },
            "ui_scene": scene,
        }

        if overrides:
            snapshot = _merge_overrides(snapshot, overrides)

        if cache_key:
            await redis_client.setex(
                cache_key, settings.CONTEXT_SNAPSHOT_TTL_SECONDS, json.dumps(snapshot)
            )

        if user_id:
            db.add(
                ContextSnapshot(
                    id=str(uuid4()),
                    user_id=user_id,
                    session_id=session_id,
                    snapshot_json=snapshot,
                    summary=None,
                )
            )
            await db.commit()

        return snapshot


def _merge_overrides(snapshot: dict[str, Any], overrides: dict[str, Any]) -> dict[str, Any]:
    for key, value in overrides.items():
        if isinstance(value, dict) and isinstance(snapshot.get(key), dict):
            snapshot[key].update(value)
        else:
            snapshot[key] = value
    return snapshot
