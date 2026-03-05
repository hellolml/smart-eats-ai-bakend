from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.models.preference import PreferenceEvent, UserTasteProfile

_SENSITIVE_HINTS = ("过敏", "allergy", "allergic", "忌口", "宗教")


async def get_or_create_taste_profile(db: AsyncSession, user_id: str) -> UserTasteProfile:
    result = await db.execute(select(UserTasteProfile).where(UserTasteProfile.user_id == user_id))
    profile = result.scalar_one_or_none()
    if profile is None:
        profile = UserTasteProfile(user_id=user_id, dislikes=[], allergens=[], confidence=0.0)
        db.add(profile)
        await db.flush()
    return profile


async def record_preference_event(
    db: AsyncSession,
    *,
    user_id: str,
    event_name: str,
    payload: dict[str, Any] | None = None,
) -> None:
    db.add(
        PreferenceEvent(
            id=str(uuid4()),
            user_id=user_id,
            event_name=event_name,
            payload_json=payload or {},
        )
    )
    await db.flush()


def extract_preferences_from_text(text: str | None) -> dict[str, Any]:
    source = (text or "").strip()
    if not source:
        return {}

    lowered = source.lower()
    extracted: dict[str, Any] = {}

    dislikes: list[str] = []
    for token in re.findall(r"(?:不吃|不喜欢|讨厌)([^，。！？；,!.?]{1,8})", source):
        normalized = token.strip()
        if normalized and normalized not in dislikes:
            dislikes.append(normalized)
    if dislikes:
        extracted["dislikes"] = dislikes

    allergens: list[str] = []
    for token in re.findall(r"(?:对|有)([^，。！？；,!.?]{1,10})(?:过敏)", source):
        normalized = token.strip()
        if normalized and normalized not in allergens:
            allergens.append(normalized)
    if allergens or any(h in lowered for h in _SENSITIVE_HINTS):
        extracted["allergens"] = allergens
        extracted["requires_sensitive_confirmation"] = True

    if "减脂" in source or "低卡" in source:
        extracted["diet_goal"] = "fat_loss"
    elif "增肌" in source:
        extracted["diet_goal"] = "muscle_gain"
    elif "控糖" in source:
        extracted["diet_goal"] = "sugar_control"

    if "预算" in source or "便宜" in source:
        if any(word in source for word in ("50", "省钱", "便宜", "低预算")):
            extracted["budget_range"] = "low"
        elif any(word in source for word in ("200", "高预算", "贵")):
            extracted["budget_range"] = "high"
        else:
            extracted["budget_range"] = "medium"

    if "不辣" in source:
        extracted["spice_level"] = 0
    elif "微辣" in source:
        extracted["spice_level"] = 1
    elif "中辣" in source:
        extracted["spice_level"] = 2
    elif any(word in source for word in ("重辣", "特辣", "爆辣")):
        extracted["spice_level"] = 4

    if extracted:
        extracted["confidence"] = 0.6
    return extracted


async def apply_extracted_preferences(
    db: AsyncSession,
    *,
    user_id: str,
    extracted: dict[str, Any],
    allow_sensitive: bool = False,
) -> dict[str, Any]:
    if not extracted:
        return {"applied": False, "changes": {}, "conflicts": {}}

    profile = await get_or_create_taste_profile(db, user_id)
    changes: dict[str, Any] = {}
    conflicts: dict[str, Any] = {}

    new_dislikes = extracted.get("dislikes") or []
    if new_dislikes:
        existing = list(profile.dislikes or [])
        merged = existing[:]
        for item in new_dislikes:
            if item not in merged:
                merged.append(item)
        if merged != existing:
            profile.dislikes = merged
            changes["dislikes"] = merged

    for field in ("diet_goal", "budget_range", "spice_level"):
        if field not in extracted:
            continue
        incoming = extracted.get(field)
        current = getattr(profile, field)
        if current is None or current == incoming:
            if current != incoming:
                setattr(profile, field, incoming)
                changes[field] = incoming
        else:
            conflicts[field] = {"current": current, "incoming": incoming}

    incoming_allergens = extracted.get("allergens") or []
    if incoming_allergens:
        if allow_sensitive:
            existing_allergens = list(profile.allergens or [])
            merged = existing_allergens[:]
            for item in incoming_allergens:
                if item not in merged:
                    merged.append(item)
            if merged != existing_allergens:
                profile.allergens = merged
                changes["allergens"] = merged
        else:
            conflicts["allergens"] = {
                "current": list(profile.allergens or []),
                "incoming": incoming_allergens,
                "reason": "sensitive_confirmation_required",
            }

    if extracted.get("confidence") is not None:
        profile.confidence = max(float(profile.confidence or 0.0), float(extracted["confidence"]))

    if changes:
        profile.updated_at = datetime.now(timezone.utc)

    await record_preference_event(
        db,
        user_id=user_id,
        event_name="preference_applied" if changes else "preference_extracted",
        payload={"changes": changes, "conflicts": conflicts, "source": "conversation"},
    )
    return {"applied": bool(changes), "changes": changes, "conflicts": conflicts}
