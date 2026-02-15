import pytest


@pytest.mark.asyncio
async def test_app_profile_and_preferences(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_profile@example.com", "password": "secret123", "name": "Old"},
    )
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.patch(
        "/api/v1/app/me",
        json={"name": "NewName", "avatar": "https://example.com/a.png", "health_goal": "减脂", "current_state": "有点累"},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.get("/api/v1/app/me", headers=headers)
    assert resp.status_code == 200
    me = resp.json()["data"]
    assert me["name"] == "NewName"
    assert me["avatar"] == "https://example.com/a.png"
    assert me["health_goal"] == "减脂"
    assert me["current_state"] == "有点累"
    assert me["joined_days"] >= 1

    pref_payload = {
        "tastes": ["中辣", "少油"],
        "taboos": ["香菜", "折耳根"],
        "allergens": ["花生"],
        "spicy_level": 2,
        "budget_level": 2,
    }
    resp = await client.patch("/api/v1/app/me/preferences", json=pref_payload, headers=headers)
    assert resp.status_code == 200

    resp = await client.get("/api/v1/app/me/preferences", headers=headers)
    assert resp.status_code == 200
    pref = resp.json()["data"]
    assert pref["tastes"] == pref_payload["tastes"]
    assert pref["taboos"] == pref_payload["taboos"]
    assert pref["allergens"] == pref_payload["allergens"]
    assert pref["spicy_level"] == 2
    assert pref["budget_level"] == 2
