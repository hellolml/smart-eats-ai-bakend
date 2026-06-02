from __future__ import annotations

import pytest

from agent_skills.home_chef.hooks import HomeChefHooks
from agent_skills.food_decision.hooks import FoodDecisionHooks
from agent_skills.food_assistant.hooks import FoodAssistantHooks
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


def test_travel_plan_new_verifies_all_extracted_places_before_candidate_confirmation():
    content = """
**识别到的地点：**
1. **大熊猫繁育研究基地** - 成都必去的熊猫基地
2. **武侯祠** - 三国文化景点
3. **锦里** - 与武侯祠相邻的古街

**美食推荐：**
- 火锅、串串、龙抄手
"""
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        message="成都3天",
        skill_state={"last_ai_message_content": content},
    )
    state.context_overrides = {"travel_payload": {"destination": "成都"}}
    hook = TravelPlanNewHooks()
    state.observations.extend(
        [
            {
                "tool": "travel_search_poi",
                "result": {
                    "query": {"keywords": "大熊猫繁育研究基地", "city": "成都", "category": "attraction"},
                    "selected_poi": {
                        "poi_id": "PANDA",
                        "name": "成都大熊猫繁育研究基地",
                        "address": "熊猫大道1375号",
                        "longitude": 104.145,
                        "latitude": 30.738,
                    },
                    "pois": [],
                },
            },
            {
                "tool": "travel_search_poi",
                "result": {
                    "query": {"keywords": "武侯祠", "city": "成都", "category": "attraction"},
                    "match_status": "only_transport_affix",
                    "rejected_pois": [{"name": "武侯祠(地铁站)"}],
                    "pois": [],
                },
            },
        ]
    )

    handled = hook.handle_tool_result(state, "travel_search_poi", state.observations[-1]["result"])

    assert handled is None
    calls = hook.forced_tool_calls(state)
    assert calls and calls[0]["args"]["keywords"] == "锦里"

    state.observations.append(
        {
            "tool": "travel_search_poi",
            "result": {
                "query": {"keywords": "锦里", "city": "成都", "category": "attraction"},
                "selected_poi": {
                    "poi_id": "JINLI",
                    "name": "锦里古街",
                    "address": "武侯祠大街231号",
                    "longitude": 104.047,
                    "latitude": 30.644,
                },
                "pois": [],
            },
        }
    )

    handled = hook.handle_tool_result(state, "travel_search_poi", state.observations[-1]["result"])

    assert handled is not None
    assert handled["state"] == "candidates_ready"
    assert [item["source_name"] for item in handled["candidates"]] == ["大熊猫繁育研究基地", "锦里"]
    assert handled["failed_places"][0]["source_name"] == "武侯祠"
    assert handled["candidate_groups"]["failed"][0]["reason"] == "只匹配到地铁站、公交站、停车场或出入口，不是攻略地点本体"
    assert [item["name"] for item in handled["food_items"]] == ["火锅", "串串", "龙抄手"]


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


def test_travel_plan_new_tracks_added_and_removed_candidates():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_action": "add_candidates",
            "travel_payload": {
                "candidates": [
                    {
                        "candidate_id": "candidate_001",
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "longitude": 120.148, "latitude": 30.242},
                    },
                    {
                        "candidate_id": "candidate_002",
                        "name": "断桥",
                        "poi": {"poi_id": "B002", "longitude": 120.147, "latitude": 30.257},
                    },
                ],
                "removed_places": ["断桥"],
                "user_added_places": [{"name": "知味观湖滨店", "category": "restaurant"}],
            },
        },
    )

    context = TravelPlanNewHooks().build_context(state, {})

    names = [item["name"] for item in context["travel_state"]["candidates"]]
    assert names == ["西湖", "知味观湖滨店"]
    assert context["travel_state"]["user_added_places"][0]["name"] == "知味观湖滨店"
    assert context["travel_state"]["excluded_places"] == ["断桥"]


def test_travel_plan_new_warns_for_generic_food_addition():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_action": "add_candidates",
            "travel_payload": {"user_added_places": [{"name": "火锅", "category": "restaurant"}]},
        },
    )

    context = TravelPlanNewHooks().build_context(state, {})

    assert "具体店名" in context["system_directive"]


def test_travel_plan_new_filters_map_tool_until_itinerary_confirmation():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_payload": {
                "itinerary": {"days": [{"day_number": 1, "items": [{"place_name": "西湖"}]}]},
                "candidates": [
                    {
                        "candidate_id": "candidate_001",
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "longitude": 120.148, "latitude": 30.242},
                    }
                ],
            }
        },
    )

    allowed = TravelPlanNewHooks().filter_allowed_tools(
        state,
        ["travel_search_poi", "travel_create_personal_map"],
    )

    assert allowed == ["travel_search_poi"]


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


def test_travel_plan_new_line_list_uses_verified_pois_and_connects_days():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_action": "generate_map",
            "travel_payload": {
                "trip_meta": {"destination": "杭州", "days": 2},
                "candidates": [
                    {
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "name": "西湖", "longitude": 120.148, "latitude": 30.242},
                    },
                    {
                        "name": "灵隐寺",
                        "poi": {"poi_id": "B002", "name": "灵隐寺", "longitude": 120.101, "latitude": 30.240},
                    },
                    {"name": "未验证地点"},
                ],
                "itinerary": {
                    "days": [
                        {"day_number": 1, "items": [{"place_name": "西湖"}]},
                        {"day_number": 2, "items": [{"place_name": "灵隐寺"}, {"place_name": "未验证地点"}]},
                    ]
                },
            },
        },
    )

    calls = TravelPlanNewHooks().forced_tool_calls(state)
    line_list = calls[0]["args"]["line_list"]

    assert [point["poiId"] for point in line_list[0]["pointInfoList"]] == ["B001"]
    assert line_list[1]["pointInfoList"][0]["poiId"] == "B001"
    assert all(point["poiId"] != "" for line in line_list for point in line["pointInfoList"])


def test_travel_plan_new_hook_enables_vision_for_attachments():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context={"attachments": [{"kind": "image", "filename": "guide.png"}]},
    )

    assert TravelPlanNewHooks().should_build_vision_input(state) is True
