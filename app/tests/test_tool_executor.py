from __future__ import annotations

import asyncio

import pytest

from app.agent.state import ChatState
from app.agent.tool_executor import ToolExecutor


class _FakeTool:
    def __init__(self, func):
        self.func = func


@pytest.mark.asyncio
async def test_execute_calls_keeps_each_tool_identity(monkeypatch):
    async def _tool_a(args):
        await asyncio.sleep(0.01)
        return {"tool": "a", "session_id": args.get("session_id")}

    async def _tool_b(args):
        await asyncio.sleep(0.01)
        return {"tool": "b", "session_id": args.get("session_id")}

    def _fake_get_tool(name, _allowlist=None):
        if name == "tool_a":
            return _FakeTool(_tool_a)
        if name == "tool_b":
            return _FakeTool(_tool_b)
        return None

    monkeypatch.setattr("app.agent.tool_executor.get_tool", _fake_get_tool)

    executor = ToolExecutor(["tool_a", "tool_b"], redis_client=None, db=None, max_workers=2)
    state = ChatState(session_id="s1", user_id="u1", message="hi")
    calls = [
        {"name": "tool_a", "args": {"x": 1}},
        {"name": "tool_b", "args": {"y": 2}},
    ]

    results = await executor.execute_calls(calls, state, servers_path=None)

    assert len(results) == 2
    assert results[0]["name"] == "tool_a"
    assert results[1]["name"] == "tool_b"
    assert results[0]["result"]["tool"] == "a"
    assert results[1]["result"]["tool"] == "b"


@pytest.mark.asyncio
async def test_execute_calls_serializes_location_dependency(monkeypatch):
    state_box = {"located": False}

    async def _get_ip_location(_args):
        await asyncio.sleep(0.01)
        state_box["located"] = True
        return {"lat": 1.0, "lng": 2.0}

    async def _search_restaurants(_args):
        await asyncio.sleep(0.01)
        if not state_box["located"]:
            return {"error": "missing_location"}
        return [{"name": "ok"}]

    def _fake_get_tool(name, _allowlist=None):
        if name == "get_ip_location":
            return _FakeTool(_get_ip_location)
        if name == "search_restaurants":
            return _FakeTool(_search_restaurants)
        return None

    monkeypatch.setattr("app.agent.tool_executor.get_tool", _fake_get_tool)

    def _serial_decider(calls):
        names = [item.get("name") for item in calls]
        return "get_ip_location" in names and "search_restaurants" in names

    executor = ToolExecutor(
        ["get_ip_location", "search_restaurants"],
        redis_client=None,
        db=None,
        max_workers=2,
        serial_execution_decider=_serial_decider,
    )
    state = ChatState(session_id="s1", user_id="u1", message="出去吃")
    calls = [
        {"name": "get_ip_location", "args": {}},
        {"name": "search_restaurants", "args": {"query": "", "lat": 0.0, "lng": 0.0}},
    ]

    results = await executor.execute_calls(calls, state, servers_path=None)

    assert results[0]["name"] == "get_ip_location"
    assert results[1]["name"] == "search_restaurants"
    assert isinstance(results[1]["result"], list)
