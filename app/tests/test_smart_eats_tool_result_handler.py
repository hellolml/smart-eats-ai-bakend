from __future__ import annotations

from app.agent.agents.smart_eats import (
    _tool_result_handler,
    smart_intent_resolver,
    smart_tool_args_normalizer,
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


def test_get_fridge_items_empty_in_cook_home_returns_directive_context():
    state = _build_state()
    state.intent = "cook_home"

    handled = _tool_result_handler(state, "get_fridge_items", {"items": []})

    assert handled is None
    assert state.context_overrides is not None
    assert state.context_overrides.get("fridge_empty") is True
    assert state.context_overrides.get("system_directive") is None


def test_search_restaurants_results_relaxed_to_llm():
    state = _build_state()

    handled = _tool_result_handler(
        state,
        "search_restaurants",
        [{"name": "阿娜尔", "rating": 4.7, "geo": {"lat": 28.2, "lng": 112.9}}],
    )

    assert handled is None


def test_search_restaurants_empty_sets_recovery_context():
    state = _build_state()

    handled = _tool_result_handler(state, "search_restaurants", [])

    assert handled is None
    assert state.context is not None
    assert state.context.get("last_search_error") == "empty_result"
    assert state.context.get("restaurant_retries") == 1
    assert state.context.get("suggested_radius_km") is None
    assert state.context_overrides is not None
    assert state.context_overrides.get("restaurant_search_retries") == 1
    assert state.context_overrides.get("system_directive") is None


def test_search_restaurants_missing_location_sets_recovery_context():
    state = _build_state()

    handled = _tool_result_handler(state, "search_restaurants", {"error": "missing_location"})

    assert handled is None
    assert state.context is not None
    assert state.context.get("last_search_error") == "missing_location"


def test_geocode_location_error_sets_recovery_context():
    state = _build_state()

    handled = _tool_result_handler(state, "geocode_location", {"error": "not_found"})

    assert handled is None
    assert state.context is not None
    assert state.context.get("last_location_error") == "not_found"


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


def test_rag_search_recipes_with_steps_sets_directive_context():
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
    assert state.context_overrides is not None
    hits = state.context_overrides.get("rag_recipe_hits")
    assert isinstance(hits, list) and hits
    assert hits[0].get("title") == "土豆炖牛肉"
    assert state.context_overrides.get("system_directive") is None


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
    assert state.context_overrides is not None
    assert "system_directive" in state.context_overrides
    latest_route = state.context_overrides.get("latest_route")
    assert isinstance(latest_route, dict)
    assert latest_route.get("distance_m") == 1200
    assert latest_route.get("duration_s") == 900
    assert latest_route.get("steps") == ["向东步行", "右转到达"]


def test_rag_search_recipes_empty_clears_recipe_hits_without_directive():
    state = _build_state()
    state.context_overrides = {"rag_recipe_hits": [{"title": "旧菜谱"}], "system_directive": "legacy"}

    handled = _tool_result_handler(state, "rag_search_recipes", {"items": []})

    assert handled is None
    assert state.context_overrides is None


def test_intent_resolver_delegates_to_llm_and_returns_unknown():
    state = ChatState(session_id="s1", message="我想吃麻辣烫", history=[])

    intent = smart_intent_resolver(state)

    assert intent == "unknown"


def test_search_restaurants_args_normalizer_accepts_keyword_and_location():
    args = {"keyword": "麻辣烫", "location": {"lat": 32.52, "lng": 108.53}, "radius": 2000}

    normalized = smart_tool_args_normalizer("search_restaurants", args)

    assert normalized["query"] == "麻辣烫"
    assert normalized["lat"] == 32.52
    assert normalized["lng"] == 108.53
    assert "radius" not in normalized

