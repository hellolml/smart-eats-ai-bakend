from __future__ import annotations

import pytest

from agent_skills.home_chef.hooks import HomeChefHooks
from agent_skills.food_decision.hooks import FoodDecisionHooks
from agent_skills.food_assistant.hooks import FoodAssistantHooks
from agent_skills.restaurant_finder.hooks import RestaurantFinderHooks
from agent_skills.route_planner.hooks import RoutePlannerHooks
from app.agent.runtime.graph import AgentRuntimeState


def test_home_chef_hook_records_empty_fridge_context():
    state = AgentRuntimeState(session_id="s1")

    handled = HomeChefHooks().handle_tool_result(state, "get_fridge_items", {"items": []})

    assert handled is None
    assert state.context["fridge_items"] == []
    assert state.context_overrides == {"fridge_empty": True}


def test_home_chef_hook_records_rag_hits():
    state = AgentRuntimeState(session_id="s1")
    items = [{"title": "番茄炒蛋"}, {"title": "青椒土豆丝"}]

    handled = HomeChefHooks().handle_tool_result(state, "rag_search_recipes", {"items": items})

    assert handled is None
    assert state.context_overrides["rag_recipe_hits"] == items


def test_food_decision_hook_returns_decision_final():
    state = AgentRuntimeState(session_id="s1")
    result = {
        "decision": {"type": "recipe", "title": "番茄炒蛋"},
        "reasons": ["快手", "适合今天"],
        "actions": [{"label": "查看做法", "url": "app://recipe"}],
    }

    handled = FoodDecisionHooks().handle_tool_result(state, "food_decision", result)

    assert handled is not None
    assert handled["recommendations"][0]["title"] == "番茄炒蛋"
    assert handled["decision"] == result


def test_food_assistant_records_home_context():
    state = AgentRuntimeState(session_id="s1", message="冰箱里有鸡蛋，能做什么")

    handled = FoodAssistantHooks().handle_tool_result(state, "get_fridge_items", {"items": []})

    assert handled is None
    assert state.context["food_mode"] == "cook_home"
    assert state.context["fridge_items"] == []
    assert state.context_overrides == {"fridge_empty": True}


def test_food_assistant_records_restaurant_context():
    state = AgentRuntimeState(session_id="s1", message="出去吃")

    handled = FoodAssistantHooks().handle_tool_result(
        state,
        "geocode_location",
        {"lat": 28.2, "lng": 112.9, "city": "长沙", "location_source": "geocode"},
    )

    assert handled is None
    assert state.context["food_mode"] == "eat_out"
    assert state.context["location"] == {"lat": 28.2, "lng": 112.9}
    assert state.context["city"] == "长沙"


def test_food_assistant_blocks_eat_out_food_decision_fallback():
    state = AgentRuntimeState(session_id="s1", message="出去吃")
    state.context = {"food_mode": "eat_out"}
    result = {
        "decision": {"type": "fallback", "title": "黄焖鸡米饭"},
        "reasons": ["兜底"],
        "actions": [],
    }

    handled = FoodAssistantHooks().handle_tool_result(state, "food_decision", result)

    assert handled is not None
    assert "黄焖鸡米饭" not in handled["recommendations"][0]["title"]
    assert "餐厅" in handled["recommendations"][0]["title"]


def test_food_assistant_allows_decide_food_result():
    state = AgentRuntimeState(session_id="s1", message="今天吃点啥")
    state.context = {"food_mode": "decide_food"}
    result = {
        "decision": {"type": "recipe", "title": "番茄炒蛋"},
        "reasons": ["快手"],
        "actions": [],
    }

    handled = FoodAssistantHooks().handle_tool_result(state, "food_decision", result)

    assert handled is not None
    assert handled["recommendations"][0]["title"] == "番茄炒蛋"


def test_restaurant_hook_normalizes_search_args():
    args = {"keyword": "火锅", "location": {"lat": 28.2, "lng": 112.9}, "radius": 3000}

    normalized = RestaurantFinderHooks().normalize_tool_args(
        AgentRuntimeState(session_id="s1"),
        "search_restaurants",
        args,
    )

    assert normalized == {"query": "火锅", "lat": 28.2, "lng": 112.9}


def test_restaurant_hook_handles_location_success():
    state = AgentRuntimeState(session_id="s1")

    handled = RestaurantFinderHooks().handle_tool_result(
        state,
        "geocode_location",
        {"lat": 28.2, "lng": 112.9, "city": "长沙", "location_source": "geocode"},
    )

    assert handled is None
    assert state.context["location"] == {"lat": 28.2, "lng": 112.9}
    assert state.context["city"] == "长沙"
    assert state.context["location_source"] == "geocode"
    assert state.context["task_stage"] == "location_ready"


def test_restaurant_hook_tracks_empty_search_retry():
    state = AgentRuntimeState(session_id="s1", context={})

    handled = RestaurantFinderHooks().handle_tool_result(state, "search_restaurants", [])

    assert handled is None
    assert state.context["restaurant_retries"] == 1
    assert state.context["last_search_error"] == "empty_result"
    assert state.context_overrides["restaurant_search_retries"] == 1


@pytest.mark.asyncio
async def test_route_hook_builds_target_candidate_from_prior_candidates():
    state = AgentRuntimeState(session_id="s1", message="就去山城火锅，怎么走")
    context = {
        "last_restaurants": [
            {"name": "山城火锅", "geo": {"lat": 28.2, "lng": 112.9}},
            {"name": "另一家", "geo": {"lat": 28.3, "lng": 112.8}},
        ]
    }

    extra = await RoutePlannerHooks().build_context(state, context)

    assert extra["route_target_candidate"] == {
        "name": "山城火锅",
        "geo": {"lat": 28.2, "lng": 112.9},
    }


def test_route_hook_returns_missing_origin_final():
    state = AgentRuntimeState(session_id="s1")

    handled = RoutePlannerHooks().handle_tool_result(state, "plan_route", {"error": "missing_origin"})

    assert handled is not None
    assert "出发位置" in handled["recommendations"][0]["title"]


def test_route_hook_records_latest_route_directive():
    state = AgentRuntimeState(session_id="s1")
    result = {"distance_m": 1200, "duration_s": 600, "steps": [{"instruction": "步行"}]}

    handled = RoutePlannerHooks().handle_tool_result(state, "plan_route", result)

    assert handled is None
    assert state.context_overrides["latest_route"]["distance_m"] == 1200
    assert "submit_final_answer" in state.context_overrides["system_directive"]
