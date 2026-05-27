from __future__ import annotations

import pytest

from app.agent.runtime.graph import AgentRuntimeState, _limit_skill_tool_calls, get_agent_runtime_config
from app.agent.skills.loader import load_skills_from_path
from app.agent.skills.resolver import SkillResolver


def test_travel_planner_skill_is_bundled_and_activates_for_trip_request():
    skills = load_skills_from_path("agent_skills")
    travel = next((skill for skill in skills if skill.id == "travel_planner"), None)

    assert travel is not None
    assert "travel_search_poi" in travel.tools.allow
    assert "travel_create_personal_map" in travel.tools.allow

    state = AgentRuntimeState(
        session_id="s-travel",
        scene="travel_planner",
        message="帮我用小红书攻略规划杭州3天2晚行程",
    )
    active = SkillResolver(skills).resolve(state, {"user_message": state.message})

    assert "travel_planner" in [skill.id for skill in active.skills]
    assert any(reason.startswith("scene:travel_planner") for reason in active.activation_reasons["travel_planner"])


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
