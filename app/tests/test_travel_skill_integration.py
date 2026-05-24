from __future__ import annotations

import pytest

from app.agent.agents import smart_eats as smart_eats_module
from app.agent.agents.smart_eats import build_smart_eats_graph, get_smart_eats_agent_config, SmartEatsState
from app.agent.state import ChatState
from app.agent.skills.loader import load_skills_from_path
from app.agent.skills.resolver import SkillResolver


def test_travel_planner_skill_is_bundled_and_activates_for_trip_request():
    skills = load_skills_from_path("agent_skills")
    travel = next((skill for skill in skills if skill.id == "travel_planner"), None)

    assert travel is not None
    assert "travel_search_poi" in travel.tools.allow
    assert "travel_create_personal_map" in travel.tools.allow

    state = SmartEatsState(
        session_id="s-travel",
        scene="travel_planner",
        message="帮我用小红书攻略规划杭州3天2晚行程",
    )
    active = SkillResolver(skills).resolve(state, {"user_message": state.message})

    assert "travel_planner" in [skill.id for skill in active.skills]
    assert any(reason.startswith("scene:travel_planner") for reason in active.activation_reasons["travel_planner"])


def test_smart_eats_tool_allowlist_includes_travel_tools():
    tool_names = get_smart_eats_agent_config().tool_names

    assert "travel_search_poi" in tool_names
    assert "travel_create_personal_map" in tool_names


@pytest.mark.asyncio
async def test_travel_skill_allowed_tools_filter_planner_visible_tools(monkeypatch, override_redis):
    captured = {"tool_names": []}

    async def _noop_ensure_chat_session(db, state):
        return None

    async def _refresh_with_skill_allowlist(db, redis_client, state, agent_config, emit_context_event=True):
        state.context = {
            "system_prompt": "test system",
            "allowed_tools": ["travel_search_poi"],
        }

    async def _fake_plan_tool_calls(self, system, user, available_tools):
        captured["tool_names"] = [tool["name"] for tool in available_tools]
        return {
            "content": "",
            "tool_calls": [
                {
                    "name": "submit_final_answer",
                    "args": {
                        "recommendations": [
                            {"type": "note", "title": "旅行规划", "reason": "allowlist_filtered"}
                        ],
                        "followups": [],
                        "warnings": [],
                    },
                    "id": "call_travel_allowlist",
                    "type": "tool_call",
                }
            ],
        }

    async def _noop_save_tool_message(*_args, **_kwargs):
        return None

    monkeypatch.setattr("app.agent.llm_adapters.OpenAIPlanner.plan_tool_calls", _fake_plan_tool_calls)
    monkeypatch.setattr(smart_eats_module, "_ensure_chat_session", _noop_ensure_chat_session)
    monkeypatch.setattr(smart_eats_module, "_refresh_observation_context", _refresh_with_skill_allowlist)
    monkeypatch.setattr("app.agent.agents.smart_eats.history.save_tool_message", _noop_save_tool_message)

    graph = build_smart_eats_graph(
        db=None,
        redis_client=override_redis,
        provider=None,
    ).compile()

    result = await graph.ainvoke(ChatState(session_id="s-travel-allowlist", message="规划杭州旅行").__dict__)

    assert captured["tool_names"] == ["travel_search_poi"]
    assert result["final_json"]["recommendations"][0]["reason"] == "allowlist_filtered"


@pytest.mark.asyncio
async def test_travel_search_poi_normalizes_amap_results(monkeypatch):
    from app.agent.tools.travel_search_poi import travel_search_poi

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

    result = await travel_search_poi({"keywords": "西湖", "city": "杭州", "page_size": 3})

    assert result["query"]["keywords"] == "西湖"
    assert result["pois"][0]["poi_id"] == "B001"
    assert result["pois"][0]["longitude"] == 120.148
    assert result["pois"][0]["latitude"] == 30.242


@pytest.mark.asyncio
async def test_travel_create_personal_map_returns_qr_payload(monkeypatch):
    from app.agent.tools.travel_create_personal_map import travel_create_personal_map

    async def _fake_personal_map(*_args, **_kwargs):
        return {"qr_code_url": "https://example.com/qr.png", "schema_url": "amapuri://foo"}

    monkeypatch.setattr("app.agent.tools.travel_create_personal_map.create_personal_map", _fake_personal_map)

    result = await travel_create_personal_map(
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
