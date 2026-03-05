import pytest
from sqlalchemy import select

from app.domain.preferences.service import apply_extracted_preferences, extract_preferences_from_text, get_or_create_taste_profile
from app.infra.db import AsyncSessionLocal
from app.infra.models.preference import PreferenceEvent, UserTasteProfile


@pytest.mark.asyncio
async def test_taste_profile_get_and_patch(client):
    resp = await client.post(
        "/api/v1/auth/register",
        json={"email": "taste_profile@example.com", "password": "secret123", "name": "Taste"},
    )
    assert resp.status_code == 200
    token = resp.json()["data"]["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    resp = await client.get("/api/v1/preferences/taste-profile", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["dislikes"] == []
    assert data["allergens"] == []
    assert data["confidence"] == 0.0

    resp = await client.patch(
        "/api/v1/preferences/taste-profile",
        json={"allergens": ["花生"]},
        headers=headers,
    )
    assert resp.status_code == 409

    resp = await client.patch(
        "/api/v1/preferences/taste-profile",
        json={
            "dislikes": ["香菜"],
            "allergens": ["花生"],
            "diet_goal": "fat_loss",
            "budget_range": "low",
            "spice_level": 1,
            "confidence": 0.8,
            "confirm_sensitive": True,
        },
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["dislikes"] == ["香菜"]
    assert data["allergens"] == ["花生"]
    assert data["diet_goal"] == "fat_loss"
    assert data["budget_range"] == "low"
    assert data["spice_level"] == 1
    assert data["confidence"] == 0.8


@pytest.mark.asyncio
async def test_preference_extraction_conflict_behavior():
    user_id = "u_conflict"
    async with AsyncSessionLocal() as db:
        profile = await get_or_create_taste_profile(db, user_id)
        profile.spice_level = 0
        profile.dislikes = ["香菜"]
        await db.commit()

    extracted = extract_preferences_from_text("我中辣就行，不吃芹菜，我对花生过敏")
    assert extracted["spice_level"] == 2
    assert extracted["dislikes"] == ["芹菜"]
    assert extracted["allergens"] == ["花生"]

    async with AsyncSessionLocal() as db:
        result = await apply_extracted_preferences(
            db,
            user_id=user_id,
            extracted=extracted,
            allow_sensitive=False,
        )
        await db.commit()
        profile = (await db.execute(select(UserTasteProfile).where(UserTasteProfile.user_id == user_id))).scalar_one()
        events = (
            await db.execute(
                select(PreferenceEvent).where(PreferenceEvent.user_id == user_id).order_by(PreferenceEvent.created_at.desc())
            )
        ).scalars().all()

    assert result["applied"] is True
    assert "spice_level" in result["conflicts"]
    assert "allergens" in result["conflicts"]
    assert profile.spice_level == 0
    assert profile.dislikes == ["香菜", "芹菜"]
    assert profile.allergens == []
    assert any(e.event_name in {"preference_applied", "preference_extracted"} for e in events)
