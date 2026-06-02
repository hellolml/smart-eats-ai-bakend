from __future__ import annotations

from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.infra.models.context import ContextSnapshot
from app.infra.models.fridge import FridgeItem
from app.infra.models.preference import UserPreference, UserProfile
from app.infra.models.user import User


class ContextService:
    @staticmethod
    async def build_debug_snapshot(
        db: AsyncSession,
        *,
        thread_id: str,
    ) -> dict[str, Any]:
        from app.agent.langgraph_context import search_source_events, store_search
        from app.agent.langgraph_store import langgraph_store_context

        async with langgraph_store_context() as store:
            source_events = await search_source_events(
                store,
                thread_id=thread_id,
                query="",
                top_k=20,
            )
            compaction_runs = await store_search(
                store,
                ("compaction_runs", thread_id),
                query=None,
                limit=20,
            )
        return {
            "thread_id": thread_id,
            "runtime": "langgraph_native",
            "source_event_count": len(source_events),
            "source_events": [
                {
                    "id": item.get("event_id"),
                    "tool_name": item.get("tool_name"),
                    "content_preview": item.get("content_preview"),
                    "metadata": item.get("metadata"),
                }
                for item in source_events
            ],
            "compaction_runs": [
                {
                    "id": getattr(item, "key", None),
                    "status": (getattr(item, "value", {}) or {}).get("status"),
                    "error_type": (getattr(item, "value", {}) or {}).get("error_type"),
                    "budget": (getattr(item, "value", {}) or {}).get("budget"),
                }
                for item in compaction_runs
            ],
        }

    @staticmethod
    async def build(
        db: AsyncSession,
        user_id: str | None,
        scene: str,
        session_id: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
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

        if session_id:
            snapshot["langgraph_context"] = await ContextService.build_debug_snapshot(
                db,
                thread_id=session_id,
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
