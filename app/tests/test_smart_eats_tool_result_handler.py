from __future__ import annotations

from app.agent.agents.smart_eats import (
    _tool_result_handler,
    smart_fast_path_decider,
    smart_fast_path_system_prompt_builder,
    smart_fast_path_writer_prompt_builder,
    smart_tool_plan_router,
)
from app.agent.state import ChatState


def _build_state() -> ChatState:
    return ChatState(session_id="s1", user_id="u1", message="test", context={})


def test_get_fridge_items_empty_relaxed_to_llm():
    state = _build_state()

    handled = _tool_result_handler(state, "get_fridge_items", {"items": []})

    assert handled is None
    assert state.context is not None
    assert state.context.get("fridge_items") == []


def test_search_restaurants_results_relaxed_to_llm():
    state = _build_state()

    handled = _tool_result_handler(
        state,
        "search_restaurants",
        [{"name": "阿娜尔", "rating": 4.7, "geo": {"lat": 28.2, "lng": 112.9}}],
    )

    assert handled is None


def test_search_restaurants_empty_returns_guidance():
    state = _build_state()

    handled = _tool_result_handler(state, "search_restaurants", [])

    assert isinstance(handled, dict)
    assert "附近暂时没刷到合适的餐厅" in handled["recommendations"][0]["title"]


def test_search_restaurants_missing_location_keeps_hard_guardrail():
    state = _build_state()

    handled = _tool_result_handler(state, "search_restaurants", {"error": "missing_location"})

    assert isinstance(handled, dict)
    assert handled["recommendations"][0]["type"] == "note"


def test_geocode_location_error_keeps_hard_guardrail():
    state = _build_state()

    handled = _tool_result_handler(state, "geocode_location", {"error": "not_found"})

    assert isinstance(handled, dict)
    assert "自动定位" in handled["recommendations"][0]["title"]


def test_geocode_location_success_updates_context_and_relaxes():
    state = _build_state()

    handled = _tool_result_handler(
        state,
        "geocode_location",
        {"lat": 28.2, "lng": 112.9, "city": "长沙", "location_source": "geocode"},
    )

    assert handled is None
    assert state.context is not None
    assert state.context.get("location") == {"lat": 28.2, "lng": 112.9}
    assert state.context.get("city") == "长沙"
    assert state.location_source == "geocode"


def test_search_recipes_relaxed_to_llm():
    state = _build_state()

    handled = _tool_result_handler(
        state,
        "search_recipes",
        [{"title": "番茄炒蛋", "time": 10}],
    )

    assert handled is None


def test_rag_search_recipes_with_steps_relaxed_to_llm():
    state = _build_state()
    state.intent = "confirm_recipe"

    handled = _tool_result_handler(
        state,
        "rag_search_recipes",
        {
            "items": [
                {
                    "title": "土豆炖牛肉",
                    "metadata": {
                        "steps": ["牛肉焯水", "小火炖煮"],
                        "ingredients": ["牛肉", "土豆"],
                    },
                }
            ]
        },
    )

    assert handled is None


def test_plan_route_missing_origin_keeps_hard_guardrail():
    state = _build_state()

    handled = _tool_result_handler(state, "plan_route", {"error": "missing_origin"})

    assert isinstance(handled, dict)
    assert handled["recommendations"][0]["title"] == "还需要你的出发位置，才能规划路线。"


def test_plan_route_missing_destination_keeps_hard_guardrail():
    state = _build_state()

    handled = _tool_result_handler(state, "plan_route", {"error": "missing_destination"})

    assert isinstance(handled, dict)
    assert handled["recommendations"][0]["title"] == "还需要你的目的地，才能规划路线。"


def test_plan_route_error_keeps_hard_guardrail():
    state = _build_state()

    handled = _tool_result_handler(state, "plan_route", {"error": "upstream_failed"})

    assert isinstance(handled, dict)
    assert handled["recommendations"][0]["title"] == "路线规划失败"


def test_plan_route_success_relaxed_to_llm():
    state = _build_state()

    handled = _tool_result_handler(
        state,
        "plan_route",
        {
            "mode": "walking",
            "distance_m": 1200,
            "duration_s": 900,
            "steps": ["向东步行", "右转到达"],
        },
    )

    assert handled is None


def test_fast_path_decider_for_simple_chat():
    state = ChatState(session_id="s1", message="你好", scene="chat")

    assert smart_fast_path_decider(state) is True


def test_fast_path_decider_rejects_tool_intent_keyword():
    state = ChatState(session_id="s1", message="附近吃什么", scene="chat")

    assert smart_fast_path_decider(state) is False


def test_fast_path_decider_rejects_context_overrides():
    state = ChatState(
        session_id="s1",
        message="你好",
        scene="chat",
        context_overrides={"environment": {"location": {"lat": 1, "lng": 2}}},
    )

    assert smart_fast_path_decider(state) is False


def test_fast_path_decider_rejects_checkpoint_resume():
    state = ChatState(
        session_id="s1",
        message="你好",
        scene="chat",
        resume_from_checkpoint=True,
    )

    assert smart_fast_path_decider(state) is False


def test_fast_path_system_prompt_builder_contains_constraints():
    state = ChatState(
        session_id="s1",
        message="hi",
        context={"system_prompt": "来自上下文的system"},
    )

    prompt = smart_fast_path_system_prompt_builder(state)
    assert "Fast Path 输出约束" in prompt
    assert "禁止输出 JSON" in prompt


def test_fast_path_writer_prompt_builder_contains_recent_history_and_message():
    state = ChatState(
        session_id="s1",
        message="继续",
        history=[
            {"role": "user", "content": "你好"},
            {"role": "assistant", "content": "你好呀"},
        ],
    )

    prompt = smart_fast_path_writer_prompt_builder(state)

    assert "对话历史：" in prompt
    assert "用户: 你好" in prompt
    assert "助手: 你好呀" in prompt
    assert "用户最新消息: 继续" in prompt
    assert "使用中文回答" in prompt


def test_tool_plan_router_eat_out_without_location_calls_get_ip_location():
    state = ChatState(session_id="s1", message="出去吃", intent="eat_out", context={})

    plan = smart_tool_plan_router(state)

    assert plan == [{"name": "get_ip_location", "args": {}}]


def test_tool_plan_router_eat_out_with_context_location_calls_search_restaurants():
    state = ChatState(
        session_id="s1",
        message="出去吃",
        intent="eat_out",
        context={"environment": {"location": {"lat": 31.2304, "lng": 121.4737}}},
    )

    plan = smart_tool_plan_router(state)

    assert isinstance(plan, list)
    assert len(plan) == 1
    assert plan[0]["name"] == "search_restaurants"
    assert plan[0]["args"]["query"] == "美食"
    assert plan[0]["args"]["lat"] == 31.2304
    assert plan[0]["args"]["lng"] == 121.4737


def test_tool_plan_router_eat_out_with_observation_location_calls_search_restaurants():
    state = ChatState(
        session_id="s1",
        message="出去吃",
        intent="eat_out",
        context={},
        observations=[{"tool": "get_ip_location", "result": {"lat": 30.67, "lng": 104.06}}],
    )

    plan = smart_tool_plan_router(state)

    assert isinstance(plan, list)
    assert len(plan) == 1
    assert plan[0]["name"] == "search_restaurants"
    assert plan[0]["args"]["lat"] == 30.67
    assert plan[0]["args"]["lng"] == 104.06


def test_tool_plan_router_non_eat_out_returns_none():
    state = ChatState(session_id="s1", message="你好", intent="chat", context={})

    plan = smart_tool_plan_router(state)

    assert plan is None


def test_tool_plan_router_explicit_location_calls_geocode_first():
    state = ChatState(
        session_id="s1",
        message="我在紫阳县紫阳广场",
        intent="eat_out",
        context={"environment": {"location": {"lat": 32.31, "lng": 108.38}}},
        history=[{"role": "user", "content": "出去吃"}],
    )

    plan = smart_tool_plan_router(state)

    assert plan == [{"name": "geocode_location", "args": {"query": "我在紫阳县紫阳广场"}}]
    assert state.task_stage == "need_geocode"


def test_tool_plan_router_after_geocode_uses_geocoded_coords_for_search():
    state = ChatState(
        session_id="s1",
        message="我在紫阳县紫阳广场",
        intent="eat_out",
        context={"environment": {"location": {"lat": 32.31, "lng": 108.38}}},
        observations=[{"tool": "geocode_location", "result": {"lat": 32.521417, "lng": 108.532332}}],
    )

    plan = smart_tool_plan_router(state)

    assert plan is not None
    assert plan[0]["name"] == "search_restaurants"
    assert plan[0]["args"]["lat"] == 32.521417
    assert plan[0]["args"]["lng"] == 108.532332


def test_tool_plan_router_after_search_delegates_to_llm():
    state = ChatState(
        session_id="s1",
        message="我在紫阳县紫阳广场",
        intent="eat_out",
        observations=[
            {"tool": "geocode_location", "result": {"lat": 32.521417, "lng": 108.532332}},
            {"tool": "search_restaurants", "result": [{"name": "迈德思客"}]},
        ],
    )

    plan = smart_tool_plan_router(state)

    assert plan is None
    assert state.task_stage == "searched"
