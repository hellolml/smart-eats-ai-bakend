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

    resp = await client.patch(
        "/api/v1/app/me/goal-state",
        json={"health_goal": "增肌", "current_state": "精神满满"},
        headers=headers,
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["health_goal"] == "增肌"
    assert data["current_state"] == "精神满满"

    resp = await client.get("/api/v1/app/home/overview", headers=headers)
    assert resp.status_code == 200
    overview = resp.json()["data"]
    assert overview["name"] == "NewName"
    assert overview["health_goal"] == "增肌"
    assert overview["current_state"] == "精神满满"
    assert "weather" in overview
    assert overview["weather"]["city"] == "北京"
    assert "display" in overview["weather"]


@pytest.mark.asyncio
async def test_home_overview_uses_explicit_location(client, monkeypatch):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_geo@example.com", "password": "secret123", "name": "GeoUser"},
    )
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    async def fake_reverse_geocode_region(location, *, servers_path):
        assert location == {"lat": 39.9042, "lng": 116.4074}
        return {"district": "浦东新区", "city": "上海市", "province": "上海市"}

    async def fake_get_weather(city, *, servers_path):
        assert city == "上海市"
        return {"city": city, "status": "晴", "temperature_c": 28}

    monkeypatch.setattr("app.infra.external.amap.amap.reverse_geocode_region", fake_reverse_geocode_region)
    monkeypatch.setattr("app.infra.external.amap.amap.get_weather", fake_get_weather)

    resp = await client.get(
        "/api/v1/app/home/overview?lat=39.9042&lng=116.4074",
        headers=headers,
    )
    assert resp.status_code == 200
    overview = resp.json()["data"]
    assert overview["weather"]["city"] == "浦东新区"
    assert overview["weather"]["temperature_text"] == "28°"
    assert overview["weather"]["display"] == "28°晴"
    assert overview["weather"]["location"] == {"lat": 39.9042, "lng": 116.4074}
