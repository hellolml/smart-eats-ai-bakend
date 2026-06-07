from __future__ import annotations

import inspect

import pytest

from app.agent.supervisor import workers as worker_module
from app.agent.supervisor.workers import WORKER_SPECS, _prepare_worker_payload


def _spec(name: str):
    return next(item for item in WORKER_SPECS if item.name == name)


@pytest.mark.asyncio
async def test_travel_worker_injects_travel_contract(monkeypatch, tmp_path):
    monkeypatch.setattr("app.common.config.settings.USER_PREFERENCE_MD_DIR", str(tmp_path))

    payload = await _prepare_worker_payload(
        _spec("travel_planner"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "帮我做成都旅行计划",
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
    assert payload["context_overrides"]["agent_id"] == "travel_plan"
    assert payload["context_overrides"]["plan_type"] == "travel"
    assert payload["travel_payload"]["previous_final_json"]["state"] == "candidates_ready"


@pytest.mark.asyncio
async def test_route_worker_forces_route_skill():
    payload = await _prepare_worker_payload(
        _spec("route_planner"),
        {"session_id": "s1", "message": "怎么去这家店？"},
    )

    overrides = payload["context_overrides"]
    assert payload["scene"] == "route"
    assert overrides["intent"] == "route"
    assert overrides["forced_skill_ids"] == ["route_planner"]


@pytest.mark.asyncio
async def test_food_worker_forces_food_assistant_owner(monkeypatch, tmp_path):
    monkeypatch.setattr("app.common.config.settings.USER_PREFERENCE_MD_DIR", str(tmp_path))

    payload = await _prepare_worker_payload(
        _spec("food_advisor"),
        {"session_id": "s1", "user_id": "u1", "message": "可以啊", "scene": "eat"},
    )

    overrides = payload["context_overrides"]
    assert payload["scene"] == "eat"
    assert payload["agent_id"] == "food_decision"
    assert overrides["intent"] == "eat_out"
    assert "food_assistant" in overrides["forced_skill_ids"]


@pytest.mark.asyncio
async def test_food_worker_keeps_affirmative_restaurant_followup_in_eat_out_mode(monkeypatch, tmp_path):
    monkeypatch.setattr("app.common.config.settings.USER_PREFERENCE_MD_DIR", str(tmp_path))

    payload = await _prepare_worker_payload(
        _spec("food_advisor"),
        {
            "session_id": "s1",
            "user_id": "u1",
            "message": "可以啊",
            "scene": "eat",
            "context_overrides": {
                "intent": "eat_out",
                "last_restaurants": [{"name": "一食坊粉面", "lat": 31.1, "lng": 121.1}],
            },
        },
    )

    overrides = payload["context_overrides"]
    assert overrides["intent"] == "eat_out"
    assert "food_assistant" in overrides["forced_skill_ids"]
    assert "home_chef" not in overrides["forced_skill_ids"]


def test_worker_agent_uses_cached_runtime_graph_without_message_final_json_channel():
    source = inspect.getsource(worker_module.build_worker_agent)

    assert "build_cached_agent_runtime_graph" in source
    assert "runtime_graph_configurable" in source
    assert 'additional_kwargs={\n                        "final_json"' not in source
