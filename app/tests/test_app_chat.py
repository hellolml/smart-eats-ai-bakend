import asyncio
import json
from unittest.mock import MagicMock

import pytest
from openai import PermissionDeniedError

from app.agent.graph import _normalize_llm_upstream_error_message, run_chat_stream
from app.agent.state import ChatState


class _FakeRequest:
    async def is_disconnected(self):
        return False


class _FakeRedis:
    async def get(self, key):
        return None


class _FakeCheckpointerContext:
    async def __aenter__(self):
        return None

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeCompiledGraph:
    def __init__(self, updates):
        self._updates = updates

    async def astream(self, *_args, **_kwargs):
        for update in self._updates:
            yield update


class _FakeGraphBuilder:
    def __init__(self, updates):
        self._updates = updates

    def compile(self, **_kwargs):
        return _FakeCompiledGraph(self._updates)


@pytest.mark.asyncio
async def test_run_chat_stream_extracts_final_json_from_typed_graph_values(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    final_json = {
        "recommendations": [{"type": "note", "title": "typed state final", "reason": "graph_values"}],
        "followups": [],
        "warnings": [],
    }

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.supervisor.build_supervisor_runtime_graph",
        lambda **_kwargs: _FakeGraphBuilder([{"session_id": "s-values", "message": "你好", "final_json": final_json}]),
    )

    events = []
    async for item in run_chat_stream(_FakeRequest(), db=None, redis_client=_FakeRedis(), state=ChatState(session_id="s-values", message="你好")):
        events.append(item)

    assert events[-1]["event"] == "final"
    assert events[-1]["data"]["answer"]["recommendations"][0]["title"] == "typed state final"


@pytest.mark.asyncio
async def test_app_chat_sessions_can_filter_by_eat_scene(client):
    resp = await client.post(
        "/api/v1/app/auth/register",
        json={"email": "app_chat_scene_filter@example.com", "password": "secret123", "name": "scene"},
    )
    assert resp.status_code == 200
    headers = {"Authorization": f"Bearer {resp.json()['data']['access_token']}"}

    eat = await client.post("/api/v1/app/chat/session", headers=headers, json={"scene": "eat", "title": "今天吃点啥"})
    travel = await client.post("/api/v1/app/chat/session", headers=headers, json={"scene": "travel_planner", "title": "旅行计划"})
    assert eat.status_code == 200
    assert travel.status_code == 200

    resp = await client.get("/api/v1/app/chat/sessions?scene=eat", headers=headers)
    assert resp.status_code == 200
    sessions = resp.json()["data"]["sessions"]

    assert sessions
    assert {item["scene"] for item in sessions} == {"eat"}
    assert sessions[0]["title"] == "今天吃点啥"


@pytest.mark.asyncio
async def test_app_chat_stream_stop(client, monkeypatch):
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

    async def _slow_plan_tool_calls(self, system, user, available_tools):
        await asyncio.sleep(0.2)
        return {"content": "", "tool_calls": []}

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _slow_plan_tool_calls)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())

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

    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.supervisor.build_supervisor_runtime_graph",
        lambda **_kwargs: _FakeGraphBuilder(
            [
                {
                    "session_id": session_id,
                    "message": "附近吃什么",
                    "final_json": {
                        "recommendations": [
                            {"type": "note", "title": "已收到定位信息", "reason": "location_override_test"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                }
            ]
        ),
    )

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
    assert all(isinstance(value, str) and ":" in value for value in values)
    assert isinstance(payload.get("default"), str)
    assert payload["default"] in values


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


def test_normalize_llm_upstream_error_message_maps_invalid_image_payload():
    exc = RuntimeError(
        "Error code: 400 - {'error': {'message': '<400> InternalError.Algo.InvalidParameter: "
        "The provided messages input is invalid. The error info is [Unexpected item type in content.]'}}"
    )

    message = _normalize_llm_upstream_error_message(exc)

    assert "模型未接受本次图片输入" in message
