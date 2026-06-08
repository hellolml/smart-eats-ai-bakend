from __future__ import annotations

import pytest

from app.agent.supervisor.workers import WORKER_SPECS, _prepare_worker_payload
from app.common.config import settings


def _spec(name: str):
    return next(item for item in WORKER_SPECS if item.name == name)


@pytest.fixture(autouse=True)
def preference_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "USER_PREFERENCE_MD_DIR", str(tmp_path))


@pytest.mark.asyncio
async def test_travel_worker_merges_plan_payload_with_latest_travel_state():
    payload = await _prepare_worker_payload(
        _spec("travel_planner"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "帮我做成都旅行计划",
            "plan_type": "travel",
            "action": "confirm_candidates",
            "payload": {"candidates": [{"name": "武侯祠"}]},
            "context_overrides": {
                "latest_travel_final_json": {
                    "state": "candidates_ready",
                    "candidates": [{"name": "宽窄巷子"}],
                }
            },
        },
    )

    assert payload["scene"] == "travel_planner"
    assert payload["agent_id"] == "travel_plan"
    assert payload["plan_type"] == "travel"
    assert payload["travel_action"] == "confirm_candidates"
    assert payload["travel_payload"]["previous_final_json"]["state"] == "candidates_ready"
    assert payload["travel_payload"]["candidates"] == [{"name": "武侯祠"}]
    assert payload["context_overrides"]["agent_id"] == "travel_plan"
    assert payload["context_overrides"]["plan_type"] == "travel"


@pytest.mark.asyncio
async def test_travel_worker_marks_new_attachments_as_refresh_sources():
    payload = await _prepare_worker_payload(
        _spec("travel_planner"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "我又上传了一张攻略图",
            "scene": "travel_planner",
            "payload": {"new_attachments": [{"attachment_id": "a2", "kind": "image"}]},
            "context_overrides": {
                "latest_travel_final_json": {
                    "state": "itinerary_generated",
                    "itinerary": {"days": [{"day": 1}]},
                    "map": {"qr_code_url": "old"},
                }
            },
        },
    )

    assert payload["travel_action"] == "refresh_sources"
    assert payload["travel_payload"]["state"] == "ingesting_content"
    assert payload["travel_payload"]["refresh_sources"] is True
    assert payload["travel_payload"]["stale_artifacts"]["itinerary"] is True
    assert "itinerary" not in payload["travel_payload"]
    assert "map" not in payload["travel_payload"]


@pytest.mark.asyncio
async def test_travel_worker_infers_confirm_candidates_action_from_message():
    payload = await _prepare_worker_payload(
        _spec("travel_planner"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "确认这些候选地点，请继续生成最终每日行程。",
            "scene": "travel_planner",
            "context_overrides": {
                "latest_travel_final_json": {
                    "state": "candidates_ready",
                    "candidates": [{"name": "宽窄巷子"}],
                }
            },
        },
    )

    assert payload["travel_action"] == "confirm_candidates"
    assert payload["context_overrides"]["action"] == "confirm_candidates"


@pytest.mark.asyncio
async def test_travel_worker_rebuilds_payload_when_user_revises_destination_and_places():
    payload = await _prepare_worker_payload(
        _spec("travel_planner"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "临时改成杭州 1 天，不去拙政园，只保留西湖和灵隐寺，别太赶。",
            "scene": "travel_planner",
            "context_overrides": {
                "latest_travel_final_json": {
                    "state": "candidates_ready",
                    "trip_meta": {"destination": "苏州", "days": 2},
                    "candidates": [{"name": "拙政园"}, {"name": "苏州博物馆"}],
                    "failed_places": [{"name": "七里山塘"}],
                    "itinerary": {"days": [{"day_number": 1}]},
                    "map": {"qr_code_url": "old"},
                }
            },
        },
    )

    travel_payload = payload["travel_payload"]
    assert payload.get("travel_action") != "confirm_candidates"
    assert travel_payload["state"] == "ingesting_content"
    assert travel_payload["trip_meta"]["destination"] == "杭州"
    assert travel_payload["trip_meta"]["days"] == 1
    assert [item["name"] for item in travel_payload["extracted_places"]] == ["西湖", "灵隐寺"]
    assert [item["name"] for item in travel_payload["excluded_places"]] == ["拙政园"]
    assert "candidates" not in travel_payload
    assert "itinerary" not in travel_payload
    assert "map" not in travel_payload
    assert travel_payload["previous_candidates"] == [{"name": "拙政园"}, {"name": "苏州博物馆"}]


@pytest.mark.asyncio
async def test_food_worker_injects_food_decision_contract():
    payload = await _prepare_worker_payload(
        _spec("food_advisor"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "今天吃点啥",
            "scene": "eat",
            "context_overrides": {"location_text": "恒伟星中心"},
        },
    )

    assert payload["agent_id"] == "food_decision"
    assert payload["scene"] == "eat"
    assert payload["context_overrides"]["intent"] == "decide_food"
    assert "food_assistant" in payload["context_overrides"]["forced_skill_ids"]
    assert "user_preference_md" in payload["context_overrides"]


@pytest.mark.asyncio
async def test_food_worker_infers_home_chef_intent_and_preserves_extra_context():
    payload = await _prepare_worker_payload(
        _spec("food_advisor"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "冰箱里有鸡蛋，自己做点什么",
            "context_overrides": {
                "environment": {"location": {"lat": 30.2}},
                "location_text": "恒伟星中心",
            },
        },
    )

    overrides = payload["context_overrides"]
    assert overrides["intent"] == "cook_home"
    assert overrides["forced_skill_ids"] == ["home_chef"]
    assert overrides["environment"]["location"]["lat"] == 30.2
    assert overrides["location_text"] == "恒伟星中心"
