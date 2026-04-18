import pytest

from app.common.config import settings


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
        json={"email": "app_changepw@example.com", "password": "oldPass123", "name": "name"},
    )
    assert resp.status_code == 200
    tokens = resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    resp = await client.post(
        "/api/v1/app/auth/password/change",
        json={"old_password": "oldPass123", "new_password": "newPass456"},
        headers=headers,
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_changepw@example.com", "password": "oldPass123"},
    )
    assert resp.status_code == 401
    assert resp.json()["code"] == 41009

    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_changepw@example.com", "password": "newPass456"},
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_app_login_accepts_phone_payload(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"phone": "15509296651", "password": "abc12345", "name": "phone_user"},
    )
    assert resp.status_code == 200

    resp = await client.post(
        "/api/v1/app/auth/login",
        json={"phone": "15509296651", "password": "abc12345"},
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
    assert replay_refresh.json()["code"] == 41004

    latest_refresh = first_refresh.json()["data"]["refresh_token"]
    blocked_refresh = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": latest_refresh},
    )
    assert blocked_refresh.status_code == 401
    assert blocked_refresh.json()["code"] == 41005


@pytest.mark.asyncio
async def test_app_login_lock_after_multiple_failures(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_lock@example.com", "password": "secret123", "name": "lock"},
    )
    assert register_resp.status_code == 200

    for _ in range(5):
        bad = await client.post(
            "/api/v1/app/auth/login",
            json={"account": "app_lock@example.com", "password": "wrong123"},
        )
        assert bad.status_code == 401

    locked = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_lock@example.com", "password": "secret123"},
    )
    assert locked.status_code == 423
    assert locked.json()["code"] == 41003


@pytest.mark.asyncio
async def test_app_refresh_supports_http_only_cookie(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_cookie_refresh@example.com", "password": "secret123", "name": "cookie"},
    )
    assert register_resp.status_code == 200
    csrf_token = register_resp.json()["data"]["csrf_token"]

    refresh_resp = await client.post(
        "/api/v1/app/auth/refresh",
        json={},
        headers={"x-csrf-token": csrf_token},
    )
    assert refresh_resp.status_code == 200
    assert refresh_resp.json()["data"]["access_token"]


@pytest.mark.asyncio
async def test_app_refresh_cookie_requires_csrf_header(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_cookie_csrf@example.com", "password": "secret123", "name": "cookie-csrf"},
    )
    assert register_resp.status_code == 200

    refresh_resp = await client.post("/api/v1/app/auth/refresh", json={})
    assert refresh_resp.status_code == 403


@pytest.mark.asyncio
async def test_app_sessions_and_logout_all(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_sessions@example.com", "password": "secret123", "name": "sessions"},
    )
    assert register_resp.status_code == 200
    tokens = register_resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    sessions_resp = await client.get("/api/v1/app/auth/sessions", headers=headers)
    assert sessions_resp.status_code == 200
    items = sessions_resp.json()["data"]["items"]
    assert len(items) >= 1
    sid = items[0]["id"]

    revoke_resp = await client.delete(f"/api/v1/app/auth/sessions/{sid}", headers=headers)
    assert revoke_resp.status_code == 200

    login_resp = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_sessions@example.com", "password": "secret123"},
    )
    assert login_resp.status_code == 200
    tokens2 = login_resp.json()["data"]
    headers2 = {"Authorization": f"Bearer {tokens2['access_token']}"}

    logout_all_resp = await client.post("/api/v1/app/auth/logout-all", headers=headers2)
    assert logout_all_resp.status_code == 200

    refresh_after_logout_all = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": tokens2["refresh_token"]},
    )
    assert refresh_after_logout_all.status_code == 401


@pytest.mark.asyncio
async def test_app_disabled_auth_routes_return_404(client):
    disabled_routes = [
        ("post", "/api/v1/app/auth/register/request-otp", {"email": "disabled@example.com"}),
        ("post", "/api/v1/app/auth/register/confirm", {"email": "disabled@example.com", "code": "123456", "password": "secret123"}),
        ("post", "/api/v1/app/auth/login/otp/request", {"account": "15500001111"}),
        ("post", "/api/v1/app/auth/login/otp/confirm", {"account": "15500001111", "code": "123456"}),
        ("post", "/api/v1/app/auth/login/one-click", {"token": "mock:15500002222"}),
        ("post", "/api/v1/app/auth/password/reset-request", {"account": "disabled@example.com"}),
        ("post", "/api/v1/app/auth/password/reset-confirm", {"account": "disabled@example.com", "code": "123456", "new_password": "newPass456"}),
        ("get", "/api/v1/app/auth/oauth/github/start", None),
    ]

    for method, path, payload in disabled_routes:
        if method == "get":
            resp = await client.get(path)
        else:
            resp = await client.post(path, json=payload)
        assert resp.status_code == 404


@pytest.mark.asyncio
async def test_app_auth_events_endpoint(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_events@example.com", "password": "secret123", "name": "events"},
    )
    assert register_resp.status_code == 200
    tokens = register_resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    login_fail = await client.post(
        "/api/v1/app/auth/login",
        json={"account": "app_events@example.com", "password": "wrong123"},
    )
    assert login_fail.status_code == 401

    events_resp = await client.get("/api/v1/app/auth/events?limit=10", headers=headers)
    assert events_resp.status_code == 200
    data = events_resp.json()["data"]
    assert isinstance(data["items"], list)


@pytest.mark.asyncio
async def test_app_auth_config_check_endpoint(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_config_check@example.com", "password": "secret123", "name": "cfg"},
    )
    assert register_resp.status_code == 200
    tokens = register_resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    cfg_resp = await client.get("/api/v1/app/auth/config-check", headers=headers)
    assert cfg_resp.status_code == 200
    data = cfg_resp.json()["data"]
    assert data["ready"] is True
    assert data["checks"]["password_auth"] == {"enabled": True, "ready": True, "missing": []}
    assert data["checks"]["otp_auth"] == {"enabled": False, "ready": False, "missing": []}
    assert data["checks"]["oauth_github"] == {"enabled": False, "ready": False, "missing": []}


@pytest.mark.asyncio
async def test_app_auth_methods_endpoint(client):
    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_methods@example.com", "phone": "15500003333", "password": "secret123", "name": "methods"},
    )
    assert register_resp.status_code == 200
    tokens = register_resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    methods_resp = await client.get("/api/v1/app/auth/methods", headers=headers)
    assert methods_resp.status_code == 200
    data = methods_resp.json()["data"]
    assert data["email_bound"] is True
    assert data["phone_bound"] is True
    assert data["oauth_providers"] == []
    assert data["github_bound"] is False
    assert data["phone_enabled"] is True
    assert data["email_enabled"] is True
    assert data["oauth_enabled"] == {"github": False}


@pytest.mark.asyncio
async def test_app_public_config_endpoint(client):
    resp = await client.get("/api/v1/app/auth/public-config")
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["auth"]["password_login"] is True
    assert data["auth"]["register"] is True
    assert data["auth"]["otp_login"] is False
    assert data["auth"]["otp_register"] is False
    assert data["auth"]["oauth"] == {"github": False}
    assert data["auth"]["phone_enabled"] is True
    assert data["auth"]["email_enabled"] is True


@pytest.mark.asyncio
async def test_app_otp_routes_enabled_by_flag(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_AUTH_OTP_ENABLED", True)

    request_resp = await client.post(
        "/api/v1/app/auth/register/request-otp",
        json={"email": "otp_enabled@example.com"},
    )
    assert request_resp.status_code == 200
    payload = request_resp.json()["data"]
    assert payload["sent"] is True
    code = payload["debug_code"]

    confirm_resp = await client.post(
        "/api/v1/app/auth/register/confirm",
        json={
            "email": "otp_enabled@example.com",
            "code": code,
            "password": "secret123",
            "name": "otp",
        },
    )
    assert confirm_resp.status_code == 200
    tokens = confirm_resp.json()["data"]
    assert tokens["access_token"]

    login_request = await client.post(
        "/api/v1/app/auth/login/otp/request",
        json={"account": "otp_enabled@example.com"},
    )
    assert login_request.status_code == 200
    login_code = login_request.json()["data"]["debug_code"]

    login_confirm = await client.post(
        "/api/v1/app/auth/login/otp/confirm",
        json={"account": "otp_enabled@example.com", "code": login_code},
    )
    assert login_confirm.status_code == 200
    assert login_confirm.json()["data"]["access_token"]


@pytest.mark.asyncio
async def test_app_phone_channel_can_be_disabled(client, monkeypatch):
    monkeypatch.setattr(settings, "APP_AUTH_PHONE_ENABLED", False)

    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"phone": "15500009999", "password": "secret123", "name": "phone_off"},
    )
    assert register_resp.status_code == 404
