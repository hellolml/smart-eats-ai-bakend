import pytest


@pytest.mark.asyncio
async def test_app_auth_flow(client):
    register_payload = {
        "email": "app_user@example.com",
        "password": "secret123",
        "name": "tester",
    }
    resp = await client.post("/api/v1/app/auth/register", json=register_payload)
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    assert tokens["access_token"]
    assert tokens["refresh_token"]

    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_user@example.com", "password": "secret123"},
    )
    assert resp.status_code == 200
    tokens = resp.json()["data"]

    headers = {"Authorization": f"Bearer {tokens['access_token']}"}
    resp = await client.get("/api/v1/app/me", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["email"] == "app_user@example.com"

    resp = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert resp.status_code == 200
    refreshed = resp.json()["data"]

    resp = await client.post(
        "/api/v1/app/auth/logout",
        json={"refresh_token": refreshed["refresh_token"]},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": refreshed["refresh_token"]},
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_app_change_password(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_changepw@example.com", "password": "old123", "name": "name"},
    )
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/api/v1/app/auth/password/change",
        json={"old_password": "old123", "new_password": "new456"},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_changepw@example.com", "password": "old123"},
    )
    assert resp.status_code == 401

    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_changepw@example.com", "password": "new456"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_app_login_accepts_phone_payload(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"phone": "15509296651", "password": "12345678", "name": "phone_user"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"phone": "15509296651", "password": "12345678"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


@pytest.mark.asyncio
async def test_app_logout_accepts_camel_refresh_token(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_logout_camel@example.com", "password": "secret123", "name": "logout"},
    )
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/api/v1/app/auth/logout",
        json={"refreshToken": tokens["refresh_token"]},
        headers=headers,
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["logged_out"] is True


@pytest.mark.asyncio
async def test_app_password_reset_flow(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_reset@example.com", "password": "old123", "name": "reset"},
    )
    assert register_resp.status_code == 200

    req_resp = await client.post(
        "/api/v1/app/auth/password/reset-request",
        json={"account": "app_reset@example.com"},
    )
    assert req_resp.status_code == 200
    code = req_resp.json()["data"]["debug_code"]

    confirm_resp = await client.post(
        "/api/v1/app/auth/password/reset-confirm",
        json={"account": "app_reset@example.com", "code": code, "new_password": "new456"},
    )
    assert confirm_resp.status_code == 200

    old_login = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_reset@example.com", "password": "old123"},
    )
    assert old_login.status_code == 401

    new_login = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_reset@example.com", "password": "new456"},
    )
    assert new_login.status_code == 200


@pytest.mark.asyncio
async def test_app_refresh_token_replay_detected(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_replay@example.com", "password": "secret123", "name": "replay"},
    )
    assert register_resp.status_code == 200
    tokens = register_resp.json()["data"]

    first_refresh = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert first_refresh.status_code == 200

    replay_refresh = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": tokens["refresh_token"]},
    )
    assert replay_refresh.status_code == 401

    latest_refresh = first_refresh.json()["data"]["refresh_token"]
    blocked_refresh = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": latest_refresh},
    )
    assert blocked_refresh.status_code == 401
