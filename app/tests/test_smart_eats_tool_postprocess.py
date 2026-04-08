from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.agent.agents import smart_eats as smart_eats_module
from app.agent.agents.smart_eats import (
    _apply_official_tool_postprocess,
    _finalize_official_after_tools,
    get_smart_eats_agent_config,
)


@pytest.mark.asyncio
async def test_apply_official_tool_postprocess_writes_final_from_submit_tool(monkeypatch):
    chat_state = smart_eats_module.SmartEatsState(session_id="s-submit", steps_left=2)
    save_tool_message = AsyncMock()
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_tool_message", save_tool_message)

    message = SimpleNamespace(
        name="submit_final_answer",
        tool_call_id="call_final",
        artifact={
            "_final_answer": {
                "recommendations": [{"type": "note", "title": "完成", "reason": "done"}],
                "followups": [],
                "warnings": [],
            }
        },
        content=None,
    )

    await _apply_official_tool_postprocess(
        chat_state,
        tool_messages=[message],
        call_args_map={"call_final": {"recommendations": []}},
        db=None,
        redis_client=None,
        agent_config=get_smart_eats_agent_config(),
    )

    assert chat_state.final_json is not None
    assert chat_state.final_json["recommendations"][0]["title"] == "完成"
    assert chat_state.tool_calls == []
    assert chat_state.observations == []
    save_tool_message.assert_not_called()


@pytest.mark.asyncio
async def test_apply_official_tool_postprocess_records_tool_observation_and_history(monkeypatch):
    chat_state = smart_eats_module.SmartEatsState(session_id="s-tool", steps_left=2)
    save_tool_message = AsyncMock()
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_tool_message", save_tool_message)

    message = SimpleNamespace(
        name="geocode_location",
        tool_call_id="call_geo",
        artifact={"lat": 28.2, "lng": 112.9, "city": "长沙", "location_source": "geocode"},
        content=None,
    )

    await _apply_official_tool_postprocess(
        chat_state,
        tool_messages=[message],
        call_args_map={"call_geo": {"query": "长沙市政府"}},
        db=None,
        redis_client=None,
        agent_config=get_smart_eats_agent_config(),
    )

    assert chat_state.final_json is None
    assert chat_state.tool_calls == [{"name": "geocode_location", "args": {"query": "长沙市政府"}, "latency_ms": 0}]
    assert chat_state.observations == [
        {
            "tool": "geocode_location",
            "result": {"lat": 28.2, "lng": 112.9, "city": "长沙", "location_source": "geocode"},
        }
    ]
    assert chat_state.context is not None
    assert chat_state.context.get("location") == {"lat": 28.2, "lng": 112.9}
    assert chat_state.location_source == "geocode"
    assert chat_state.events and chat_state.events[0]["event"] == "tool_call"
    save_tool_message.assert_awaited_once()


@pytest.mark.asyncio
async def test_finalize_official_after_tools_triggers_best_effort_at_last_step():
    chat_state = smart_eats_module.SmartEatsState(session_id="s-best-effort", steps_left=1)

    _finalize_official_after_tools(chat_state, get_smart_eats_agent_config())

    assert chat_state.steps_left == 0
    assert chat_state.final_json is not None
    assert chat_state.final_json["recommendations"][0]["reason"] == "fallback"
    assert chat_state.pending_tool_calls == []


@pytest.mark.asyncio
async def test_finalize_official_after_tools_does_not_trigger_best_effort_before_last_step():
    chat_state = smart_eats_module.SmartEatsState(session_id="s-best-effort-later", steps_left=2)

    _finalize_official_after_tools(chat_state, get_smart_eats_agent_config())

    assert chat_state.steps_left == 1
    assert chat_state.final_json is None
    assert chat_state.pending_tool_calls == []
