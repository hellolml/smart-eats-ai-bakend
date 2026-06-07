from __future__ import annotations

import pytest

from agent_skills.home_chef.hooks import HomeChefHooks
from agent_skills.food_decision.hooks import FoodDecisionHooks
from agent_skills.food_assistant.hooks import FoodAssistantHooks
from agent_skills.restaurant_finder.hooks import RestaurantFinderHooks
from agent_skills.route_planner.hooks import RoutePlannerHooks
from agent_skills.travel_plan_new.hooks import TravelPlanNewHooks
from app.agent.runtime.graph import AgentRuntimeState
from app.agent.runtime import builder as runtime_builder


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

    assert handled is None
    assert state.context["last_search_error"] == "food_decision_non_restaurant"


def test_food_assistant_finalizes_when_restaurants_found():
    state = AgentRuntimeState(session_id="s1", message="出去吃粉面")

    handled = FoodAssistantHooks().handle_tool_result(
        state,
        "search_restaurants",
        [
            {"name": "一食坊粉面", "address": "丰顺路惟盛园", "rating": "4.6"},
            {"name": "国欢粉面馆", "address": "西二环辅路"},
        ],
    )

    assert handled is not None
    assert handled["recommendations"][0]["type"] == "restaurant"
    assert handled["recommendations"][0]["title"] == "一食坊粉面"
    assert state.context["last_restaurants"][0]["name"] == "一食坊粉面"


def test_food_assistant_does_not_duplicate_price_prefix():
    state = AgentRuntimeState(session_id="s1", message="人民广场附近粤菜")

    handled = FoodAssistantHooks().handle_tool_result(
        state,
        "search_restaurants",
        [
            {
                "name": "人民广场粤味小馆",
                "address": "人民广场步行 8 分钟",
                "rating": "4.7",
                "price": "人均 88",
            }
        ],
    )

    assert handled is not None
    reason = handled["recommendations"][0]["reason"]
    assert "人均 88" in reason
    assert "人均 人均" not in reason


def test_food_assistant_short_circuits_selected_restaurant_with_typo_particle():
    state = AgentRuntimeState(session_id="s1", message="味汁园把")
    state.context = {
        "last_restaurants": [
            {"name": "长沙米粉(惟盛园店)", "address": "惟盛园小区6栋"},
            {
                "name": "味汁园(惟盛园店)",
                "address": "惟盛园小区4栋2单元106",
                "lat": 28.148423,
                "lng": 112.933207,
            },
        ]
    }

    handled = FoodAssistantHooks().short_circuit_final(state)

    assert handled is not None
    assert handled["recommendations"][0]["title"] == "味汁园(惟盛园店)"
    assert handled["selected_restaurant"]["name"] == "味汁园(惟盛园店)"
    assert state.context["selected_restaurant"]["name"] == "味汁园(惟盛园店)"
    assert handled["followups"][0] == "我可以继续帮你规划路线。"


def test_food_assistant_filters_food_decision_for_eat_out_mode():
    state = AgentRuntimeState(session_id="s1", message="出去吃粉面")
    state.context = {"food_mode": "eat_out"}

    allowed = FoodAssistantHooks().filter_allowed_tools(
        state,
        [
            "memory_search",
            "food_decision",
            "get_fridge_items",
            "search_restaurants",
            "geocode_location",
            "plan_route",
            "get_weather",
        ],
    )

    assert allowed == ["search_restaurants", "geocode_location", "get_weather"]


def test_food_assistant_filters_decide_food_to_decision_tool_only():
    state = AgentRuntimeState(session_id="s1", message="吃什么好")
    state.context = {"food_mode": "decide_food"}

    allowed = FoodAssistantHooks().filter_allowed_tools(
        state,
        [
            "memory_search",
            "food_decision",
            "get_fridge_items",
            "search_restaurants",
            "geocode_location",
            "get_weather",
        ],
    )

    assert allowed == ["food_decision"]


def test_home_chef_filters_to_recipe_tools_only():
    state = AgentRuntimeState(session_id="s1", message="冰箱里有鸡蛋怎么做", scene="home_chef")
    state.context = {"intent": "cook_home"}

    allowed = HomeChefHooks().filter_allowed_tools(
        state,
        [
            "memory_search",
            "food_decision",
            "get_fridge_items",
            "rag_search_recipes",
            "search_recipes",
            "search_restaurants",
            "geocode_location",
        ],
    )

    assert allowed == ["get_fridge_items", "rag_search_recipes", "search_recipes"]


@pytest.mark.asyncio
async def test_food_assistant_keeps_affirmative_followup_in_eat_out_mode():
    state = AgentRuntimeState(session_id="s1", message="可以啊")
    context = {
        "intent": "eat_out",
        "history": [
            {
                "role": "assistant",
                "content": "要不要我按距离、评分或口味再帮你筛一轮？",
            }
        ],
    }

    extra = await FoodAssistantHooks().build_context(state, context, runtime=None)

    assert extra["food_mode"] == "eat_out"
    assert state.context["food_mode"] == "eat_out"


def test_route_hook_filters_non_route_tools():
    state = AgentRuntimeState(session_id="s1", message="怎么走")

    allowed = RoutePlannerHooks().filter_allowed_tools(
        state,
        ["memory_search", "geocode_location", "plan_route", "source_event_search"],
    )

    assert allowed == ["geocode_location", "plan_route"]


def test_runtime_blocks_repeated_tool_call_loop():
    state = AgentRuntimeState(
        session_id="s1",
        tool_calls=[
            {"name": "search_restaurants", "args": {"query": "粉面"}},
            {"name": "search_restaurants", "args": {"query": "粉面"}},
        ],
    )

    allowed = runtime_builder._enforce_tool_execution_policy(
        state,
        [{"name": "search_restaurants", "args": {"query": "粉面"}, "id": "call_1"}],
    )

    assert allowed == []
    assert state.final_json is not None
    assert state.final_json["failure_class"] == "agent_execution_loop"
    assert state.events[-1]["event"] == "recovery"


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


def test_route_hook_short_circuits_bare_route_followup_to_clarification():
    state = AgentRuntimeState(session_id="s1", scene="route", message="怎么走呢")
    state.context = {}

    handled = RoutePlannerHooks().short_circuit_final(state)

    assert handled is not None
    assert handled["status"] == "needs_clarification"
    assert "去哪儿" in handled["recommendations"][0]["title"]


def test_route_hook_does_not_short_circuit_explicit_destination():
    state = AgentRuntimeState(session_id="s1", scene="route", message="怎么去西湖")
    state.context = {}

    assert RoutePlannerHooks().short_circuit_final(state) is None


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


def test_travel_plan_new_filters_tools_by_workflow_phase():
    hook = TravelPlanNewHooks()
    allowed_tools = [
        "memory_search",
        "source_event_search",
        "geocode_location",
        "plan_route",
        "travel_fetch_url_content",
        "travel_search_poi",
        "travel_search_nearby_poi",
        "travel_create_personal_map",
        "food_decision",
    ]

    ingesting = AgentRuntimeState(session_id="s-ingest", scene="travel_planner")
    assert hook.filter_allowed_tools(ingesting, allowed_tools) == [
        "travel_fetch_url_content",
        "travel_search_poi",
        "travel_search_nearby_poi",
    ]

    pending = AgentRuntimeState(
        session_id="s-pending",
        scene="travel_planner",
        context_overrides={
            "travel_payload": {
                "destination": "杭州",
                "extracted_places": [{"name": "西湖", "category": "attraction"}],
            }
        },
    )
    assert hook.filter_allowed_tools(pending, allowed_tools) == ["travel_search_poi"]

    candidates_ready = AgentRuntimeState(
        session_id="s-candidates",
        scene="travel_planner",
        context_overrides={
            "travel_payload": {
                "candidates": [
                    {
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "longitude": 120.148, "latitude": 30.242},
                    }
                ]
            }
        },
    )
    assert hook.filter_allowed_tools(candidates_ready, allowed_tools) == []

    map_action = AgentRuntimeState(
        session_id="s-map",
        scene="travel_planner",
        context_overrides={"travel_action": "generate_map"},
    )
    assert hook.filter_allowed_tools(map_action, allowed_tools) == ["travel_create_personal_map"]


def test_travel_plan_new_limits_poi_verification_batch_and_finalizes_on_budget():
    content = "\n".join(
        f"{index}. **地点{index}** - 推荐理由"
        for index in range(1, 12)
    )
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        message="杭州3天",
        skill_state={"last_ai_message_content": content},
        context_overrides={"travel_payload": {"destination": "杭州"}},
    )
    hook = TravelPlanNewHooks()

    calls = hook.forced_tool_calls(state) or []

    assert len(calls) == 8
    for index in range(1, 9):
        state.observations.append(
            {
                "tool": "travel_search_poi",
                "result": {
                    "query": {"keywords": f"地点{index}", "city": "杭州", "category": "attraction"},
                    "selected_poi": {
                        "poi_id": f"POI{index}",
                        "name": f"地点{index}",
                        "address": "杭州",
                        "longitude": 120 + index / 1000,
                        "latitude": 30 + index / 1000,
                    },
                    "pois": [],
                },
            }
        )

    handled = hook.handle_tool_result(state, "travel_search_poi", state.observations[-1]["result"])

    assert handled is not None
    assert handled["state"] == "candidates_ready"
    assert handled["await_confirmation"] is True
    assert len(handled["candidates"]) == 8
    assert handled["pending_places"]
    assert "POI 验证预算" in handled["warnings"][0]


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


def test_travel_plan_new_extracts_general_food_context_and_filters_structure_titles():
    content = """
## 阶段2：攻略图片内容解析

**景点类：**
1. **人民公园** - 慢生活体验

**美食类：**
1. 推荐餐厅：A饭店、B茶社
2. 午饭吃火锅、米粉、酸奶
3. 饭后去人民公园
7. 阶段3
住宿区域

现在调用高德 POI 搜索来验证这些地点。
"""
    state = AgentRuntimeState(
        session_id="s-food-context",
        scene="travel_planner",
        message="帮我规划旅行",
        skill_state={"last_ai_message_content": content},
        context_overrides={"travel_payload": {"destination": "杭州"}},
    )
    hook = TravelPlanNewHooks()

    context = hook.build_context(state, {})
    extracted_names = [item["name"] for item in context["travel_state"]["extracted_places"]]
    food_names = [item["name"] for item in context["travel_state"]["food_items"]]
    calls = hook.forced_tool_calls(state) or []
    call_keywords = [item["args"]["keywords"] for item in calls]

    assert "阶段3" not in extracted_names
    assert "住宿区域" not in extracted_names
    assert "阶段3" not in food_names
    assert "住宿区域" not in food_names
    assert "A饭店" in call_keywords
    assert "B茶社" in call_keywords
    assert "火锅" in food_names
    assert "米粉" in food_names
    assert "酸奶" in food_names
    assert "人民公园" in call_keywords


def test_travel_plan_new_uses_llm_structured_extraction_and_curate_lists():
    state = AgentRuntimeState(
        session_id="s-structured",
        scene="travel_planner",
        message="根据图片规划旅行",
        context_overrides={"travel_payload": {"destination": "杭州"}},
    )
    hook = TravelPlanNewHooks()
    final_payload = {
        "extracted_places": [
            {
                "name": "西湖",
                "category": "nature",
                "source": "image_extracted",
                "score": 10,
                "business_hours": "全天",
                "suggested_duration_minutes": 180,
                "recommended_reason": "攻略重点推荐的湖区景观",
            },
            {
                "name": "湖边酒店",
                "category": "hotel",
                "source": "image_extracted",
                "score": 8,
                "price_range": "¥300-500/晚",
                "recommended_reason": "靠近湖区，方便第二天出发",
            },
            {
                "name": "知味观湖滨店",
                "category": "restaurant",
                "source": "image_extracted",
                "score": 9,
                "average_cost_yuan": 80,
                "business_hours": "10:00-21:00",
                "recommended_reason": "攻略推荐的本地餐饮",
            },
            {"name": "阶段3", "category": "restaurant", "source": "image_extracted"},
            {"name": "小笼包", "category": "food_item", "source": "image_extracted", "recommended_reason": "当地特色"},
            {"name": "踩雷餐厅", "category": "excluded", "exclude_reason": "攻略明确避雷"},
        ],
        "excluded_places": [{"name": "旧酒店", "exclude_reason": "位置偏远"}],
    }

    state.final_json = final_payload
    handled = hook.handle_tool_result(state, "submit_final_answer", {"_final_answer": final_payload})

    assert handled is None
    assert state.final_json is None
    calls = hook.forced_tool_calls(state) or []
    call_keywords = [item["args"]["keywords"] for item in calls]
    assert call_keywords == ["西湖", "湖边酒店", "知味观湖滨店"]
    assert "阶段3" not in call_keywords
    assert state.context_overrides["travel_payload"]["food_items"][0]["name"] == "小笼包"
    assert state.context_overrides["travel_payload"]["excluded_places"][0]["name"] == "旧酒店"

    state.observations.extend(
        [
            {
                "tool": "travel_search_poi",
                "result": {
                    "query": {"keywords": "西湖", "city": "杭州", "category": "nature"},
                    "selected_poi": {"poi_id": "WESTLAKE", "name": "西湖风景名胜区", "address": "杭州市西湖区", "longitude": 120.1, "latitude": 30.2},
                },
            },
            {
                "tool": "travel_search_poi",
                "result": {
                    "query": {"keywords": "湖边酒店", "city": "杭州", "category": "hotel"},
                    "selected_poi": {"poi_id": "HOTEL", "name": "湖边酒店", "address": "湖滨路1号", "longitude": 120.11, "latitude": 30.21},
                },
            },
            {
                "tool": "travel_search_poi",
                "result": {
                    "query": {"keywords": "知味观湖滨店", "city": "杭州", "category": "restaurant"},
                    "selected_poi": {"poi_id": "FOOD", "name": "知味观(湖滨店)", "address": "湖滨路", "longitude": 120.12, "latitude": 30.22},
                },
            },
        ]
    )

    handled = hook.handle_tool_result(state, "travel_search_poi", state.observations[-1]["result"])

    assert handled is not None
    raw_text = handled["raw_text"]
    assert "### 景点类" in raw_text
    assert "|---|" not in raw_text
    assert "西湖（高德：西湖风景名胜区）" in raw_text
    assert "- 来源：攻略提取" in raw_text
    assert "- 营业时间：全天" in raw_text
    assert "- 预计时长：3小时" in raw_text
    assert "攻略重点推荐的湖区景观" in raw_text
    assert "### 住宿类" in raw_text
    assert "- 价格区间：¥300-500/晚" in raw_text
    assert "¥300-500/晚" in raw_text
    assert "### 美食类" in raw_text
    assert "知味观湖滨店（高德：知味观(湖滨店)）" in raw_text
    assert "- 人均消费：¥80" in raw_text
    assert "### 已排除的地点" in raw_text
    assert "旧酒店" in raw_text
    assert "阶段3" not in raw_text


def test_travel_plan_new_falls_back_to_llm_vision_text_and_shows_pending_places():
    content = """
**景点类：**
1. **西湖** - 攻略重点推荐
2. **灵隐寺** - 寺庙景点
3. **法喜寺** - 寺庙景点

**美食类：**
1. 火锅
2. 米粉
"""
    state = AgentRuntimeState(
        session_id="s-fallback",
        scene="travel_planner",
        message="根据图片规划旅行",
        skill_state={"last_ai_message_content": content},
        context_overrides={
            "attachments": [{"kind": "image", "object_key": "guide.jpg"}],
            "travel_payload": {
                "destination": "杭州",
                "extracted_places": [{"name": "西湖", "category": "nature", "source": "image_extracted"}],
            },
        },
    )
    hook = TravelPlanNewHooks()

    calls = hook.forced_tool_calls(state) or []
    call_keywords = [item["args"]["keywords"] for item in calls]

    assert call_keywords == ["西湖", "灵隐寺", "法喜寺"]
    assert [item["name"] for item in hook.build_context(state, {})["travel_state"]["food_items"]] == ["火锅", "米粉"]

    state.observations.append(
        {
            "tool": "travel_search_poi",
            "result": {
                "query": {"keywords": "西湖", "city": "杭州", "category": "nature"},
                "selected_poi": {"poi_id": "WESTLAKE", "name": "西湖风景名胜区", "address": "杭州市西湖区", "longitude": 120.1, "latitude": 30.2},
            },
        }
    )

    handled = hook.best_effort_fallback(state)

    assert handled is not None
    assert handled["state"] == "candidates_ready"
    assert [item["name"] for item in handled["pending_places"]] == ["灵隐寺", "法喜寺"]
    assert "### 待继续验证" in handled["raw_text"]
    assert "灵隐寺" in handled["raw_text"]
    assert "法喜寺" in handled["raw_text"]
    assert "|---|" not in handled["raw_text"]


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


def test_travel_plan_new_filters_all_tools_until_itinerary_confirmation():
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

    assert allowed == []


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


def test_travel_plan_new_default_map_title_does_not_duplicate_travel_word():
    state = AgentRuntimeState(
        session_id="s1",
        scene="travel_planner",
        context_overrides={
            "travel_action": "generate_map",
            "travel_payload": {
                "trip_meta": {},
                "candidates": [
                    {
                        "name": "西湖",
                        "poi": {"poi_id": "B001", "name": "西湖", "longitude": 120.148, "latitude": 30.242},
                    }
                ],
            },
        },
    )

    calls = TravelPlanNewHooks().forced_tool_calls(state)

    assert calls is not None
    title = calls[0]["args"]["title"]
    assert title == "旅行地图"
    assert "旅行旅行" not in title


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
