from __future__ import annotations

from scripts.agent_dialogue_quality_probe import (
    SCENARIOS,
    _preflight_config_from_env,
    _provider_unavailable_issue_from_result,
    aggregate_quality_metrics,
    evaluate_turn,
    preflight_provider,
    wait_for_turn_persistence,
)


def test_dialogue_probe_scenarios_are_all_long_multiturn_cases():
    assert {scenario["id"] for scenario in SCENARIOS} >= {
        "travel_revision_city_swap_resume_map_long",
        "travel_food_route_cross_context_long",
        "time_boxed_transit_food_route_long",
    }
    for scenario in SCENARIOS:
        assert len(scenario.get("turns") or []) >= 5, scenario["id"]


def test_dialogue_probe_detects_provider_billing_issue_from_diagnostics():
    result = {
        "agent_result": {
            "final": {},
            "diagnostics": {
                "provider_issue": {
                    "http_status": 402,
                    "code": "provider_upstream_error",
                    "user_message": "Error code: 402 - Insufficient Balance",
                }
            },
        },
        "text": "fallback",
    }

    issue = _provider_unavailable_issue_from_result(result)

    assert issue is not None
    assert issue["http_status"] == 402
    assert issue["kind"] == "provider_unavailable"


def test_dialogue_probe_evaluation_short_circuits_provider_unavailable():
    result = {
        "provider_unavailable": {
            "kind": "provider_unavailable",
            "http_status": 402,
            "code": "provider_billing_unavailable",
        },
        "agent_result": {},
    }

    evaluation = evaluate_turn({"expect": {"status_in": ["completed"]}}, result)

    assert evaluation == {"passed": False, "violations": ["provider_unavailable:402"]}


def test_dialogue_probe_quality_metrics_separate_provider_blocker():
    metrics = aggregate_quality_metrics(
        [
            {
                "id": "food",
                "provider_unavailable": {"kind": "provider_unavailable", "http_status": 402},
                "turns": [
                    {
                        "id": "food:1",
                        "status": "failed",
                        "failure_class": "upstream_error",
                        "provider_unavailable": {"kind": "provider_unavailable", "http_status": 402},
                        "evaluation": {"passed": False, "violations": ["provider_unavailable:402"]},
                    }
                ],
            }
        ],
        total_planned=16,
    )

    assert metrics["provider_blocked"] is True
    assert metrics["total_planned_scenarios"] == 16
    assert metrics["attempted_turns"] == 1
    assert metrics["scenario_pass_rate"] is None
    assert metrics["turn_pass_rate"] is None
    assert metrics["dimension_scores"]["provider_availability"] is None
    assert metrics["dimension_failed_turn_counts"]["provider_availability"] == 1


def test_dialogue_probe_quality_metrics_support_top_level_provider_preflight_blocker():
    metrics = aggregate_quality_metrics(
        [],
        total_planned=12,
        provider_unavailable={
            "kind": "provider_unavailable",
            "code": "provider_api_key_missing",
            "action": "set_provider_api_key",
        },
    )

    assert metrics["provider_blocked"] is True
    assert metrics["total_planned_scenarios"] == 12
    assert metrics["attempted_scenarios"] == 0
    assert metrics["attempted_turns"] == 0
    assert metrics["scenario_pass_rate"] is None
    assert metrics["turn_pass_rate"] is None
    assert metrics["dimension_scores"]["provider_availability"] is None
    assert metrics["dimension_failed_turn_counts"]["provider_availability"] == 1
    assert metrics["violation_counts"]["provider_unavailable:provider_api_key_missing"] == 1


def test_dialogue_probe_preflight_reports_missing_deepseek_key(monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "deepseek:deepseek-v4-flash")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_BASE_URL", raising=False)

    issue = preflight_provider("deepseek:deepseek-v4-flash", timeout_seconds=1.0)

    assert issue is not None
    assert issue["code"] == "provider_api_key_missing"
    assert issue["action"] == "set_provider_api_key"
    assert issue["model_config"]["provider"] == "deepseek"
    assert issue["model_config"]["model"] == "deepseek-v4-flash"
    assert issue["model_config"]["api_key_set"] is False


def test_dialogue_probe_preflight_resolves_openai_compatible_provider(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-test")
    monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")

    config = _preflight_config_from_env("deepseek:deepseek-v4-flash")

    assert config is not None
    assert config["provider"] == "deepseek"
    assert config["model"] == "deepseek-v4-flash"
    assert config["base_url"] == "https://api.deepseek.com"


def test_dialogue_probe_quality_metrics_group_agent_failures_by_dimension():
    metrics = aggregate_quality_metrics(
        [
            {
                "id": "mixed",
                "passed": False,
                "turns": [
                    {
                        "id": "mixed:1",
                        "status": "completed",
                        "evaluation": {
                            "passed": False,
                            "violations": [
                                "worker:general_chat!=expected:food_advisor",
                                "missing_tool_call:search_restaurants",
                                "business_state:None not in ['itinerary_generated']",
                                "answer_missing_any:第一,推荐",
                            ],
                        },
                    },
                    {
                        "id": "mixed:2",
                        "status": "completed",
                        "evaluation": {"passed": True, "violations": []},
                    },
                ],
            }
        ],
        total_planned=1,
    )

    assert metrics["provider_blocked"] is False
    assert metrics["scenario_pass_rate"] == 0.0
    assert metrics["turn_pass_rate"] == 0.5
    assert metrics["dimension_failed_turn_counts"]["route_accuracy"] == 1
    assert metrics["dimension_failed_turn_counts"]["tool_policy"] == 1
    assert metrics["dimension_failed_turn_counts"]["business_payload"] == 1
    assert metrics["dimension_failed_turn_counts"]["answer_quality"] == 1
    assert metrics["dimension_scores"]["route_accuracy"] == 0.5


def test_dialogue_probe_evaluation_checks_expected_travel_candidates():
    result = {
        "trace_id": "trace",
        "status": "completed",
        "worker": "travel_planner",
        "agent_result": {
            "status": "completed",
            "diagnostics": {"route": {"worker": "travel_planner"}},
            "final": {
                "state": "candidates_ready",
                "candidates": [{"name": "西湖"}],
            },
        },
        "answer": {},
    }

    evaluation = evaluate_turn(
        {"expect": {"candidate_expected_any": ["西湖", "灵隐寺"]}},
        result,
    )

    assert evaluation["passed"] is False
    assert "candidate_missing_any:灵隐寺" in evaluation["violations"]


class _FakeResponse:
    def __init__(self, messages):
        self._messages = messages

    def raise_for_status(self):
        return None

    def json(self):
        return {"data": {"messages": self._messages}}


class _FakeMessageClient:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)

    def get(self, *_args, **_kwargs):
        if len(self._snapshots) > 1:
            return _FakeResponse(self._snapshots.pop(0))
        return _FakeResponse(self._snapshots[0])


def test_dialogue_probe_waits_until_assistant_message_is_persisted():
    client = _FakeMessageClient(
        [
            [{"role": "user"}],
            [{"role": "user"}, {"role": "assistant"}],
        ]
    )

    result = wait_for_turn_persistence(
        client,
        "session-1",
        {"total": 0, "user": 0, "assistant": 0, "tool": 0},
        timeout_seconds=1.0,
        poll_seconds=0.01,
    )

    assert result["ok"] is True
    assert result["after"]["assistant"] == 1


def test_dialogue_probe_reports_unpersisted_assistant_after_timeout():
    client = _FakeMessageClient([[{"role": "user"}]])

    result = wait_for_turn_persistence(
        client,
        "session-1",
        {"total": 0, "user": 0, "assistant": 0, "tool": 0},
        timeout_seconds=0.01,
        poll_seconds=0.01,
    )

    assert result["ok"] is False
    assert result["reason"] == "assistant_not_persisted_after_final"
