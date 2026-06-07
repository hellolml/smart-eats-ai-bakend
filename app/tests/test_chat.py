import asyncio
import json

import pytest
from langchain_core.messages import AIMessage

from app.agent.graph import _render_final_text, _resolve_graph_input, run_chat_stream
from app.agent.runtime.graph import _best_effort_final_from_observations, get_agent_runtime_config
from app.agent.state import ChatState


class _FakeRequest:
    async def is_disconnected(self):
        return False


class _FakeRedis:
    async def get(self, key):
        return None


class _MemoryRedis:
    def __init__(self):
        self.values = {}

    async def get(self, key):
        return self.values.get(key)

    async def setex(self, key, _ttl, value):
        self.values[key] = value


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


class _SlowFakeCompiledGraph:
    async def astream(self, *_args, **_kwargs):
        await asyncio.sleep(0.2)
        yield {
            "session_id": "s-slow",
            "message": "quick dinner",
            "final_json": {
                "recommendations": [{"type": "note", "title": "slow final", "reason": "test"}],
                "followups": [],
                "warnings": [],
            },
        }


class _SlowFakeGraphBuilder:
    def compile(self, **_kwargs):
        return _SlowFakeCompiledGraph()


class _FailingCompiledGraph:
    async def astream(self, *_args, **_kwargs):
        raise RuntimeError("provider unavailable")
        yield  # pragma: no cover


class _FailingGraphBuilder:
    def compile(self, **_kwargs):
        return _FailingCompiledGraph()


class _FailingAfterSnapshotCompiledGraph:
    async def astream(self, *_args, **_kwargs):
        yield {
            "session_id": "s-upstream",
            "message": "今天吃什么",
            "route_decision": {
                "worker": "food_advisor",
                "intent": "eat_out",
                "confidence": 0.9,
                "reason": "food_intent",
            },
        }
        exc = RuntimeError("provider unavailable")
        exc.agent_worker_latest_state = {
            "session_id": "s-upstream",
            "message": "今天吃什么",
            "context": {
                "allowed_tools": ["get_ip_location", "geocode_location", "search_restaurants", "get_weather"],
                "active_skills": [{"id": "food_assistant"}, {"id": "restaurant_finder"}],
                "skill_diagnostics": {"max_tool_calls_per_turn": 4},
            },
        }
        raise exc


class _FailingAfterSnapshotGraphBuilder:
    def compile(self, **_kwargs):
        return _FailingAfterSnapshotCompiledGraph()


class _FakeStatefulCheckpointerContext:
    async def __aenter__(self):
        return object()

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _PendingSnapshot:
    next = ("worker",)
    values = {"session_id": "stale"}


def test_resolve_graph_input_ignores_pending_checkpoint_without_explicit_resume():
    state = ChatState(session_id="s-new-turn", message="从酒店怎么去浅草寺")

    resolved = _resolve_graph_input(state, _PendingSnapshot(), checkpointer=object())

    assert isinstance(resolved, dict)
    assert resolved["session_id"] == "s-new-turn"
    assert resolved["message"] == "从酒店怎么去浅草寺"


@pytest.mark.asyncio
async def test_travel_scene_uses_supervisor_runtime_and_waits_for_confirmation(monkeypatch):
    async def _noop_save_assistant_message(*_args, **_kwargs):
        return None

    final_json = {
        "state": "candidates_ready",
        "await_confirmation": True,
        "candidates": [
            {
                "candidate_id": "candidate_001",
                "name": "西湖",
                "poi": {"poi_id": "B001", "longitude": 120.1, "latitude": 30.2},
            }
        ],
        "itinerary": {"days": []},
        "map": {"qr_code_url": None, "schema_url": None},
        "recommendations": [{"title": "已验证 1 个候选地点", "reason": "等待确认"}],
        "followups": [],
        "warnings": [],
    }

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.supervisor.build_supervisor_runtime_graph",
        lambda **_kwargs: _FakeGraphBuilder([{"session_id": "s-travel", "message": "目的地：杭州\n西湖\n灵隐寺", "final_json": final_json}]),
    )

    events = [
        item
        async for item in run_chat_stream(
            _FakeRequest(),
            db=None,
            redis_client=_MemoryRedis(),
            state=ChatState(session_id="s-travel", scene="travel_planner", message="目的地：杭州\n西湖\n灵隐寺"),
        )
    ]

    final = [item for item in events if item["event"] == "final"][-1]["data"]["answer"]
    assert final["state"] == "candidates_ready"
    assert final["await_confirmation"] is True
    assert final["candidates"]
    assert final["candidates"][0]["poi"]["poi_id"]


@pytest.mark.asyncio
async def test_travel_confirmation_generates_itinerary_and_map_in_supervisor_runtime(monkeypatch):
    async def _noop_save_assistant_message(*_args, **_kwargs):
        return None

    final_json = {
        "state": "map_generated",
        "await_confirmation": False,
        "itinerary": {"days": [{"day_number": 1, "items": [{"place_name": "西湖"}]}]},
        "map": {"qr_code_url": "https://example.com/qr.png", "schema_url": "amapuri://travel"},
        "recommendations": [{"title": "杭州行程已生成", "reason": "地图已生成"}],
        "followups": [],
        "warnings": [],
    }

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.supervisor.build_supervisor_runtime_graph",
        lambda **_kwargs: _FakeGraphBuilder([{"session_id": "s-travel", "message": "确认生成", "final_json": final_json}]),
    )

    events = [
        item
        async for item in run_chat_stream(
            _FakeRequest(),
            db=None,
            redis_client=_MemoryRedis(),
            state=ChatState(
                session_id="s-travel",
                scene="travel_planner",
                message="确认生成",
                context_overrides={"travel_action": "confirm_candidates"},
            ),
        )
    ]

    final = [item for item in events if item["event"] == "final"][-1]["data"]["answer"]
    assert final["state"] == "map_generated"
    assert final["itinerary"]["days"]
    assert final["map"]["qr_code_url"] == "https://example.com/qr.png"


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
    monkeypatch.setattr("app.agent.supervisor.build_supervisor_runtime_graph", lambda **_kwargs: _ResumeGraphBuilder())

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
        "app.agent.supervisor.build_supervisor_runtime_graph",
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
async def test_run_chat_stream_supervisor_runtime_ignores_message_final_json(monkeypatch):
    async def _noop_save_assistant_message(*_args, **_kwargs):
        return None

    state_final_json = {
        "recommendations": [{"type": "note", "title": "state 回答", "reason": "supervisor"}],
        "followups": [],
        "warnings": [],
    }
    message_final_json = {
        "recommendations": [{"type": "note", "title": "message 回答", "reason": "legacy_channel"}],
        "followups": [],
        "warnings": [],
    }

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.supervisor.build_supervisor_runtime_graph",
        lambda **_kwargs: _FakeGraphBuilder(
            [
                {
                    "session_id": "s-supervisor",
                    "message": "今天吃什么",
                    "messages": [
                        AIMessage(
                            content="主管回答",
                            additional_kwargs={"final_json": message_final_json},
                        )
                    ],
                    "final_json": state_final_json,
                    "events": [{"event": "tool_call", "data": {"name": "food_advisor"}}],
                }
            ]
        ),
    )

    events = []
    async for item in run_chat_stream(
        _FakeRequest(),
        db=None,
        redis_client=_FakeRedis(),
        state=ChatState(session_id="s-supervisor", message="今天吃什么"),
    ):
        events.append(item)

    assert [item["event"] for item in events] == ["thinking", "tool_call", "thinking", "delta", "final"]
    assert events[-1]["data"]["answer"]["recommendations"][0]["title"] == "state 回答"


@pytest.mark.asyncio
async def test_run_chat_stream_supervisor_runtime_uses_direct_ai_text_without_state_final_json(monkeypatch):
    async def _noop_save_assistant_message(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop_save_assistant_message)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr(
        "app.agent.supervisor.build_supervisor_runtime_graph",
        lambda **_kwargs: _FakeGraphBuilder(
            [
                {
                    "session_id": "s-supervisor-direct",
                    "message": "你好",
                    "messages": [AIMessage(content="你好，我在。")],
                }
            ]
        ),
    )

    events = [
        item
        async for item in run_chat_stream(
            _FakeRequest(),
            db=None,
            redis_client=_FakeRedis(),
            state=ChatState(session_id="s-supervisor-direct", message="你好"),
        )
    ]

    assert events[-1]["data"]["answer"]["recommendations"][0]["title"] == "你好，我在。"
    assert events[-1]["data"]["answer"]["recommendations"][0].get("reason") != "fallback"
    assert events[-1]["data"]["agent_result"]["status"] == "completed"


@pytest.mark.asyncio
async def test_run_chat_stream_converts_upstream_exception_to_failed_final(monkeypatch):
    saved = []
    scheduled = []

    async def _save_assistant_message(*_args, **kwargs):
        saved.append(kwargs)

    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _save_assistant_message)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr("app.agent.graph._schedule_realtime_eval", lambda **kwargs: scheduled.append(kwargs))
    monkeypatch.setattr("app.agent.supervisor.build_supervisor_runtime_graph", lambda **_kwargs: _FailingGraphBuilder())

    events = [
        item
        async for item in run_chat_stream(
            _FakeRequest(),
            db=None,
            redis_client=_FakeRedis(),
            state=ChatState(session_id="s-upstream", message="今天吃什么", trace_id="trace-upstream"),
        )
    ]

    assert [item["event"] for item in events] == ["thinking", "error", "delta", "final"]
    error_data = events[1]["data"]
    final_data = events[-1]["data"]
    assert error_data["failure_class"] == "upstream_error"
    assert error_data["provider_issue"]["code"] == "provider_upstream_error"
    assert final_data["trace_id"] == "trace-upstream"
    assert final_data["failure_class"] == "upstream_error"
    assert final_data["provider_issue"]["code"] == "provider_upstream_error"
    assert final_data["agent_result"]["status"] == "failed"
    assert final_data["agent_result"]["failure_class"] == "upstream_error"
    assert final_data["agent_result"]["final"]["failure_class"] == "upstream_error"
    assert final_data["agent_result"]["diagnostics"]["provider_issue"]["code"] == "provider_upstream_error"
    assert saved[-1]["failure_class"] == "upstream_error"
    assert saved[-1]["agent_result"]["status"] == "failed"
    assert saved[-1]["agent_result"]["diagnostics"]["provider_issue"]["code"] == "provider_upstream_error"
    assert scheduled[-1]["events"][-1]["event"] == "final"


@pytest.mark.asyncio
async def test_run_chat_stream_preserves_runtime_diagnostics_on_upstream_exception(monkeypatch):
    async def _noop(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent.graph.conversation.save_assistant_message", _noop)
    monkeypatch.setattr("app.agent.graph._apply_turn_preference_extraction", _noop)
    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr("app.agent.graph._schedule_realtime_eval", lambda **_kwargs: None)
    monkeypatch.setattr("app.agent.supervisor.build_supervisor_runtime_graph", lambda **_kwargs: _FailingAfterSnapshotGraphBuilder())

    events = [
        item
        async for item in run_chat_stream(
            _FakeRequest(),
            db=None,
            redis_client=_FakeRedis(),
            state=ChatState(session_id="s-upstream", message="今天吃什么", trace_id="trace-upstream"),
        )
    ]

    diagnostics = events[-1]["data"]["agent_result"]["diagnostics"]
    assert diagnostics["route"]["worker"] == "food_advisor"
    assert diagnostics["active_tools"] == ["get_ip_location", "geocode_location", "search_restaurants", "get_weather"]
    assert [item["id"] for item in diagnostics["active_skills"]] == ["food_assistant", "restaurant_finder"]
    assert diagnostics["skill_diagnostics"]["max_tool_calls_per_turn"] == 4


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
        "app.agent.supervisor.build_supervisor_runtime_graph",
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

    monkeypatch.setattr("app.agent.graph.checkpointer_context", lambda: _FakeCheckpointerContext())
    monkeypatch.setattr("app.agent.supervisor.build_supervisor_runtime_graph", lambda **_kwargs: _SlowFakeGraphBuilder())

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
    state = ChatState(
        session_id="s1",
        context={"active_skills": [{"id": "food_assistant"}], "food_mode": "cook_home", "fridge_items": []},
    )

    final_json = _best_effort_final_from_observations(state, get_agent_runtime_config())

    assert final_json["recommendations"][0]["reason"] != "fallback"
    assert "冰箱" in final_json["recommendations"][0]["title"]


def test_best_effort_with_rag_recipe_results_avoids_fallback():
    state = ChatState(
        session_id="s1",
        context={"active_skills": [{"id": "food_assistant"}], "food_mode": "cook_home"},
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

    final_json = _best_effort_final_from_observations(state, get_agent_runtime_config())

    assert final_json["recommendations"][0]["reason"] != "fallback"
    assert final_json["recommendations"][0]["type"] == "recipe"
    assert final_json["recommendations"][0]["title"] == "番茄炒蛋"


def test_best_effort_without_business_signal_falls_back():
    state = ChatState(session_id="s1")

    final_json = _best_effort_final_from_observations(state, get_agent_runtime_config())

    assert final_json["recommendations"][0]["reason"] == "fallback"
    assert "抱歉" in final_json["recommendations"][0]["title"]
