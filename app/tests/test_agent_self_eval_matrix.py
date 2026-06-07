from __future__ import annotations

import json
from pathlib import Path

from app.agent.supervisor.graph import route_agent_request
from scripts.replay_eval import evaluate_result


def _iter_turn_cases(cases: list[dict]):
    for case in cases:
        turns = case.get("turns")
        if isinstance(turns, list):
            for index, turn in enumerate(turns):
                merged = {**turn, "id": f"{case['id']}:{index + 1}"}
                yield merged
        else:
            yield case


def test_replay_fixture_declares_multi_scene_agent_expectations():
    cases = json.loads(Path("app/tests/fixtures/replay_cases.json").read_text(encoding="utf-8"))
    case_ids = {item["id"] for item in cases}

    assert {"travel-plan-basic", "travel-map-request", "travel-food-cross-scene"}.issubset(case_ids)
    assert {"eatout-location-as-target", "cook-home-query", "route-followup"}.issubset(case_ids)
    assert {"travel-multiturn-plan-adjust-map", "travel-cross-scene-food-route"}.issubset(case_ids)


def test_replay_fixture_route_expectations_match_router():
    cases = json.loads(Path("app/tests/fixtures/replay_cases.json").read_text(encoding="utf-8"))

    for case in _iter_turn_cases(cases):
        expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
        if not expect.get("worker") and not expect.get("worker_in"):
            continue
        decision = route_agent_request(
            {
                "session_id": f"fixture:{case['id']}",
                "scene": case.get("scene") or "chat",
                "message": case.get("message"),
                "context_overrides": case.get("context_overrides") or case.get("client_context_overrides"),
            }
        )
        expected_statuses = expect.get("status_in")
        status = (
            expected_statuses[0]
            if isinstance(expected_statuses, list) and expected_statuses and isinstance(expected_statuses[0], str)
            else "completed"
        )
        result = {
            "fallback": False,
            "worker": decision.worker,
            "intent": decision.intent,
            "status": status,
            "failure_class": None,
            "trace_id": "fixture-trace",
            "agent_result": {
                "status": status,
                "worker": decision.worker,
                "trace_id": "fixture-trace",
                "final": {"recommendations": [], "followups": [], "warnings": []},
            },
        }

        assert evaluate_result(case, result)["passed"], case["id"]
