import pytest


@pytest.mark.asyncio
async def test_games_blindbox_and_wheel(client):
    register_payload = {
        "email": "game@example.com",
        "password": "secret123",
        "nickname": "gamer",
    }
    resp = await client.post("/api/v1/auth/register", json=register_payload)
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    pref_payload = {"avoid_ingredients": ["salad", "noodles"], "allergens": []}
    resp = await client.patch(
        "/api/v1/users/me/preferences", json=pref_payload, headers=headers
    )
    assert resp.status_code == 200

    resp = await client.post("/api/v1/games/blindbox/roll", json={}, headers=headers)
    assert resp.status_code == 200
    result = resp.json()["data"]["result"]
    assert result not in pref_payload["avoid_ingredients"]

    wheel_payload = {
        "name": "Dinner",
        "options": [{"label": "salad"}, {"label": "pizza"}],
    }
    resp = await client.post("/api/v1/games/wheels", json=wheel_payload, headers=headers)
    assert resp.status_code == 200
    wheel_id = resp.json()["data"]["id"]

    resp = await client.post(
        f"/api/v1/games/wheels/{wheel_id}/spin",
        json={},
        headers=headers,
    )
    assert resp.status_code == 200
    selected = resp.json()["data"]["selected_option"]
    label = selected["label"] if isinstance(selected, dict) else str(selected)
    assert "salad" not in label.lower()
