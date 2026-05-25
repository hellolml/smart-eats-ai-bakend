from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select

from app.context_engine.types import ContextBlock, ContextRequest
from app.infra.models.fridge import FridgeItem
from app.infra.models.preference import UserPreference, UserProfile
from app.infra.models.user import User


class SmartEatsBusinessProvider:
    name = "smart_eats_business"

    def __init__(self, db: Any) -> None:
        self.db = db

    async def collect(self, request: ContextRequest) -> list[ContextBlock]:
        if not request.user_id:
            return []

        user = (await self.db.execute(select(User).where(User.id == request.user_id))).scalar_one_or_none()
        profile = (
            await self.db.execute(select(UserProfile).where(UserProfile.user_id == request.user_id))
        ).scalar_one_or_none()
        preferences = (
            await self.db.execute(select(UserPreference).where(UserPreference.user_id == request.user_id))
        ).scalar_one_or_none()
        fridge_items = (
            await self.db.execute(select(FridgeItem).where(FridgeItem.user_id == request.user_id).limit(10))
        ).scalars().all()

        payload = {
            "user": {
                "nickname": user.nickname if user else None,
                "goal": profile.health_goal if profile else None,
                "current_state": profile.current_state if profile else None,
                "dietary_style": profile.dietary_style if profile else None,
            },
            "preferences": {
                "tastes": preferences.taste_tags if preferences else [],
                "avoid": preferences.avoid_ingredients if preferences else [],
                "allergens": preferences.allergens if preferences else [],
                "budget": preferences.budget_level if preferences else None,
                "spicy_level": preferences.spicy_level if preferences else None,
            },
            "fridge": [
                {
                    "name": item.name,
                    "quantity": item.quantity,
                    "unit": item.unit,
                    "expiry_date": item.expiry_date.isoformat() if item.expiry_date else None,
                }
                for item in fridge_items
            ],
        }
        return [
            ContextBlock(
                kind="business_facts",
                source=self.name,
                content=json.dumps(payload, ensure_ascii=False),
                priority=85,
                metadata={"scene": request.scene},
            )
        ]
