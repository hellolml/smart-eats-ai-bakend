from __future__ import annotations

from pathlib import Path

from scripts.agent_quality_loop import (
    LoopConfig,
    apply_suggested_provider_replay_results,
    audit_uncovered_finding_types,
    build_self_test_command,
    collect_provider_config_via_project_python,
    run_suggested_provider_replays,
    select_script_python,
    should_continue,
    suggest_next_actions,
    summarize_iteration,
)


def _config(**overrides):
    values = {
        "profile": "extended",
        "audit": True,
        "db": "local.db",
        "limit_sessions": 30,
        "audit_session_ids": (),
        "live_replay": False,
        "live_replay_base_url": "http://127.0.0.1:8000",
        "out_root": Path("/tmp/quality-loop"),
        "interval_seconds": 0.0,
        "iterations": 1,
        "duration_seconds": None,
        "stop_on_failure": False,
        "fail_on_quality_findings": False,
    }
    values.update(overrides)
    return LoopConfig(**values)


def test_quality_loop_builds_self_test_command_with_audit_and_live_replay():
    command = build_self_test_command(
        _config(live_replay=True, audit_session_ids=("s-dd83",), fail_on_quality_findings=True),
        Path("/tmp/quality-loop/iter_0001"),
    )

    assert "scripts/agent_self_test.py" in command
    assert command[command.index("--profile") + 1] == "extended"
    assert command[command.index("--out-dir") + 1] == "/tmp/quality-loop/iter_0001"
    assert "--audit" in command
    assert command[command.index("--db") + 1] == "local.db"
    assert "--live-replay" in command
    assert command[command.index("--live-replay-base-url") + 1] == "http://127.0.0.1:8000"
    assert "--audit-session-id" in command
    assert command[command.index("--audit-session-id") + 1] == "s-dd83"
    assert "--fail-on-audit-quality-findings" in command


def test_quality_loop_uses_explicit_agent_test_python(monkeypatch):
    monkeypatch.setenv("AGENT_TEST_PYTHON", "/tmp/project-python")

    assert select_script_python() == "/tmp/project-python"
    command = build_self_test_command(_config(), Path("/tmp/quality-loop/iter_0001"))
    assert command[0] == "/tmp/project-python"


def test_quality_loop_suggests_environment_and_quality_actions():
    actions = suggest_next_actions(
        {
            "failed_steps": ["runtime-stream-contract"],
            "quality_finding_count": 2,
            "audit_uncovered_finding_types": ["new_quality_issue"],
            "environment_failure_count": 3,
            "provider_action_counts": {},
            "live_failed": [{"id": "travel"}],
        }
    )

    assert actions == [
        "inspect_failed_self_test_steps",
        "inspect_conversation_audit_quality_findings",
        "add_deterministic_regression_for_audit_findings",
        "check_model_provider_or_runtime_environment",
        "inspect_live_replay_contract_or_route_failures",
    ]


def test_quality_loop_prefers_specific_provider_action():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 3,
            "provider_action_counts": {"switch_model_or_refresh_provider_subscription": 3},
            "live_failed": [],
        }
    )

    assert actions == ["switch_model_or_refresh_provider_subscription"]


def test_quality_loop_uses_live_provider_action_without_audit():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 0,
            "live_environment_failure_count": 3,
            "live_provider_action_counts": {"switch_model_or_refresh_provider_subscription": 3},
            "live_failed": [],
        }
    )

    assert actions == ["switch_model_or_refresh_provider_subscription"]


def test_quality_loop_prefers_provider_health_action():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 3,
            "provider_action_counts": {"generic_old_action": 3},
            "provider_health": {
                "status": "unhealthy",
                "action": "switch_model_or_refresh_provider_subscription",
            },
            "live_failed": [],
        }
    )

    assert actions == ["switch_model_or_refresh_provider_subscription"]


def test_quality_loop_adds_live_replay_action_for_suggested_provider_values():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 3,
            "provider_health": {
                "status": "unhealthy",
                "action": "switch_model_or_refresh_provider_subscription",
            },
            "provider_suggested_provider_values": ["openai:kimi-k2.5"],
            "live_failed": [],
        }
    )

    assert actions == [
        "switch_model_or_refresh_provider_subscription",
        "run_live_replay_against_suggested_provider_values",
    ]


def test_quality_loop_suggests_switch_when_provider_probe_finds_viable_value():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 3,
            "provider_health": {
                "status": "unhealthy",
                "action": "switch_model_or_refresh_provider_subscription",
            },
            "provider_suggested_provider_values": ["openai:kimi-k2.5"],
            "provider_viable_values": ["openai:kimi-k2.5"],
            "live_failed": [],
        }
    )

    assert actions == [
        "switch_model_or_refresh_provider_subscription",
        "run_live_replay_against_suggested_provider_values",
        "switch_default_provider_to_verified_value",
    ]


def test_quality_loop_runs_suggested_provider_replay_matrix(tmp_path, monkeypatch):
    calls = []

    class Completed:
        returncode = 0

    def fake_run(command, text=True):
        calls.append(command)
        out = Path(command[command.index("--out") + 1])
        model_value = command[command.index("--model-value") + 1]
        out.write_text(
            json_for_model(model_value),
            encoding="utf-8",
        )
        return Completed()

    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.run", fake_run)
    report = {"provider_suggested_provider_values": ["openai:kimi-k2.5", "openai:deepseek-v3.2"]}

    results = run_suggested_provider_replays(report, iteration_dir=tmp_path, base_url="http://127.0.0.1:8000")
    updated = apply_suggested_provider_replay_results(
        {
            **report,
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 3,
            "provider_health": {
                "status": "unhealthy",
                "action": "switch_model_or_refresh_provider_subscription",
            },
            "live_failed": [],
        },
        results,
    )

    assert len(calls) == 2
    assert calls[0][calls[0].index("--model-value") + 1] == "openai:kimi-k2.5"
    assert results[0]["environment_failure_count"] == 0
    assert results[1]["environment_failure_count"] == 1
    assert updated["provider_viable_values"] == ["openai:kimi-k2.5"]
    assert "switch_default_provider_to_verified_value" in updated["next_actions"]


def json_for_model(model_value: str) -> str:
    environment_failure_count = 0 if model_value == "openai:kimi-k2.5" else 1
    return (
        "{"
        f'"pass_rate": 1.0, "fallback_count": 0, "environment_failure_count": {environment_failure_count},'
        '"provider_issue_counts": {}, "provider_action_counts": {}, "worker_tool_boundary_violations": []'
        "}"
    )


def test_quality_loop_summary_includes_provider_health(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "scripts.agent_quality_loop.summarize_provider_health",
        lambda reports, provider_config=None: {
            "status": "unhealthy",
            "action": "switch_model_or_refresh_provider_subscription",
            "issue_counts": reports[0]["provider_issue_counts"],
            "provider_config": provider_config,
            "suggested_provider_values": ["openai:kimi-k2.5"],
        },
    )
    monkeypatch.setattr(
        "scripts.agent_quality_loop.collect_provider_config_via_project_python",
        lambda: {"provider": "openai", "api_key_set": True},
    )
    iteration_dir = tmp_path / "iter"
    iteration_dir.mkdir()
    (iteration_dir / "summary.json").write_text(
        """
        {
          "results": [
            {
              "name": "conversation-audit",
              "environment_failure_count": 2,
              "quality_finding_count": 1,
              "findings_by_type": {"food_affirmation_mode_drift": 1},
              "provider_issue_counts": {"subscription_expired": 2},
              "provider_action_counts": {"switch_model_or_refresh_provider_subscription": 2}
            },
            {
              "name": "deterministic-replay",
              "pass_rate": 1.0,
              "fallback_count": 0,
              "active_tool_counts": {"search_restaurants": 1},
              "active_skill_counts": {"food_assistant": 1},
              "tool_call_counts": {"search_restaurants": 1},
              "worker_tool_counts": {"food_advisor": {"search_restaurants": 1}},
              "worker_tool_call_counts": {"food_advisor": {"search_restaurants": 1}},
              "worker_tool_boundary_violations": [],
              "coverage": {
                "passed": true,
                "observed": {
                  "workers": ["food_advisor"],
                  "quality_issue_regressions": ["food_affirmation_mode_drift"]
                },
                "missing": {}
              }
            },
            {
              "name": "live-replay",
              "active_tool_counts": {"search_restaurants": 3},
              "active_skill_counts": {"food_assistant": 3},
              "tool_call_counts": {"search_restaurants": 2},
              "worker_tool_counts": {"food_advisor": {"search_restaurants": 3}},
              "worker_tool_call_counts": {"food_advisor": {"search_restaurants": 2}},
              "worker_tool_boundary_violations": []
            }
          ],
          "failed": []
        }
        """,
        encoding="utf-8",
    )

    report = summarize_iteration(1, iteration_dir, 0, 123)

    assert report["provider_health"]["status"] == "unhealthy"
    assert report["provider_health"]["issue_counts"] == {"subscription_expired": 2}
    assert report["provider_health"]["provider_config"] == {"provider": "openai", "api_key_set": True}
    assert report["provider_suggested_provider_values"] == ["openai:kimi-k2.5"]
    assert report["deterministic_pass_rate"] == 1.0
    assert report["deterministic_tool_call_counts"] == {"search_restaurants": 1}
    assert report["deterministic_worker_tool_call_counts"] == {"food_advisor": {"search_restaurants": 1}}
    assert report["deterministic_coverage_passed"] is True
    assert report["deterministic_coverage"]["observed"]["workers"] == ["food_advisor"]
    assert report["deterministic_quality_issue_regressions"] == ["food_affirmation_mode_drift"]
    assert report["audit_uncovered_finding_types"] == []
    assert report["live_active_tool_counts"] == {"search_restaurants": 3}
    assert report["live_tool_call_counts"] == {"search_restaurants": 2}
    assert report["live_worker_tool_counts"] == {"food_advisor": {"search_restaurants": 3}}
    assert report["live_worker_tool_call_counts"] == {"food_advisor": {"search_restaurants": 2}}
    assert report["live_worker_tool_boundary_violations"] == []
    assert report["next_actions"] == [
        "inspect_conversation_audit_quality_findings",
        "rerun_or_archive_historical_audit_findings",
        "switch_model_or_refresh_provider_subscription",
        "run_live_replay_against_suggested_provider_values",
    ]


def test_quality_loop_suggests_worker_tool_boundary_action():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 0,
            "live_failed": [],
            "live_worker_tool_boundary_violations": [{"id": "cook-home"}],
        }
    )

    assert actions == ["inspect_worker_tool_boundary_violations"]


def test_quality_loop_suggests_expanding_deterministic_coverage():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 0,
            "live_failed": [],
            "deterministic_coverage_passed": False,
        }
    )

    assert actions == ["expand_deterministic_replay_coverage"]


def test_quality_loop_maps_audit_findings_to_regression_coverage():
    report = {
        "audit_findings_by_type": {
            "food_affirmation_mode_drift": 2,
            "route_memory_tool_leak": 1,
            "environment_failure": 4,
        },
        "deterministic_quality_issue_regressions": ["food_affirmation_mode_drift"],
    }

    assert audit_uncovered_finding_types(report) == ["route_memory_tool_leak"]
    assert suggest_next_actions(
        {
            **report,
            "failed_steps": [],
            "quality_finding_count": 3,
            "environment_failure_count": 0,
            "live_failed": [],
            "audit_uncovered_finding_types": ["route_memory_tool_leak"],
        }
    ) == [
        "inspect_conversation_audit_quality_findings",
        "add_deterministic_regression_for_audit_findings",
    ]


def test_quality_loop_collects_provider_config_with_project_python(monkeypatch):
    class Completed:
        returncode = 0
        stdout = '{"provider": "openai", "api_key_set": true}'

    monkeypatch.setattr("scripts.agent_quality_loop.select_script_python", lambda: "/tmp/python")
    monkeypatch.setattr("subprocess.run", lambda *args, **kwargs: Completed())

    assert collect_provider_config_via_project_python() == {"provider": "openai", "api_key_set": True}


def test_quality_loop_suggests_expanding_cases_when_clean():
    assert suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 0,
            "live_failed": [],
        }
    ) == ["expand_replay_cases_or_start_next_quality_iteration"]


def test_quality_loop_stops_after_configured_iterations():
    assert should_continue(0.0, 0, _config(iterations=1)) is True
    assert should_continue(0.0, 1, _config(iterations=1)) is False
