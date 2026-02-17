from __future__ import annotations

from app.agent.agents.smart_eats import _tool_result_handler
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


def test_search_restaurants_empty_relaxed_to_llm():
    state = _build_state()

    handled = _tool_result_handler(state, "search_restaurants", [])

    assert handled is None


def test_search_restaurants_missing_location_keeps_hard_guardrail():
    state = _build_state()

    handled = _tool_result_handler(state, "search_restaurants", {"error": "missing_location"})

    assert isinstance(handled, dict)
    assert handled["recommendations"][0]["type"] == "note"


def test_geocode_location_error_keeps_hard_guardrail():
    state = _build_state()

    handled = _tool_result_handler(state, "geocode_location", {"error": "not_found"})

    assert isinstance(handled, dict)
    assert handled["recommendations"][0]["title"] == "需要你的具体位置，才能推荐附近餐厅。"


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
