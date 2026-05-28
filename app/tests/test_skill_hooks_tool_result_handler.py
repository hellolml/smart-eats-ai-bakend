from __future__ import annotations

import pytest

from agent_skills.home_chef.hooks import HomeChefHooks
from agent_skills.food_decision.hooks import FoodDecisionHooks
from agent_skills.restaurant_finder.hooks import RestaurantFinderHooks
from agent_skills.route_planner.hooks import RoutePlannerHooks
from agent_skills.travel_plan_new.hooks import TravelPlanNewHooks
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


def test_food_decision_hook_normalizes_location_context():
    state = AgentRuntimeState(
        session_id="s1",
        message="附近有什么吃的",
        context={
            "environment": {
                "location": {"lat": 31.23, "lng": 121.47},
            },
            "city": "上海",
        },
    )

    normalized = FoodDecisionHooks().normalize_tool_args(state, "food_decision", {})

    assert normalized["query"] == "附近有什么吃的"
    assert normalized["lat"] == 31.23
    assert normalized["lng"] == 121.47
    assert normalized["city"] == "上海"
    assert normalized["scene"] == "eat"


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


def test_travel_plan_new_hook_stops_at_candidate_confirmation():
    state = AgentRuntimeState(session_id="s1", scene="travel_planner", message="杭州3天")
    state.observations.append(
        {
            "tool": "travel_search_poi",
            "result": {
                "query": {"keywords": "西湖", "city": "杭州"},
                "pois": [
                    {
                        "poi_id": "B001",
                        "name": "西湖风景名胜区",
                        "address": "杭州市西湖区",
                        "longitude": 120.148,
                        "latitude": 30.242,
                    }
                ],
            },
        }
    )

    handled = TravelPlanNewHooks().handle_tool_result(state, "travel_search_poi", state.observations[0]["result"])

    assert handled is not None
    assert handled["state"] == "candidates_ready"
    assert handled["await_confirmation"] is True
    assert handled["candidates"][0]["poi"]["poi_id"] == "B001"
    assert handled["itinerary"]["days"] == []


def test_travel_plan_new_hook_returns_map_final():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_action": "confirm_candidates",
            "travel_payload": {
                "candidates": [
                    {
                        "candidate_id": "candidate_001",
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "longitude": 120.148, "latitude": 30.242},
                    }
                ]
            },
        },
    )
    result = {
        "title": "杭州3天地图",
        "qr_code_url": "https://example.com/qr.png",
        "schema_url": "amapuri://foo",
        "line_list": [
            {
                "title": "Day 1",
                "pointInfoList": [
                    {"name": "西湖", "poiId": "B001", "lon": 120.148, "lat": 30.242}
                ],
            }
        ],
    }

    handled = TravelPlanNewHooks().handle_tool_result(state, "travel_create_personal_map", result)

    assert handled is not None
    assert handled["state"] == "map_generated"
    assert handled["map"]["qr_code_url"] == "https://example.com/qr.png"
    assert handled["candidates"][0]["poi"]["poi_id"] == "B001"
    assert handled["itinerary"]["days"][0]["items"][0]["place_name"] == "西湖"


def test_travel_plan_new_confirm_candidates_waits_for_itinerary_confirmation():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_action": "confirm_candidates",
            "travel_payload": {
                "candidates": [
                    {
                        "candidate_id": "candidate_001",
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "longitude": 120.148, "latitude": 30.242},
                    }
                ]
            },
        },
    )

    context = TravelPlanNewHooks().build_context(state, {})

    assert context["travel_state"]["phase"] == "candidates_confirmed"
    assert "禁止调用 travel_create_personal_map" in context["system_directive"]


def test_travel_plan_new_forces_map_tool_after_itinerary_confirmation():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_action": "generate_map",
            "travel_payload": {
                "trip_meta": {"destination": "杭州", "days": 1},
                "candidates": [
                    {
                        "candidate_id": "candidate_001",
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "name": "西湖", "longitude": 120.148, "latitude": 30.242},
                    }
                ],
                "itinerary": {
                    "days": [
                        {
                            "day_number": 1,
                            "theme": "西湖经典线",
                            "items": [{"place_name": "西湖"}],
                        }
                    ]
                },
            },
        },
    )

    calls = TravelPlanNewHooks().forced_tool_calls(state)

    assert calls is not None
    assert calls[0]["name"] == "travel_create_personal_map"
    assert calls[0]["args"]["line_list"][0]["pointInfoList"][0]["poiId"] == "B001"


def test_travel_plan_new_hook_enables_vision_for_attachments():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context={"attachments": [{"kind": "image", "filename": "guide.png"}]},
    )

    assert TravelPlanNewHooks().should_build_vision_input(state) is True
