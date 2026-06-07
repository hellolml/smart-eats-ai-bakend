from pathlib import Path

from scripts.replay_eval import (
    active_value_counts,
    classify_http_provider_issue,
    classify_request_provider_issue,
    evaluate_result,
    extract_provider_issue,
    is_fallback,
    load_cases,
    model_config_counts,
    provider_issue_counts,
    validate_worker_tool_boundary,
    validate_final_contract,
    validate_visible_text_quality,
    worker_tool_boundary_violations,
    worker_tool_call_counts,
    worker_tool_counts,
    _expected_worker,
)


def test_load_cases_from_fixture():
    cases = load_cases(Path("app/tests/fixtures/replay_cases.json"))
    assert isinstance(cases, list)
    assert len(cases) >= 8
    assert cases[0]["id"] == "eatout-location-as-target"


def test_is_fallback_detects_reason_fallback():
    assert is_fallback({"recommendations": [{"type": "note", "reason": "fallback"}]}) is True
    assert is_fallback({"recommendations": [{"type": "note", "reason": "ok"}]}) is False


def test_validate_final_contract_requires_agent_result_envelope():
    assert validate_final_contract({"agent_result": {}}) == ["contract:missing_agent_result"]
    assert validate_final_contract(
        {
            "trace_id": "t1",
            "worker": "general_chat",
            "failure_class": None,
            "agent_result": {
                "status": "completed",
                "worker": "general_chat",
                "trace_id": "t1",
                "final": {"recommendations": [], "followups": [], "warnings": []},
            },
        }
    ) == []


def test_validate_final_contract_requires_failure_class_for_failed_status():
    violations = validate_final_contract(
        {
            "trace_id": "t1",
            "worker": "general_chat",
            "agent_result": {
                "status": "failed",
                "worker": "general_chat",
                "trace_id": "t1",
                "final": {"recommendations": [], "followups": [], "warnings": []},
            },
        }
    )

    assert "contract:failed_missing_failure_class" in violations


def test_validate_final_contract_rejects_agent_result_worker_route_mismatch():
    violations = validate_final_contract(
        {
            "trace_id": "t1",
            "worker": "food_advisor",
            "agent_result": {
                "status": "completed",
                "worker": "food_decision",
                "trace_id": "t1",
                "final": {"recommendations": [], "followups": [], "warnings": []},
                "diagnostics": {"route": {"worker": "food_advisor"}},
            },
        }
    )

    assert "contract:agent_result_worker_route_mismatch:food_decision!=food_advisor" in violations


def test_evaluate_result_allows_environment_failure_without_masking_route_checks():
    case = {
        "expect": {
            "no_fallback": True,
            "worker": "route_planner",
            "intent_in": ["route"],
        }
    }
    result = {
        "fallback": True,
        "failure_class": "upstream_error",
        "worker": "food_advisor",
        "intent": "eat_out",
        "trace_id": "t1",
        "agent_result": {
            "status": "failed",
            "worker": "food_advisor",
            "trace_id": "t1",
            "failure_class": "upstream_error",
            "final": {"recommendations": [], "followups": [], "warnings": []},
        },
    }

    evaluation = evaluate_result(case, result, allowed_environment_failures={"upstream_error"})

    assert "unexpected_fallback:upstream_error" not in evaluation["violations"]
    assert "worker:food_advisor!=expected:route_planner" in evaluation["violations"]
    assert "intent:eat_out not in ['route']" in evaluation["violations"]


def test_evaluate_result_checks_recommendation_titles():
    case = {"expect": {"recommendation_titles_include": ["味汁园"]}}
    result = {
        "trace_id": "t1",
        "worker": "food_advisor",
        "agent_result": {
            "status": "completed",
            "worker": "food_advisor",
            "trace_id": "t1",
            "final": {
                "recommendations": [{"type": "restaurant", "title": "味汁园(惟盛园店)"}],
                "followups": [],
                "warnings": [],
            },
        },
    }

    assert evaluate_result(case, result)["violations"] == []

    failed = evaluate_result({"expect": {"recommendation_titles_include": ["长沙米粉"]}}, result)
    assert "missing_recommendation_titles:长沙米粉" in failed["violations"]


def test_replay_eval_extracts_and_counts_provider_issue():
    payload = {
        "answer": {
            "provider_issue": {
                "category": "provider_auth",
                "code": "subscription_expired",
                "action": "switch_model_or_refresh_provider_subscription",
            }
        }
    }
    issue = extract_provider_issue(payload)
    results = [
        {"provider_issue": issue},
        {
            "turns": [
                {
                    "provider_issue": {
                        "category": "provider_auth",
                        "code": "subscription_expired",
                        "action": "switch_model_or_refresh_provider_subscription",
                    }
                }
            ]
        },
    ]

    assert issue["code"] == "subscription_expired"
    assert provider_issue_counts(results, "code") == {"subscription_expired": 2}
    assert provider_issue_counts(results, "action") == {"switch_model_or_refresh_provider_subscription": 2}


def test_replay_eval_counts_actual_or_requested_model_config():
    results = [
        {"model_config": {"provider_value": "openai:kimi-k2.5", "model_planner": "kimi-k2.5"}},
        {"model_config": {"requested_model_value": "openai:kimi-k2.5"}},
        {"turns": [{"model_config": {"model_planner": "deepseek-v3.2"}}]},
    ]

    assert model_config_counts(results) == {
        "openai:kimi-k2.5": 2,
        "deepseek-v3.2": 1,
    }


def test_replay_eval_counts_active_tools_and_worker_tool_boundaries():
    results = [
        {
            "worker": "food_advisor",
            "active_tools": ["get_ip_location", "search_restaurants"],
            "active_skills": ["food_assistant", "restaurant_finder"],
            "tool_calls": ["get_ip_location", "search_restaurants"],
        },
        {
            "turns": [
                {
                    "worker": "route_planner",
                    "active_tools": ["geocode_location", "plan_route"],
                    "active_skills": ["route_planner"],
                    "tool_calls": [],
                },
                {
                    "worker": "food_advisor",
                    "active_tools": ["search_restaurants"],
                    "active_skills": ["food_assistant"],
                    "tool_calls": ["search_restaurants"],
                },
            ]
        },
    ]

    assert active_value_counts(results, "active_tools") == {
        "search_restaurants": 2,
        "get_ip_location": 1,
        "geocode_location": 1,
        "plan_route": 1,
    }
    assert active_value_counts(results, "active_skills") == {
        "food_assistant": 2,
        "restaurant_finder": 1,
        "route_planner": 1,
    }
    assert active_value_counts(results, "tool_calls") == {
        "search_restaurants": 2,
        "get_ip_location": 1,
    }
    assert worker_tool_counts(results) == {
        "food_advisor": {"search_restaurants": 2, "get_ip_location": 1},
        "route_planner": {"geocode_location": 1, "plan_route": 1},
    }
    assert worker_tool_call_counts(results) == {
        "food_advisor": {"search_restaurants": 2, "get_ip_location": 1},
        "route_planner": {},
    }


def test_evaluate_result_flags_unexpected_tool_calls():
    case = {"expect": {"no_tool_calls": True}}
    result = {
        "fallback": False,
        "worker": "route_planner",
        "intent": "route",
        "trace_id": "t1",
        "tool_calls": ["geocode_location"],
        "agent_result": {
            "status": "needs_clarification",
            "worker": "route_planner",
            "trace_id": "t1",
            "final": {"recommendations": [], "followups": [], "warnings": []},
        },
    }

    evaluation = evaluate_result(case, result)

    assert "unexpected_tool_calls:geocode_location" in evaluation["violations"]


def test_evaluate_result_checks_required_and_excluded_tool_calls():
    case = {
        "expect": {
            "tool_calls_include": ["search_restaurants", "get_ip_location"],
            "tool_calls_exclude": ["memory_search"],
        }
    }
    result = {
        "fallback": False,
        "worker": "food_advisor",
        "intent": "eat_out",
        "trace_id": "t1",
        "tool_calls": ["search_restaurants", "memory_search"],
        "agent_result": {
            "status": "completed",
            "worker": "food_advisor",
            "trace_id": "t1",
            "final": {"recommendations": [], "followups": [], "warnings": []},
        },
    }

    evaluation = evaluate_result(case, result)

    assert "missing_tool_calls:get_ip_location" in evaluation["violations"]
    assert "unexpected_tool_calls:memory_search" in evaluation["violations"]


def test_replay_eval_flags_worker_tool_boundary_leaks():
    result = {
        "worker": "home_chef",
        "active_tools": ["get_fridge_items", "search_restaurants", "memory_search"],
    }

    assert validate_worker_tool_boundary(result) == [
        "tool_boundary:home_chef:unexpected:memory_search,search_restaurants"
    ]
    assert worker_tool_boundary_violations([{"turns": [{**result, "id": "cook-home"}]}]) == [
        {
            "id": "cook-home",
            "worker": "home_chef",
            "active_tools": ["get_fridge_items", "search_restaurants", "memory_search"],
            "violations": ["tool_boundary:home_chef:unexpected:memory_search,search_restaurants"],
        }
    ]


def test_replay_eval_flags_visible_duplicate_phrases():
    result = {
        "answer": {
            "recommendations": [
                {"title": "旅行旅行地图", "reason": "人民广场步行 8 分钟；评分 4.7；人均 人均 88"}
            ]
        }
    }

    assert set(validate_visible_text_quality(result)) == {
        "visible_text:duplicated_phrase:人均 人均",
        "visible_text:duplicated_phrase:旅行旅行",
    }


def test_replay_eval_classifies_rate_limit_as_provider_issue():
    issue = classify_http_provider_issue(429)

    assert issue["category"] == "provider_rate_limit"
    assert issue["code"] == "rate_limited"
    assert issue["action"] == "wait_or_reduce_live_replay_rate"


def test_replay_eval_classifies_timeout_as_provider_issue():
    issue = classify_request_provider_issue("timeout")

    assert issue["category"] == "provider_timeout"
    assert issue["code"] == "request_timeout"
    assert issue["action"] == "wait_or_reduce_live_replay_rate"


def test_evaluate_result_allows_harness_environment_failure():
    case = {"expect": {"no_fallback": True, "worker": "food_advisor", "status_in": ["completed"]}}
    result = {
        "fallback": True,
        "failure_class": "upstream_error",
        "harness_environment_failure": True,
        "environment_failure": True,
    }

    evaluation = evaluate_result(case, result, allowed_environment_failures={"upstream_error"})

    assert evaluation == {"passed": True, "violations": []}


def test_replay_eval_infers_worker_from_expected_intent_for_harness_failures():
    case = {"expect": {"intent_in": ["eat_out", "unknown"]}}

    assert _expected_worker(case, "eat_out") == "food_advisor"


def test_evaluate_result_does_not_mask_tool_boundary_with_environment_failure():
    case = {"expect": {"no_fallback": True, "worker": "home_chef"}}
    result = {
        "fallback": True,
        "failure_class": "upstream_error",
        "worker": "home_chef",
        "intent": "cook_home",
        "trace_id": "t1",
        "active_tools": ["get_fridge_items", "search_restaurants"],
        "agent_result": {
            "status": "failed",
            "worker": "home_chef",
            "trace_id": "t1",
            "failure_class": "upstream_error",
            "final": {"recommendations": [], "followups": [], "warnings": []},
        },
    }

    evaluation = evaluate_result(case, result, allowed_environment_failures={"upstream_error"})

    assert "unexpected_fallback:upstream_error" not in evaluation["violations"]
    assert "tool_boundary:home_chef:unexpected:search_restaurants" in evaluation["violations"]
