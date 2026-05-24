from __future__ import annotations

import asyncio

import pytest

from app.infra.mcp import client as mcp_client


class _FakeTool:
    name = "maps_text_search"

    def __init__(self) -> None:
        self.active = 0
        self.max_active = 0

    async def ainvoke(self, args):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.01)
        self.active -= 1
        return {"args": args}


class _FakeClient:
    def __init__(self, tool: _FakeTool) -> None:
        self.tool = tool
        self.get_tools_calls = 0

    async def get_tools(self):
        self.get_tools_calls += 1
        await asyncio.sleep(0.01)
        return [self.tool]


@pytest.mark.asyncio
async def test_call_tool_caches_tools_and_serializes_server_calls(monkeypatch):
    mcp_client._CLIENTS.clear()
    mcp_client._TOOLS_CACHE.clear()
    mcp_client._TOOLS_LOCKS.clear()
    mcp_client._CALL_SEMAPHORES.clear()

    tool = _FakeTool()
    fake_client = _FakeClient(tool)

    async def _fake_get_client(_servers):
        return fake_client

    monkeypatch.setattr(mcp_client, "get_client", _fake_get_client)
    servers = {"amap": {"transport": "sse", "url": "https://example.com/sse"}}

    results = await asyncio.gather(
        mcp_client.call_tool(servers, "amap", "maps_text_search", {"keywords": "赛里木湖"}),
        mcp_client.call_tool(servers, "amap", "maps_text_search", {"keywords": "伊宁"}),
    )

    assert [item["args"]["keywords"] for item in results] == ["赛里木湖", "伊宁"]
    assert fake_client.get_tools_calls == 1
    assert tool.max_active == 1
