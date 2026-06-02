from __future__ import annotations

import pytest

from app.agent.multi_agent import AgentRouter
from app.common.config import settings


@pytest.fixture(autouse=True)
def preference_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "USER_PREFERENCE_MD_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_router_maps_travel_plan_payload_to_travel_agent_contract():
    prepared = await AgentRouter().prepare_turn(
        session_id="s1",
        user_id="u1",
        payload={
            "message": "帮我做成都旅行计划",
            "plan_type": "travel",
            "action": "confirm_candidates",
            "payload": {"candidates": [{"name": "武侯祠"}]},
        },
        latest_final_json={"state": "candidates_ready", "candidates": [{"name": "宽窄巷子"}]},
    )

    assert prepared.agent_id == "travel_plan"
    assert prepared.plan_type == "travel"
    assert prepared.payload["scene"] == "travel_planner"
    assert prepared.payload["travel_action"] == "confirm_candidates"
    assert prepared.payload["travel_payload"]["previous_final_json"]["state"] == "candidates_ready"
    assert prepared.payload["travel_payload"]["candidates"] == [{"name": "武侯祠"}]
    assert prepared.payload["client_context_overrides"]["agent_id"] == "travel_plan"
    assert prepared.payload["client_context_overrides"]["plan_type"] == "travel"


@pytest.mark.asyncio
async def test_router_marks_travel_new_attachments_as_refresh_sources():
    prepared = await AgentRouter().prepare_turn(
        session_id="s1",
        user_id="u1",
        payload={
            "message": "我又上传了一张攻略图",
            "scene": "travel_planner",
            "payload": {"new_attachments": [{"attachment_id": "a2", "kind": "image"}]},
        },
        latest_final_json={
            "state": "itinerary_generated",
            "itinerary": {"days": [{"day": 1}]},
            "map": {"qr_code_url": "old"},
        },
    )

    assert prepared.agent_id == "travel_plan"
    assert prepared.payload["travel_action"] == "refresh_sources"
    assert prepared.payload["travel_payload"]["state"] == "ingesting_content"
    assert prepared.payload["travel_payload"]["refresh_sources"] is True
    assert prepared.payload["travel_payload"]["stale_artifacts"]["itinerary"] is True
    assert "itinerary" not in prepared.payload["travel_payload"]
    assert "map" not in prepared.payload["travel_payload"]


@pytest.mark.asyncio
async def test_router_maps_eat_scene_to_food_decision_agent():
    prepared = await AgentRouter().prepare_turn(
        session_id="s1",
        user_id="u1",
        payload={
            "message": "今天吃点啥",
            "scene": "eat",
            "client_context_overrides": {"location_text": "恒伟星中心"},
        },
    )

    assert prepared.agent_id == "food_decision"
    assert prepared.payload["scene"] == "eat"
    assert prepared.payload["client_context_overrides"]["intent"] == "eat_out"
    assert "food_decision" in prepared.payload["client_context_overrides"]["forced_skill_ids"]
    assert "user_preference_md" in prepared.payload["client_context_overrides"]


@pytest.mark.asyncio
async def test_router_leaves_chat_payload_unchanged_for_unknown_agent():
    payload = {"message": "你好", "scene": "chat"}

    prepared = await AgentRouter().prepare_turn(
        session_id="s1",
        user_id="u1",
        payload=payload,
    )

    assert prepared.agent_id == "chat"
    assert prepared.plan_type is None
    assert prepared.payload == payload
