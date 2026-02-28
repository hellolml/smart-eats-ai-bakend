from __future__ import annotations

import pytest

from app.agent import tools_registry
from app.agent.agent_registry import create_agent_config
from app.agent.graph import build_langgraph_official
from app.agent.state import ChatState


@pytest.mark.asyncio
async def test_official_graph_toolnode_roundtrip(monkeypatch, override_redis):
    tool_name = "test_echo_tool"

    @tools_registry.register_tool(
        name=tool_name,
        description="test echo tool",
        input_schema={
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
        },
        output_schema={"type": "object"},
    )
    async def _test_echo_tool(args):
        return {
            "echo": args.get("query"),
            "session_id": args.get("session_id"),
        }

    call_counter = {"count": 0}

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        call_counter["count"] += 1
        if call_counter["count"] == 1:
            return {
                "content": "",
                "tool_calls": [
                    {
                        "name": tool_name,
                        "args": {"query": "火锅"},
                        "id": "call_1",
                        "type": "tool_call",
                    }
                ],
            }
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "完成", "reason": "tool_roundtrip"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_2",
                    "type": "tool_call",
                }
            ],
        }

    async def _noop_ensure_chat_session(db, state):
        return None

    async def _noop_refresh_context(db, redis_client, state, agent_config, emit_context_event=True):
        state.context = {"system_prompt": "test system"}

    async def _noop_save_tool_message(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent.graph.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr("app.agent.graph._ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr("app.agent.graph._refresh_observation_context", _noop_refresh_context)
    monkeypatch.setattr("app.agent.graph.history.save_tool_message", _noop_save_tool_message)

    config = create_agent_config(
        name="official_test_agent",
        scene="chat",
        tool_names=[tool_name],
        max_steps=3,
    )
    graph = build_langgraph_official(
        db=None,
        redis_client=override_redis,
        provider=None,
        agent_config=config,
    ).compile()

    result = await graph.ainvoke(ChatState(session_id="s-official", message="帮我推荐").__dict__)

    assert result["final_json"]["recommendations"][0]["title"] == "完成"
    assert any(item.get("tool") == tool_name for item in result.get("observations", []))
    assert call_counter["count"] >= 2

    tools_registry.TOOLS.pop(tool_name, None)
