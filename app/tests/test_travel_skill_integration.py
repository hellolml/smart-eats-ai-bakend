from __future__ import annotations

import pytest

from app.agent.runtime.graph import AgentRuntimeState, _limit_skill_tool_calls, get_agent_runtime_config
from app.agent.skills.loader import load_skills_from_path
from app.agent.skills.resolver import SkillResolver
from app.domain.app.service import AppBffService


def test_travel_plan_new_skill_replaces_legacy_travel_planner_and_activates():
    skills = load_skills_from_path("agent_skills")
    legacy = next((skill for skill in skills if skill.id == "travel_planner"), None)
    travel = next((skill for skill in skills if skill.id == "travel_plan_new"), None)

    assert legacy is None
    assert travel is not None
    assert "travel_search_poi" in travel.tools.allow
    assert "travel_create_personal_map" in travel.tools.allow

    state = AgentRuntimeState(
        session_id="s-travel",
        scene="travel_planner",
        message="帮我用小红书攻略规划杭州3天2晚行程",
    )
    active = SkillResolver(skills).resolve(state, {"user_message": state.message})

    assert "travel_plan_new" in [skill.id for skill in active.skills]
    assert "travel_planner" not in [skill.id for skill in active.skills]
    assert any(reason.startswith("scene:travel_planner") for reason in active.activation_reasons["travel_plan_new"])


def test_travel_scene_food_words_stay_in_travel_skill_without_food_skills():
    skills = load_skills_from_path("agent_skills")
    state = AgentRuntimeState(
        session_id="s-travel-food",
        scene="travel_planner",
        message="帮我规划杭州3天行程，午饭和晚饭也安排当地美食",
    )

    active = SkillResolver(skills).resolve(state, {"user_message": state.message})
    active_ids = [skill.id for skill in active.skills]

    assert "travel_plan_new" in active_ids
    assert "food_decision" not in active_ids
    assert "restaurant_finder" not in active_ids


def test_food_assistant_skill_is_bundled_and_activates_for_eat_request():
    skills = load_skills_from_path("agent_skills")
    food = next((skill for skill in skills if skill.id == "food_assistant"), None)

    assert food is not None
    assert "food_decision" in food.tools.allow
    assert "search_restaurants" in food.tools.allow
    assert "get_fridge_items" in food.tools.allow

    state = AgentRuntimeState(
        session_id="s-food",
        scene="eat",
        message="今天吃点啥",
    )
    active = SkillResolver(skills).resolve(state, {"user_message": state.message})

    assert [skill.id for skill in active.skills if skill.id == "food_assistant"] == ["food_assistant"]


def test_chat_intent_forces_unified_food_skill():
    assert AppBffService._infer_chat_intent("今天吃点啥") == "food"
    assert AppBffService._infer_chat_intent("出去吃") == "food"
    assert AppBffService._infer_chat_intent("冰箱里有鸡蛋") == "food"
    assert AppBffService._infer_chat_intent("换一家不辣的") == "food"
    assert AppBffService._forced_skill_ids_for_intent("food") == ["food_assistant"]


def test_chat_route_intent_wins_for_navigation_followup():
    assert AppBffService._infer_chat_intent("第二家怎么走") == "route"


def test_agent_runtime_config_keeps_travel_tools_out_of_core_allowlist():
    tool_names = get_agent_runtime_config().core_tool_names

    assert "travel_search_poi" not in tool_names
    assert "travel_create_personal_map" not in tool_names


def test_skill_tool_call_limit_preserves_final_answer_and_limits_external_tools():
    calls = [
        {"name": "travel_search_poi", "args": {"keywords": "赛里木湖"}, "id": "call_1", "type": "tool_call"},
        {"name": "travel_search_poi", "args": {"keywords": "伊宁"}, "id": "call_2", "type": "tool_call"},
        {"name": "travel_search_poi", "args": {"keywords": "昭苏"}, "id": "call_3", "type": "tool_call"},
        {"name": "submit_final_answer", "args": {"recommendations": [], "followups": [], "warnings": []}, "id": "final", "type": "tool_call"},
    ]

    limited = _limit_skill_tool_calls(calls, max_tool_calls=2)

    assert [item["id"] for item in limited] == ["call_1", "call_2", "final"]


@pytest.mark.asyncio
async def test_travel_search_poi_normalizes_amap_results(monkeypatch):
    from app.agent.tools.travel_search_poi import travel_search_poi_tool

    async def _fake_text_search(*_args, **_kwargs):
        return [
            {
                "id": "B001",
                "name": "西湖风景名胜区",
                "address": "杭州市西湖区",
                "location": {"lng": 120.148, "lat": 30.242},
            }
        ]

    monkeypatch.setattr("app.agent.tools.travel_search_poi.amap.text_search", _fake_text_search)

    result = await travel_search_poi_tool.ainvoke({"keywords": "西湖", "city": "杭州", "page_size": 3})

    assert result["query"]["keywords"] == "西湖"
    assert result["pois"][0]["poi_id"] == "B001"
    assert result["pois"][0]["longitude"] == 120.148
    assert result["pois"][0]["latitude"] == 30.242


@pytest.mark.asyncio
async def test_travel_search_poi_uses_cache_for_valid_pois(monkeypatch):
    from app.agent.tools.travel_search_poi import travel_search_poi
    from app.common.config import settings

    calls = {"count": 0}

    class FakeRedis:
        def __init__(self):
            self.value = None

        async def get(self, _key):
            return self.value

        async def setex(self, _key, _ttl, value):
            assert _ttl == settings.TRAVEL_POI_CACHE_TTL_SECONDS
            self.value = value

    async def _fake_text_search(*_args, **_kwargs):
        calls["count"] += 1
        return [
            {
                "id": "B001",
                "name": "西湖风景名胜区",
                "address": "杭州市西湖区",
                "location": {"lng": 120.148, "lat": 30.242},
            }
        ]

    redis = FakeRedis()
    monkeypatch.setattr("app.agent.tools.travel_search_poi.amap.text_search", _fake_text_search)

    first = await travel_search_poi({"keywords": "西湖", "city": "杭州", "redis_client": redis})
    second = await travel_search_poi({"keywords": "西湖", "city": "杭州", "redis_client": redis})

    assert calls["count"] == 1
    assert first["cache_hit"] is False
    assert second["cache_hit"] is True


@pytest.mark.asyncio
async def test_travel_search_poi_normalizes_nested_mcp_payload(monkeypatch):
    from app.agent.tools.travel_search_poi import travel_search_poi

    async def _fake_text_search(*_args, **_kwargs):
        return [
            {
                "poiId": "B002",
                "name": "断桥残雪",
                "address": "杭州市西湖区",
                "lon": "120.147",
                "lat": "30.257",
            }
        ]

    monkeypatch.setattr("app.agent.tools.travel_search_poi.amap.text_search", _fake_text_search)

    result = await travel_search_poi({"keywords": "断桥", "city": "杭州"})

    assert result["pois"][0]["poi_id"] == "B002"
    assert result["pois"][0]["longitude"] == 120.147
    assert result["pois"][0]["latitude"] == 30.257


@pytest.mark.asyncio
async def test_travel_search_poi_does_not_cache_invalid_pois(monkeypatch):
    from app.agent.tools.travel_search_poi import travel_search_poi

    class FakeRedis:
        def __init__(self):
            self.value = None

        async def get(self, _key):
            return self.value

        async def setex(self, _key, _ttl, value):
            self.value = value

    async def _fake_text_search(*_args, **_kwargs):
        return [{"name": "无坐标地点"}]

    redis = FakeRedis()
    monkeypatch.setattr("app.agent.tools.travel_search_poi.amap.text_search", _fake_text_search)

    await travel_search_poi({"keywords": "无坐标", "redis_client": redis})

    assert redis.value is None


@pytest.mark.asyncio
async def test_travel_create_personal_map_returns_qr_payload(monkeypatch):
    from app.agent.tools.travel_create_personal_map import travel_create_personal_map_tool

    async def _fake_personal_map(*_args, **_kwargs):
        return {"qr_code_url": "https://example.com/qr.png", "schema_url": "amapuri://foo"}

    monkeypatch.setattr("app.agent.tools.travel_create_personal_map.create_personal_map", _fake_personal_map)

    result = await travel_create_personal_map_tool.ainvoke(
        {
            "title": "杭州3天2晚",
            "line_list": [
                {
                    "title": "Day 1",
                    "pointInfoList": [
                        {"name": "西湖", "lon": 120.148, "lat": 30.242, "poiId": "B001"}
                    ],
                }
            ],
        }
    )

    assert result["qr_code_url"] == "https://example.com/qr.png"
    assert result["line_list"][0]["pointInfoList"][0]["name"] == "西湖"
