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
    assert resp.json()["code"] == 41009

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
    assert replay_refresh.json()["code"] == 41004

    latest_refresh = first_refresh.json()["data"]["refresh_token"]
    blocked_refresh = await client.post(
        "/api/v1/app/auth/refresh",
        json={"refresh_token": latest_refresh},
    )
    assert blocked_refresh.status_code == 401
    assert blocked_refresh.json()["code"] == 41005


@pytest.mark.asyncio
async def test_app_register_otp_confirm_flow(client):
    otp_resp = await client.post(
        "/api/v1/app/auth/register/request-otp",
        json={"email": "app_otp_register@example.com"},
    )
    assert otp_resp.status_code == 200
    code = otp_resp.json()["data"]["debug_code"]

    confirm_resp = await client.post(
        "/api/v1/app/auth/register/confirm",
        json={
            "email": "app_otp_register@example.com",
            "code": code,
            "password": "secret123",
            "name": "otp-user",
        },
    )
    assert confirm_resp.status_code == 200
    data = confirm_resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


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
            json={"account": "app_lock@example.com", "password": "wrong"},
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

    # use cookie fallback (no refresh_token in body) with csrf header
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
async def test_app_oauth_github_start_and_callback(client, monkeypatch):
    async def fake_fetch(_code: str):
        return {
            "provider": "github",
            "provider_uid": "gh_123",
            "nickname": "GH User",
            "email": "gh_user@example.com",
            "access_token": "gh_access",
        }

    monkeypatch.setattr("app.domain.app.service.settings.GITHUB_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setattr("app.domain.app.service.settings.GITHUB_OAUTH_REDIRECT_URI", "http://localhost/callback")
    monkeypatch.setattr("app.domain.app.service.settings.GITHUB_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr("app.domain.app.service.AppBffService._oauth_fetch_github_user", staticmethod(fake_fetch))

    start_resp = await client.get("/api/v1/app/auth/oauth/github/start")
    assert start_resp.status_code == 200
    start_data = start_resp.json()["data"]
    assert start_data["auth_url"]
    assert start_data["state"]

    callback_resp = await client.post(
        "/api/v1/app/auth/oauth/github/callback",
        json={"code": "abc", "state": start_data["state"]},
    )
    assert callback_resp.status_code == 200
    data = callback_resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


@pytest.mark.asyncio
async def test_app_sms_login_flow(client):
    request_resp = await client.post(
        "/api/v1/app/auth/login/sms/request",
        json={"phone": "15500001111"},
    )
    assert request_resp.status_code == 200
    code = request_resp.json()["data"]["debug_code"]

    confirm_resp = await client.post(
        "/api/v1/app/auth/login/sms/confirm",
        json={"phone": "15500001111", "code": code},
    )
    assert confirm_resp.status_code == 200
    data = confirm_resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


@pytest.mark.asyncio
async def test_app_phone_one_click_login_mock(client):
    resp = await client.post(
        "/api/v1/app/auth/login/one-click",
        json={"token": "mock:15500002222"},
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["access_token"]
    assert data["refresh_token"]


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
    assert isinstance(data["oauth_providers"], list)


@pytest.mark.asyncio
async def test_app_oauth_bind_unbind(client, monkeypatch):
    async def fake_fetch(_code: str):
        return {
            "provider": "github",
            "provider_uid": "gh_bind_1",
            "nickname": "GH Bind",
            "email": "gh_bind@example.com",
            "access_token": "gh_access",
        }

    monkeypatch.setattr("app.domain.app.service.settings.GITHUB_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setattr("app.domain.app.service.settings.GITHUB_OAUTH_REDIRECT_URI", "http://localhost/callback")
    monkeypatch.setattr("app.domain.app.service.settings.GITHUB_OAUTH_CLIENT_SECRET", "secret")
    monkeypatch.setattr("app.domain.app.service.AppBffService._oauth_fetch_github_user", staticmethod(fake_fetch))

    register_resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "oauth_bind_local@example.com", "password": "secret123", "name": "local"},
    )
    assert register_resp.status_code == 200
    tokens = register_resp.json()["data"]
    headers = {"Authorization": f"Bearer {tokens['access_token']}"}

    start_resp = await client.get("/api/v1/app/auth/oauth/github/start")
    assert start_resp.status_code == 200
    state = start_resp.json()["data"]["state"]

    bind_resp = await client.post(
        "/api/v1/app/auth/oauth/github/bind",
        json={"code": "abc", "state": state},
        headers=headers,
    )
    assert bind_resp.status_code == 200
    assert bind_resp.json()["data"]["bound"] is True

    unbind_resp = await client.delete("/api/v1/app/auth/oauth/github", headers=headers)
    assert unbind_resp.status_code == 200
    assert unbind_resp.json()["data"]["removed"] is True
