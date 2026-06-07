from __future__ import annotations

import pytest
from sqlalchemy import select

from app.common.config import settings
from app.domain.llm_config.resolver import resolve_model_config
from app.infra.db import AsyncSessionLocal
from app.infra.models.user import User


async def _auth_headers(client, email: str) -> dict[str, str]:
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": email, "password": "secret123", "name": "llm user"},
    )
    assert resp.status_code == 200
    return {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}


@pytest.mark.asyncio
async def test_llm_config_crud_does_not_return_plain_api_key(client):
    headers = await _auth_headers(client, "llm_config_crud@example.com")

    resp = await client.post(
        "/api/v1/app/chat/model-configs",
        headers=headers,
        json={
            "display_name": "My OpenAI",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-secret-123456",
            "model_planner": "gpt-4o-mini",
            "enabled": True,
            "is_default": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["display_name"] == "My OpenAI"
    assert data["api_key_hint"].startswith("sk-")
    assert "sk-test-secret-123456" not in str(data)

    config_id = data["id"]
    resp = await client.patch(
        f"/api/v1/app/chat/model-configs/{config_id}",
        headers=headers,
        json={"display_name": "Renamed", "model_writer": "gpt-4o-mini"},
    )
    assert resp.status_code == 200
    assert resp.json()["data"]["display_name"] == "Renamed"

    resp = await client.get("/api/v1/app/chat/model-configs", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"][0]["id"] == config_id


@pytest.mark.asyncio
async def test_llm_config_local_http_base_url_is_allowed(client):
    headers = await _auth_headers(client, "llm_config_private@example.com")

    resp = await client.post(
        "/api/v1/app/chat/model-configs",
        headers=headers,
        json={
            "display_name": "Localhost",
            "base_url": "http://127.0.0.1:8317/v1",
            "api_key": "sk-test-secret-123456",
            "model_planner": "gpt-5.4-mini",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["base_url"] == "http://127.0.0.1:8317/v1"
    assert data["model_planner"] == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_llm_config_default_and_models_endpoint(client):
    headers = await _auth_headers(client, "llm_config_models@example.com")

    resp = await client.post(
        "/api/v1/app/chat/model-configs",
        headers=headers,
        json={
            "display_name": "Custom Model",
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-test-secret-abcdef",
            "model_planner": "gpt-custom",
            "enabled": True,
            "is_default": True,
        },
    )
    assert resp.status_code == 200
    config_id = resp.json()["data"]["id"]

    resp = await client.get("/api/v1/app/chat/models", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    expected = f"config:{config_id}:gpt-custom"
    assert data["default"] == expected
    assert any(item.get("value") == expected and item.get("source") == "user_config" for item in data["models"])


@pytest.mark.asyncio
async def test_llm_config_enabled_manual_config_becomes_default_without_default_flag(client):
    headers = await _auth_headers(client, "llm_config_manual_default@example.com")

    resp = await client.post(
        "/api/v1/app/chat/model-configs",
        headers=headers,
        json={
            "display_name": "Manual Model",
            "base_url": "http://127.0.0.1:8317/v1",
            "api_key": "sk-test-secret-manual",
            "model_planner": "gpt-5.4-mini",
            "enabled": True,
            "is_default": False,
        },
    )
    assert resp.status_code == 200
    config_id = resp.json()["data"]["id"]

    resp = await client.get("/api/v1/app/chat/models", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    expected = f"config:{config_id}:gpt-5.4-mini"
    assert data["default"] == expected
    assert [item["source"] for item in data["models"]] == ["user_config"]
    assert data["models"][0]["value"] == expected


@pytest.mark.asyncio
async def test_llm_config_manual_config_overrides_env_selection(client):
    email = "llm_config_manual_over_env@example.com"
    headers = await _auth_headers(client, email)

    resp = await client.post(
        "/api/v1/app/chat/model-configs",
        headers=headers,
        json={
            "display_name": "Manual Runtime",
            "base_url": "http://127.0.0.1:8317/v1",
            "api_key": "sk-test-secret-runtime",
            "model_planner": "gpt-5.4-mini",
            "enabled": True,
            "is_default": False,
        },
    )
    assert resp.status_code == 200
    config_id = resp.json()["data"]["id"]

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        resolved = await resolve_model_config(db, user.id, "env:qwen:qwen3.5-flash")

    assert resolved.source == "user_config"
    assert resolved.config_id == config_id
    assert resolved.model_planner == "gpt-5.4-mini"


@pytest.mark.asyncio
async def test_llm_config_env_model_value_overrides_default_without_user(monkeypatch):
    monkeypatch.setattr(settings, "LLM_PROVIDERS", "openai,qwen")
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai:glm-5")
    monkeypatch.setattr(settings, "OPENAI_MODEL_PLANNER", "glm-5")
    monkeypatch.setattr(settings, "OPENAI_MODEL_WRITER", "glm-5")

    async with AsyncSessionLocal() as db:
        resolved = await resolve_model_config(db, None, "openai:kimi-k2.5")

    assert resolved.source == "env"
    assert resolved.provider_value == "openai:kimi-k2.5"
    assert resolved.model_planner == "kimi-k2.5"
    assert resolved.model_writer == "kimi-k2.5"


@pytest.mark.asyncio
async def test_llm_config_disabled_manual_config_falls_back_to_env(client):
    email = "llm_config_disabled_fallback@example.com"
    headers = await _auth_headers(client, email)

    resp = await client.post(
        "/api/v1/app/chat/model-configs",
        headers=headers,
        json={
            "display_name": "Disabled Manual",
            "base_url": "http://127.0.0.1:8317/v1",
            "api_key": "sk-test-secret-disabled",
            "model_planner": "gpt-disabled",
            "enabled": False,
            "is_default": False,
        },
    )
    assert resp.status_code == 200

    resp = await client.get("/api/v1/app/chat/models", headers=headers)
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["models"]
    assert all(item.get("source") == "env" for item in data["models"])

    async with AsyncSessionLocal() as db:
        user = (await db.execute(select(User).where(User.email == email))).scalar_one()
        resolved = await resolve_model_config(db, user.id, None)

    assert resolved.source == "env"


@pytest.mark.asyncio
async def test_llm_config_anthropic_config_appears_in_models(client):
    headers = await _auth_headers(client, "llm_config_anthropic@example.com")

    resp = await client.post(
        "/api/v1/app/chat/model-configs",
        headers=headers,
        json={
            "display_name": "Claude",
            "provider_type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-test-secret-abcdef",
            "model_planner": "claude-sonnet-4-6",
            "enabled": True,
            "is_default": True,
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["provider_type"] == "anthropic"
    assert "sk-ant-test-secret-abcdef" not in str(data)

    resp = await client.get("/api/v1/app/chat/models", headers=headers)
    assert resp.status_code == 200
    models = resp.json()["data"]["models"]
    expected = f"config:{data['id']}:claude-sonnet-4-6"
    assert any(item["value"] == expected and item["provider"] == "anthropic" for item in models)


@pytest.mark.asyncio
async def test_llm_config_anthropic_test_connection_sanitizes_errors(client, monkeypatch):
    headers = await _auth_headers(client, "llm_config_anthropic_test@example.com")

    async def fake_test(base_url: str, api_key: str, model: str):
        assert base_url == "https://api.anthropic.com"
        assert api_key == "sk-ant-live-secret-abcdef"
        assert model == "claude-sonnet-4-6"
        raise RuntimeError("bad key sk-ant-live-secret-abcdef")

    monkeypatch.setattr("app.domain.llm_config.service.LlmConfigService._test_anthropic_messages", fake_test)

    resp = await client.post(
        "/api/v1/app/chat/model-configs/test",
        headers=headers,
        json={
            "provider_type": "anthropic",
            "base_url": "https://api.anthropic.com",
            "api_key": "sk-ant-live-secret-abcdef",
            "model": "claude-sonnet-4-6",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "failed"
    assert "sk-ant-live-secret-abcdef" not in data["error"]
    assert "[redacted]" in data["error"]


@pytest.mark.asyncio
async def test_llm_config_test_connection_sanitizes_errors(client, monkeypatch):
    headers = await _auth_headers(client, "llm_config_test@example.com")

    class FakeModels:
        async def list(self):
            raise RuntimeError("bad key sk-live-secret-abcdef")

    class FakeOpenAI:
        def __init__(self, **_kwargs):
            self.models = FakeModels()

    monkeypatch.setattr("app.domain.llm_config.service.AsyncOpenAI", FakeOpenAI)

    resp = await client.post(
        "/api/v1/app/chat/model-configs/test",
        headers=headers,
        json={
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-live-secret-abcdef",
            "model": "gpt-4o-mini",
        },
    )
    assert resp.status_code == 200
    data = resp.json()["data"]
    assert data["status"] == "failed"
    assert "sk-live-secret-abcdef" not in data["error"]
    assert "[redacted]" in data["error"]
