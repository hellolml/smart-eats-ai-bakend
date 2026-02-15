import pytest


@pytest.mark.asyncio
async def test_app_restaurants_with_fallback(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_rest@example.com", "password": "secret123", "name": "eatout"},
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    resp = await client.get(
        "/api/v1/app/restaurants",
        params={"q": "火锅", "lat": 31.23, "lng": 121.47},
        headers=headers,
    )
    assert resp.status_code == 200
    rows = resp.json()["data"]
    assert rows
    first = rows[0]
    assert "provider" in first
    assert "provider_id" in first
    assert "distance_text" in first
    assert "price_text" in first

    resp = await client.get(
        f"/api/v1/app/restaurants/{first['provider']}/{first['provider_id']}",
        headers=headers,
    )
    assert resp.status_code == 200
    detail = resp.json()["data"]
    assert detail["provider"] == first["provider"]
    assert detail["provider_id"] == first["provider_id"]
