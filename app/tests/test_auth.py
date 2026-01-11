import pytest


@pytest.mark.asyncio
async def test_auth_flow(client):
    register_payload = {
        "email": "user@example.com",
        "password": "secret123",
        "nickname": "tester",
    }
    resp = await client.post("/api/v1/auth/register", json=register_payload)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert "access_token" in data

    login_payload = {"account": "user@example.com", "password": "secret123"}
    resp = await client.post("/api/v1/auth/login", json=login_payload)
    assert resp.status_code == 200
    tokens = resp.json()["data"]

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.get("/api/v1/users/me", headers=headers)
    assert resp.status_code == 200
    me_data = resp.json()["data"]
    assert me_data["email"] == "user@example.com"

    resp = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 200
    refreshed = resp.json()["data"]

    resp = await client.post(
        "/api/v1/auth/logout",
        json={"refresh_token": refreshed["refresh_token"]},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/v1/auth/token/refresh",
        json={"refresh_token": refreshed["refresh_token"]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_change_password(client):
    register_payload = {
        "email": "changepw@example.com",
        "password": "secret123",
        "nickname": "changer",
    }
    resp = await client.post("/api/v1/auth/register", json=register_payload)
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    change_payload = {"old_password": "secret123", "new_password": "newsecret456"}
    resp = await client.post("/api/v1/auth/password/change", json=change_payload, headers=headers)
    assert resp.status_code == 200

    resp = await client.post(
        "/api/v1/auth/login",
        json={"account": "changepw@example.com", "password": "secret123"},
    )
    assert resp.status_code == 401

    resp = await client.post(
        "/api/v1/auth/login",
        json={"account": "changepw@example.com", "password": "newsecret456"},
    )
    assert resp.status_code == 200
