import pytest


@pytest.mark.asyncio
async def test_app_games_blindbox_and_wheel(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_game@example.com", "password": "secret123", "name": "gamer"},
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    resp = await client.patch(
        "/api/v1/app/me/preferences",
        json={"taboos": ["salad", "noodles"], "allergens": []},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.post("/api/v1/app/games/blind-box/draw", json={}, headers=headers)
    assert resp.status_code == 200
    result = resp.json()["data"]["result"]
    assert result["name_cn"]
    assert result["emoji"]
    assert result["id"] not in {"salad", "noodles"}

    resp = await client.put(
        "/api/v1/app/games/wheel/current",
        json={"name": "Dinner", "options": ["salad", "pizza", "sushi"]},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.get("/api/v1/app/games/wheel/current", headers=headers)
    assert resp.status_code == 200
    assert len(resp.json()["data"]["options"]) == 3

    resp = await client.post(
        "/api/v1/app/games/wheel/current/spin",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200
    spin = resp.json()["data"]
    assert spin["winner"].lower() != "salad"
    assert 0 <= spin["angle"] <= 360
