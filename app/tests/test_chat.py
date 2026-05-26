import asyncio
import json

import pytest

from app.agent.agents.smart_eats import _best_effort_final_from_observations, get_smart_eats_agent_config
from app.agent.graph import _render_final_text, run_chat_stream
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


class _FakeStatefulCheckpointerContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


@pytest.mark.asyncio
async def test_run_chat_stream_resume_without_pending_checkpoint_uses_state_input(monkeypatch):
    async def _noop_save_assistant_message(*_args, **_kwargs):
        return None

    final_json = {
        "recommendations": [{"type": "note", "title": "继续", "reason": "resume"}],
        "followups": [],
        "warnings": [],
    }
    captured = {}

    class _ResumeGraph:
        async def aget_state(self, _config):
            return None

        async def astream(self, input_payload, *_args, **kwargs):
            captured["input_payload"] = input_payload
            captured["config"] = kwargs.get("config")
            yield {"session_id": "s-resume", "message": "继续", "final_json": final_json}

    class _ResumeGraphBuilder:
        def compile(self, **_kwargs):
            return _ResumeGraph()

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeStatefulCheckpointerContext())
    monkeypatch.setattr("app.agent.graph.build_smart_eats_graph", lambda **_kwargs: _ResumeGraphBuilder())

    state = ChatState(
        session_id="s-resume",
        message="继续",
        resume_from_checkpoint=True,
        resume_payload={"message": "继续上次"},
    )
    events = []

    async for item in run_chat_stream(_FakeRequest(), db=None, redis_client=_FakeRedis(), state=state):
        events.append(item)

    assert isinstance(captured["input_payload"], dict)
    assert captured["input_payload"]["resume_from_checkpoint"] is True
    assert captured["config"]["recursion_limit"] == 64
    assert [item["event"] for item in events].count("final") == 1


@pytest.mark.asyncio
async def test_run_chat_stream_preserves_core_event_contract(monkeypatch):
    async def _noop_save_assistant_message(*_args, **_kwargs):
        return None

    final_json = {
        "recommendations": [{"type": "note", "title": "你好", "reason": "direct"}],
        "followups": [],
        "warnings": [],
    }

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.graph.build_smart_eats_graph",
        lambda **_kwargs: _FakeGraphBuilder([{"session_id": "s-contract", "message": "你好", "final_json": final_json}]),
    )

    state = ChatState(session_id="s-contract", message="你好")
    events = []

    async for item in run_chat_stream(_FakeRequest(), db=None, redis_client=_FakeRedis(), state=state):
        events.append(item)

    event_names = [item["event"] for item in events]
    assert event_names == ["thinking", "thinking", "delta", "final"]
    assert events[0]["data"] == {"status": "start"}
    assert events[1]["data"] == {"status": "done"}
    assert events[-1]["data"]["stopped"] is False
    assert events[-1]["data"]["answer"]["recommendations"][0]["title"] == "你好"


@pytest.mark.asyncio
async def test_run_chat_stream_cancellation_emits_single_stopped_final(monkeypatch):
    async def _noop_save_assistant_message(*_args, **_kwargs):
        return None

    class CancelRedis:
        async def get(self, key):
            return b"1"

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.graph.build_smart_eats_graph",
        lambda **_kwargs: _FakeGraphBuilder([
            {
                "session_id": "s-cancel",
                "final_json": {
                    "recommendations": [{"type": "note", "title": "不应输出", "reason": "cancelled"}],
                    "followups": [],
                    "warnings": [],
                },
            }
        ]),
    )

    events = []
    async for item in run_chat_stream(_FakeRequest(), db=None, redis_client=CancelRedis(), state=ChatState(session_id="s-cancel", message="停下")):
        events.append(item)

    final_events = [item for item in events if item["event"] == "final"]
    assert len(final_events) == 1
    assert final_events[0]["data"] == {"stopped": True}
    assert all(item["event"] != "delta" for item in events)


@pytest.mark.asyncio
async def test_chat_stream_stop(client, monkeypatch):
    resp = await client.post("/api/v1/chat/sessions")
    assert resp.status_code == 200
    session_id = resp.json()["data"]["session_id"]

    async def _slow_plan_tool_calls(self, system, user, available_tools):
        await asyncio.sleep(0.2)
        return {"content": "", "tool_calls": []}

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _slow_plan_tool_calls)

    got_tool_call = False
    got_delta = False
    got_final = False
    stopped_flag = None
    current_event = None
    async def send_stop():
        await asyncio.sleep(0.1)
        stop_resp = await client.post(f"/api/v1/chat/sessions/{session_id}/stop")
        assert stop_resp.status_code == 200

    stop_task = asyncio.create_task(send_stop())

    async with client.stream(
        "POST",
        f"/api/v1/chat/sessions/{session_id}/stream",
        json={"message": "quick dinner"},
    ) as response:
        async for line in response.aiter_lines():
            if line.startswith("event:"):
                current_event = line.split(":", 1)[1].strip()
                continue
            if line.startswith("data:"):
                raw = line.split(":", 1)[1].strip()
                try:
                    payload = json.loads(raw)
                except json.JSONDecodeError:
                    payload = {}

                if current_event == "tool_call":
                    got_tool_call = True
                if current_event == "delta":
                    got_delta = True
                if current_event == "final":
                    got_final = True
                    stopped_flag = payload.get("stopped")
                    break

    await stop_task

    assert got_final
    assert stopped_flag is True


def test_render_final_text_with_recommendations_followups_warnings():
    final_json = {
        "recommendations": [
            {"type": "note", "title": "推荐番茄炒蛋", "reason": "简单快手"},
            {"type": "note", "title": "推荐青椒肉丝"},
        ],
        "followups": ["想要10分钟内完成", "偏清淡口味"],
        "warnings": ["食材过敏请先确认"],
    }

    text = _render_final_text(final_json)

    assert "推荐番茄炒蛋（简单快手）" in text
    assert "推荐青椒肉丝" in text
    assert "**你可以继续：**" in text
    assert "想要10分钟内完成" in text
    assert "偏清淡口味" in text
    assert "**注意：**" in text
    assert "食材过敏请先确认" in text


def test_render_final_text_empty_returns_default():
    text = _render_final_text({"recommendations": [], "followups": [], "warnings": []})

    assert text == "好的。"


def test_best_effort_with_empty_fridge_avoids_fallback():
    state = ChatState(session_id="s1", context={"fridge_items": []})

    final_json = _best_effort_final_from_observations(state, get_smart_eats_agent_config())

    assert final_json["recommendations"][0]["reason"] != "fallback"
    assert "冰箱" in final_json["recommendations"][0]["title"]


def test_best_effort_with_rag_recipe_results_avoids_fallback():
    state = ChatState(
        session_id="s1",
        observations=[
            {
                "tool": "rag_search_recipes",
                "result": {
                    "items": [
                        {"title": "番茄炒蛋", "snippet": "鸡蛋打散，番茄切块，先炒蛋后下番茄翻炒。"},
                        {"title": "青椒土豆丝", "snippet": "土豆切丝泡水，热锅快炒保持脆爽。"},
                    ]
                },
            }
        ],
    )

    final_json = _best_effort_final_from_observations(state, get_smart_eats_agent_config())

    assert final_json["recommendations"][0]["reason"] != "fallback"
    assert final_json["recommendations"][0]["type"] == "recipe"
    assert final_json["recommendations"][0]["title"] == "番茄炒蛋"


def test_best_effort_without_business_signal_falls_back():
    state = ChatState(session_id="s1")

    final_json = _best_effort_final_from_observations(state, get_smart_eats_agent_config())

    assert final_json["recommendations"][0]["reason"] == "fallback"
    assert "抱歉" in final_json["recommendations"][0]["title"]
