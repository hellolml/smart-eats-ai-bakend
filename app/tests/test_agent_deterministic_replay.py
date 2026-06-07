import pytest

from scripts.agent_deterministic_replay import REQUIRED_COVERAGE, coverage_report, run_replay


@pytest.mark.asyncio
async def test_deterministic_replay_covers_core_workers_and_tool_calls():
    report = await run_replay()

    assert report["passed_count"] == report["total"]
    assert report["pass_rate"] == 1.0
    assert report["fallback_count"] == 0
    assert report["worker_tool_boundary_violations"] == []
    assert report["coverage"]["passed"] is True
    for key, required_values in REQUIRED_COVERAGE.items():
        assert set(report["coverage"]["observed"][key]) >= required_values
    assert set(report["coverage"]["observed"]["quality_issue_regressions"]) >= {
        "food_affirmation_mode_drift",
        "route_memory_tool_leak",
        "travel_tool_explosion",
    }
    assert report["tool_call_counts"]["food_decision"] >= 1
    assert report["tool_call_counts"]["search_restaurants"] >= 2
    assert report["tool_call_counts"]["plan_route"] >= 2
    assert report["tool_call_counts"]["get_fridge_items"] >= 1
    assert report["tool_call_counts"]["rag_search_recipes"] >= 1
    assert report["tool_call_counts"]["travel_search_poi"] >= 3
    assert report["tool_call_counts"]["travel_create_personal_map"] >= 1

    general_chat = next(item for item in report["results"] if item["id"] == "deterministic-general-chat")
    assert general_chat["worker"] == "general_chat"
    assert general_chat["tool_calls"] == []

    affirmative_refine = next(
        item for item in report["results"] if item["id"] == "deterministic-eat-out-affirm-refine-multiturn"
    )
    assert [turn["tool_calls"] for turn in affirmative_refine["turns"]] == [
        ["search_restaurants"],
        ["search_restaurants"],
    ]

    route_clarification = next(item for item in report["results"] if item["id"] == "deterministic-route-clarification")
    assert route_clarification["status"] == "needs_clarification"
    assert route_clarification["tool_calls"] == []

    cross_worker = next(item for item in report["results"] if item["id"] == "deterministic-travel-food-route-multiturn")
    assert [turn["worker"] for turn in cross_worker["turns"]] == [
        "travel_planner",
        "food_advisor",
        "route_planner",
    ]
    assert [turn["tool_calls"] for turn in cross_worker["turns"]] == [
        ["travel_search_poi"],
        ["search_restaurants"],
        ["plan_route"],
    ]

    travel_confirm_map = next(
        item for item in report["results"] if item["id"] == "deterministic-travel-confirm-map-multiturn"
    )
    assert [turn["answer"].get("state") for turn in travel_confirm_map["turns"]] == [
        "candidates_ready",
        "itinerary_generated",
        "map_generated",
    ]
    assert [turn["tool_calls"] for turn in travel_confirm_map["turns"]] == [
        ["travel_search_poi"],
        [],
        ["travel_create_personal_map"],
    ]


def test_deterministic_coverage_report_exposes_missing_requirements():
    report = coverage_report(
        [
            {
                "id": "only-chat",
                "worker": "food_advisor",
                "status": "completed",
                "intent": "decide_food",
                "tool_calls": ["food_decision"],
            }
        ],
        [{"id": "only-chat", "scene": "eat"}],
    )

    assert report["passed"] is False
    assert "travel_planner" in report["missing"]["workers"]
    assert "travel_search_poi" in report["missing"]["tool_calls"]
    assert "multi_turn" in report["missing"]["scenario_types"]
