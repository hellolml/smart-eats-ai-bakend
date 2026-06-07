from __future__ import annotations

from app.agent.contracts import (
    build_agent_run_result,
    final_json_for_failure,
    infer_failure_class,
)


def test_agent_run_result_wraps_success_final_with_business_payload():
    final_json = {
        "recommendations": [{"type": "note", "title": "done", "reason": "ok"}],
        "followups": [],
        "warnings": [],
        "state": "candidates_ready",
        "candidates": [{"name": "西湖"}],
    }

    result = build_agent_run_result(
        final_json=final_json,
        route_decision={"worker": "travel_planner", "intent": "travel"},
        worker="travel_planner",
        trace_id="trace-1",
    )

    assert result["status"] == "completed"
    assert result["worker"] == "travel_planner"
    assert result["trace_id"] == "trace-1"
    assert result["business_payload"]["state"] == "candidates_ready"
    assert result["business_payload"]["candidates"] == [{"name": "西湖"}]


def test_agent_run_result_prefers_routed_worker_over_inner_agent_id():
    result = build_agent_run_result(
        final_json={"recommendations": [], "followups": [], "warnings": []},
        route_decision={"worker": "food_advisor", "intent": "eat_out"},
        worker="food_decision",
        diagnostics={"worker": "food_decision"},
    )

    assert result["worker"] == "food_advisor"
    assert result["diagnostics"]["worker"] == "food_advisor"
    assert result["diagnostics"]["agent_id"] == "food_decision"


def test_agent_run_result_uses_needs_clarification_status_from_final():
    result = build_agent_run_result(
        final_json={
            "recommendations": [{"type": "note", "title": "你想去哪儿？", "reason": "缺少目的地"}],
            "followups": ["告诉我目的地"],
            "warnings": [],
            "status": "needs_clarification",
        },
        route_decision={"worker": "route_planner", "intent": "route"},
        worker="route_planner",
    )

    assert result["status"] == "needs_clarification"


def test_agent_run_result_keeps_explicit_failure_class():
    final_json = final_json_for_failure("worker_no_final")

    result = build_agent_run_result(
        final_json=final_json,
        route_decision={"worker": "food_advisor", "intent": "eat_out"},
        worker="food_advisor",
    )

    assert result["status"] == "failed"
    assert result["failure_class"] == "worker_no_final"
    assert result["diagnostics"]["failure_class"] == "worker_no_final"
    assert infer_failure_class(final_json) == "worker_no_final"
