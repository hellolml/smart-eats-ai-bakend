import asyncio
import json
from unittest.mock import MagicMock

import pytest
from openai import PermissionDeniedError

from app.agent.graph import _normalize_llm_upstream_error_message


@pytest.mark.asyncio
async def test_app_chat_stream_stop(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_chat@example.com", "password": "secret123", "name": "chatter"},
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    resp = await client.post("/api/v1/app/chat/session", headers=headers)
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    resp = await client.get("/api/v1/app/chat/sessions", headers=headers)
    assert resp.status_code == 200
    assert resp.json()["data"]["sessions"]

    got_final = False
    stopped_flag = None
    current_event = None

    async def send_stop():
        await asyncio.sleep(0.1)
        stop_resp = await client.post(f"/api/v1/app/chat/session/{session_id}/stop", headers=headers)
        assert stop_resp.status_code == 200

    stop_task = asyncio.create_task(send_stop())

    async with client.stream(
        "POST",
        f"/api/v1/app/chat/session/{session_id}/stream",
        headers=headers,
        json={"message": "quick dinner"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                payload = json.loads(raw)
                if current_event == "final":
                    got_final = True
                    stopped_flag = payload.get("stopped")
                    break

    await stop_task

    assert got_final
    assert stopped_flag is True

    resp = await client.get(f"/api/v1/app/chat/session/{session_id}/messages", headers=headers)
    assert resp.status_code == 200
    assert "messages" in resp.json()["data"]


@pytest.mark.asyncio
async def test_app_chat_stream_accepts_client_location_overrides(client, monkeypatch):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_chat_geo@example.com", "password": "secret123", "name": "geo chatter"},
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    resp = await client.post("/api/v1/app/chat/session", headers=headers)
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "已收到定位信息", "reason": "location_override_test"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_test_final",
                    "type": "tool_call",
                }
            ],
        }

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)

    got_final = False
    current_event = None

    async with client.stream(
        "POST",
        f"/api/v1/app/chat/session/{session_id}/stream",
        headers=headers,
        json={
            "message": "附近吃什么",
            "client_context_overrides": {
                "environment": {
                    "location": {
                        "lat": 31.2304,
                        "lng": 121.4737,
                        "source": "device"
                    }
                }
            }
        },
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:") and current_event == "final":
                got_final = True
                break

    assert got_final


@pytest.mark.asyncio
async def test_app_chat_models_endpoint_returns_model_options(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_chat_models@example.com", "password": "secret123", "name": "models user"},
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    resp = await client.get("/api/v1/app/chat/models", headers=headers)
    assert resp.status_code == 200

    payload = resp.json()["data"]
    assert isinstance(payload.get("models"), list)
    assert payload["models"]

    values = [item.get("value") for item in payload["models"] if isinstance(item, dict)]
    assert values == [
        "qwen:qwen3.5-flash",
        "qwen:qwen3.5-plus",
        "qwen:qwen3.5-flash-2026-02-23",
        "qwen:qwen3.5-plus-2026-02-15",
        "qwen:qwen3.5-397b-a17b",
    ]
    assert isinstance(payload.get("default"), str)
    assert payload["default"] == "qwen:qwen3.5-flash"


def test_normalize_llm_upstream_error_message_maps_free_tier_quota():
    response = MagicMock()
    response.request = MagicMock()
    response.status_code = 403
    response.headers = {}

    exc = PermissionDeniedError(
        "Error code: 403",
        response=response,
        body={
            "error": {
                "message": "The free tier of the model has been exhausted.",
                "type": "AllocationQuota.FreeTierOnly",
                "code": "AllocationQuota.FreeTierOnly",
            }
        },
    )

    message = _normalize_llm_upstream_error_message(exc)

    assert "免费额度已用尽" in message
    assert "仅使用免费额度" in message
