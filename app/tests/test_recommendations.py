import pytest


@pytest.mark.asyncio
async def test_fridge_recommendations(client):
    register_payload = {
        "email": "fridge@example.com",
        "password": "secret123",
        "nickname": "cook",
    }
    resp = await client.post("/api/v1/auth/register", json=register_payload)
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    await client.post(
        "/api/v1/fridge/items",
        json={"name": "egg", "quantity": 2, "unit": "pcs"},
        headers=headers,
    )
    await client.post(
        "/api/v1/fridge/items",
        json={"name": "tomato", "quantity": 3, "unit": "pcs"},
        headers=headers,
    )

    resp = await client.get("/api/v1/fridge/recommendations", headers=headers)
    assert resp.status_code == 200
    recs = resp.json()["data"]
    assert recs
    titles = [item["title"].lower() for item in recs if item.get("title")]
    assert any("egg" in title or "tomato" in title for title in titles)
