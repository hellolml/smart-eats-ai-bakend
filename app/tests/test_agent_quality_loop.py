from __future__ import annotations

from pathlib import Path

from scripts.agent_quality_loop import (
    LoopConfig,
    apply_suggested_provider_replay_results,
    audit_quality_action_counts,
    audit_uncovered_finding_types,
    build_dialogue_probe_command,
    build_managed_server_command,
    build_self_test_command,
    combine_iteration_returncode,
    collect_provider_config_via_project_python,
    provider_config_from_dialogue_probe_report,
    run_dialogue_probe,
    run_suggested_provider_replays,
    select_script_python,
    self_test_env,
    should_continue,
    suggest_next_actions,
    summarize_dialogue_probe_report,
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
        "dialogue_probe": False,
        "dialogue_probe_base_url": "http://127.0.0.1:8000",
        "dialogue_probe_model_value": None,
        "dialogue_probe_scenario_ids": (),
        "dialogue_probe_timeout_seconds": 150.0,
        "dialogue_probe_request_delay_seconds": 1.0,
        "dialogue_probe_persist_wait_seconds": 20.0,
        "dialogue_probe_persist_poll_seconds": 0.25,
        "dialogue_probe_continue_on_provider_unavailable": False,
        "dialogue_probe_skip_provider_preflight": False,
        "dialogue_probe_managed_server": False,
        "dialogue_probe_managed_host": "127.0.0.1",
        "dialogue_probe_managed_port": 8031,
        "dialogue_probe_managed_startup_timeout_seconds": 90.0,
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


def test_quality_loop_builds_dialogue_probe_command():
    command = build_dialogue_probe_command(
        _config(
            dialogue_probe=True,
            dialogue_probe_model_value="openai:deepseek-v4-flash",
            dialogue_probe_scenario_ids=("food_restaurant_context_long", "travel_chengdu_three_day_long"),
            dialogue_probe_continue_on_provider_unavailable=True,
        ),
        Path("/tmp/quality-loop/iter_0001"),
    )

    assert "scripts/agent_dialogue_quality_probe.py" in command
    assert command[command.index("--base-url") + 1] == "http://127.0.0.1:8000"
    assert command[command.index("--model-value") + 1] == "openai:deepseek-v4-flash"
    assert command[command.index("--persist-wait-seconds") + 1] == "20.0"
    assert command[command.index("--persist-poll-seconds") + 1] == "0.25"
    assert command.count("--scenario-id") == 2
    assert "--continue-on-provider-unavailable" in command


def test_quality_loop_builds_dialogue_probe_command_with_skip_preflight():
    command = build_dialogue_probe_command(
        _config(
            dialogue_probe=True,
            dialogue_probe_model_value="openai:gpt-5.4-mini",
            dialogue_probe_skip_provider_preflight=True,
        ),
        Path("/tmp/quality-loop/iter_0001"),
    )

    assert command[command.index("--model-value") + 1] == "openai:gpt-5.4-mini"
    assert "--skip-provider-preflight" in command


def test_quality_loop_builds_managed_server_command():
    command = build_managed_server_command(
        _config(
            dialogue_probe_managed_server=True,
            dialogue_probe_managed_host="127.0.0.1",
            dialogue_probe_managed_port=8123,
        )
    )

    assert command[1:] == [
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        "127.0.0.1",
        "--port",
        "8123",
    ]


def test_quality_loop_uses_explicit_agent_test_python(monkeypatch):
    monkeypatch.setenv("AGENT_TEST_PYTHON", "/tmp/project-python")

    assert select_script_python() == "/tmp/project-python"
    command = build_self_test_command(_config(), Path("/tmp/quality-loop/iter_0001"))
    assert command[0] == "/tmp/project-python"


def test_quality_loop_self_test_env_isolates_database(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://prod.example/smart_eats")
    monkeypatch.setenv("EVAL_DATABASE_URL", "postgresql+asyncpg://prod.example/eval")

    env = self_test_env(Path("/tmp/quality-loop/iter_0001"))

    assert env["DATABASE_URL"] == "sqlite+aiosqlite:///:memory:"
    assert env["EVAL_DATABASE_URL"] == "sqlite+aiosqlite:///:memory:"
    assert env["LANGGRAPH_STORE_BACKEND"] == "memory"
    assert "/tmp/quality-loop/iter_0001" == env["AGENT_QUALITY_LOOP_ITERATION_DIR"]


def test_quality_loop_self_test_env_allows_explicit_database_override(monkeypatch):
    monkeypatch.setenv("AGENT_QUALITY_SELF_TEST_DATABASE_URL", "sqlite+aiosqlite:///./tmp-test.db")
    monkeypatch.setenv("AGENT_QUALITY_SELF_TEST_EVAL_DATABASE_URL", "sqlite+aiosqlite:///./tmp-eval.db")

    env = self_test_env(Path("/tmp/quality-loop/iter_0001"))

    assert env["DATABASE_URL"] == "sqlite+aiosqlite:///./tmp-test.db"
    assert env["EVAL_DATABASE_URL"] == "sqlite+aiosqlite:///./tmp-eval.db"


def test_quality_loop_suggests_environment_and_quality_actions():
    actions = suggest_next_actions(
        {
            "failed_steps": ["runtime-stream-contract"],
            "quality_finding_count": 2,
            "audit_findings_by_type": {
                "restaurant_route_context_loss": 1,
                "travel_itinerary_day_mismatch": 1,
            },
            "audit_uncovered_finding_types": ["new_quality_issue"],
            "environment_failure_count": 3,
            "provider_action_counts": {},
            "live_failed": [{"id": "travel"}],
        }
    )

    assert actions == [
        "inspect_failed_self_test_steps",
        "inspect_conversation_audit_quality_findings",
        "fix_restaurant_context_carry_and_route_reference",
        "fix_travel_workflow_state_extraction",
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


def test_quality_loop_summarizes_dialogue_probe_provider_unavailable():
    summary = summarize_dialogue_probe_report(
        {
            "total_planned": 16,
            "total": 1,
            "passed_count": 0,
            "pass_rate": None,
            "provider_unavailable": {
                "kind": "provider_unavailable",
                "http_status": 402,
                "code": "provider_billing_unavailable",
            },
            "quality_metrics": {
                "provider_blocked": True,
                "turn_pass_rate": None,
                "dimension_scores": {"provider_availability": None},
                "dimension_failed_turn_counts": {"provider_availability": 1}
            },
            "failed": [],
        }
    )

    assert summary["total_planned"] == 16
    assert summary["turn_pass_rate"] is None
    assert summary["dimension_scores"] == {"provider_availability": None}
    assert summary["dimension_failed_turn_counts"] == {"provider_availability": 1}
    assert summary["provider_unavailable_code"] == "provider_billing_unavailable"
    assert summary["provider_unavailable_http_status"] == 402
    assert summary["failed_count"] == 0


def test_quality_loop_extracts_dialogue_probe_runtime_provider_config():
    config = provider_config_from_dialogue_probe_report(
        {
            "results": [
                {
                    "turns": [
                        {
                            "agent_result": {
                                "diagnostics": {
                                    "model_config": {
                                        "provider_value": "deepseek:deepseek-v4-flash",
                                        "provider": "deepseek",
                                        "model_planner": "deepseek-v4-flash",
                                        "model_writer": "deepseek-v4-flash",
                                    }
                                }
                            }
                        }
                    ]
                }
            ]
        }
    )

    assert config == {
        "source": "dialogue_probe_runtime",
        "configured_provider": "deepseek:deepseek-v4-flash",
        "enabled_providers": ["deepseek"],
        "configured_models": ["deepseek:deepseek-v4-flash"],
        "provider": "deepseek",
        "model_planner": "deepseek-v4-flash",
        "model_writer": "deepseek-v4-flash",
        "api_key_set": None,
    }


def test_quality_loop_combined_returncode_allows_provider_unavailable():
    assert combine_iteration_returncode(
        0,
        {
            "returncode": 2,
            "provider_unavailable": {"code": "provider_billing_unavailable", "http_status": 402},
            "failed_count": 0,
        },
    ) == 0


def test_quality_loop_combined_returncode_fails_dialogue_quality_regression():
    assert combine_iteration_returncode(
        0,
        {
            "returncode": 1,
            "provider_unavailable": None,
            "failed_count": 2,
        },
    ) == 1


def test_quality_loop_run_dialogue_probe_writes_step_summary(tmp_path, monkeypatch):
    class Completed:
        returncode = 1

    def fake_run(command, text=True):
        out = Path(command[command.index("--out") + 1])
        out.write_text(
            """
            {
              "total_planned": 1,
              "total": 1,
              "passed_count": 0,
              "pass_rate": 0.0,
              "failed": [{"id": "context-loss", "violations": ["worker_mismatch"]}]
            }
            """,
            encoding="utf-8",
        )
        return Completed()

    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.run", fake_run)

    summary = run_dialogue_probe(_config(dialogue_probe=True), tmp_path)
    step = (tmp_path / "dialogue_probe_step.json").read_text(encoding="utf-8")

    assert summary["returncode"] == 1
    assert summary["failed_count"] == 1
    assert "context-loss" in step


def test_quality_loop_managed_dialogue_probe_preflight_blocker_does_not_start_server(tmp_path, monkeypatch):
    def fake_preflight(_model_value, *, timeout_seconds):
        return {
            "kind": "provider_unavailable",
            "http_status": 402,
            "code": "provider_billing_unavailable",
            "action": "recharge_provider_or_switch_model",
        }

    def fail_popen(*_args, **_kwargs):
        raise AssertionError("managed server should not start when provider preflight is blocked")

    monkeypatch.setattr("scripts.agent_quality_loop.preflight_dialogue_provider", fake_preflight)
    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.Popen", fail_popen)

    summary = run_dialogue_probe(
        _config(
            dialogue_probe=True,
            dialogue_probe_managed_server=True,
            dialogue_probe_model_value="deepseek:deepseek-v4-flash",
            dialogue_probe_scenario_ids=("travel_revision_city_swap_resume_map_long",),
        ),
        tmp_path,
    )
    report = (tmp_path / "dialogue_probe_report.json").read_text(encoding="utf-8")

    assert summary["returncode"] == 2
    assert summary["total"] == 0
    assert summary["provider_unavailable_code"] == "provider_billing_unavailable"
    assert '"attempted_turns": 0' in report


def test_quality_loop_managed_dialogue_probe_starts_and_stops_server(tmp_path, monkeypatch):
    calls = {"popen": 0, "terminate": 0, "wait": 0}

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            calls["terminate"] += 1

        def wait(self, timeout=None):
            calls["wait"] += 1
            return 0

    class Completed:
        returncode = 0

    def fake_run(command, text=True):
        out = Path(command[command.index("--out") + 1])
        out.write_text(
            """
            {
              "total_planned": 1,
              "total": 1,
              "passed_count": 1,
              "pass_rate": 1.0,
              "quality_metrics": {"provider_blocked": false},
              "failed": []
            }
            """,
            encoding="utf-8",
        )
        return Completed()

    def fake_popen(command, **_kwargs):
        calls["popen"] += 1
        assert command[1:] == [
            "-m",
            "uvicorn",
            "app.main:app",
            "--host",
            "127.0.0.1",
            "--port",
            "8124",
        ]
        return FakeProcess()

    monkeypatch.setattr("scripts.agent_quality_loop.preflight_dialogue_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("scripts.agent_quality_loop.wait_for_managed_server_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.Popen", fake_popen)
    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.run", fake_run)

    summary = run_dialogue_probe(
        _config(
            dialogue_probe=True,
            dialogue_probe_managed_server=True,
            dialogue_probe_managed_port=8124,
        ),
        tmp_path,
    )

    assert summary["returncode"] == 0
    assert summary["pass_rate"] == 1.0
    assert calls == {"popen": 1, "terminate": 1, "wait": 1}


def test_quality_loop_managed_dialogue_probe_can_skip_preflight(tmp_path, monkeypatch):
    calls = {"preflight": 0, "popen": 0}

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    class Completed:
        returncode = 0

    def fake_preflight(*_args, **_kwargs):
        calls["preflight"] += 1
        raise AssertionError("preflight should be skipped")

    def fake_run(command, text=True):
        out = Path(command[command.index("--out") + 1])
        out.write_text(
            """
            {
              "total_planned": 1,
              "total": 1,
              "passed_count": 1,
              "pass_rate": 1.0,
              "quality_metrics": {"provider_blocked": false},
              "failed": []
            }
            """,
            encoding="utf-8",
        )
        return Completed()

    def fake_popen(*_args, **_kwargs):
        calls["popen"] += 1
        return FakeProcess()

    monkeypatch.setattr("scripts.agent_quality_loop.preflight_dialogue_provider", fake_preflight)
    monkeypatch.setattr("scripts.agent_quality_loop.wait_for_managed_server_ready", lambda *_args, **_kwargs: None)
    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.Popen", fake_popen)
    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.run", fake_run)

    summary = run_dialogue_probe(
        _config(
            dialogue_probe=True,
            dialogue_probe_managed_server=True,
            dialogue_probe_skip_provider_preflight=True,
        ),
        tmp_path,
    )

    assert summary["returncode"] == 0
    assert summary["pass_rate"] == 1.0
    assert calls == {"preflight": 0, "popen": 1}


def test_quality_loop_dialogue_probe_runtime_error_writes_report(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.agent_quality_loop.preflight_dialogue_provider", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        "scripts.agent_quality_loop.wait_for_managed_server_ready",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("port busy")),
    )

    class FakeProcess:
        def poll(self):
            return None

        def terminate(self):
            return None

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr("scripts.agent_quality_loop.subprocess.Popen", lambda *_args, **_kwargs: FakeProcess())

    summary = run_dialogue_probe(
        _config(dialogue_probe=True, dialogue_probe_managed_server=True),
        tmp_path,
    )

    assert summary["returncode"] == 1
    assert summary["failed_count"] == 1
    assert summary["failed"][0]["id"] == "dialogue_probe_runtime"
    assert "port busy" in (tmp_path / "dialogue_probe_report.json").read_text(encoding="utf-8")


def test_quality_loop_suggests_dialogue_probe_failure_action():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 0,
            "live_failed": [],
            "dialogue_probe": {
                "provider_unavailable": None,
                "failed_count": 2,
            },
        }
    )

    assert actions == ["inspect_dialogue_probe_context_or_quality_failures"]


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


def test_quality_loop_prefers_dialogue_probe_billing_action_over_generic_provider_action():
    actions = suggest_next_actions(
        {
            "failed_steps": [],
            "quality_finding_count": 0,
            "environment_failure_count": 3,
            "provider_health": {
                "status": "unhealthy",
                "action": "inspect_provider_error_and_model_config",
            },
            "dialogue_probe": {
                "provider_unavailable": {
                    "code": "provider_billing_unavailable",
                    "http_status": 402,
                },
                "failed_count": 0,
            },
            "live_failed": [],
        }
    )

    assert actions == ["recharge_provider_or_switch_model"]


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
    (iteration_dir / "dialogue_probe_report.json").write_text(
        """
        {
          "total_planned": 16,
          "total": 1,
          "passed_count": 0,
          "pass_rate": null,
          "provider_unavailable": {
            "kind": "provider_unavailable",
            "http_status": 402,
            "code": "provider_billing_unavailable"
          },
          "quality_metrics": {
            "provider_blocked": true,
            "turn_pass_rate": null,
            "dimension_scores": {"provider_availability": null},
            "dimension_failed_turn_counts": {"provider_availability": 1}
          },
          "failed": []
        }
        """,
        encoding="utf-8",
    )
    (iteration_dir / "dialogue_probe_step.json").write_text(
        """
        {
          "name": "dialogue-probe",
          "returncode": 2,
          "elapsed_ms": 123,
          "out": "dialogue_probe_report.json"
        }
        """,
        encoding="utf-8",
    )

    report = summarize_iteration(1, iteration_dir, 0, 123)

    assert report["provider_health"]["status"] == "unhealthy"
    assert report["provider_health"]["issue_counts"] == {"subscription_expired": 2}
    assert report["provider_health"]["provider_config"] == {"provider": "openai", "api_key_set": True}
    assert report["provider_suggested_provider_values"] == ["openai:kimi-k2.5"]
    assert report["audit_quality_action_counts"] == {"fix_food_followup_intent_and_tool_policy": 1}
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
    assert report["dialogue_probe"]["total_planned"] == 16
    assert report["dialogue_probe"]["returncode"] == 2
    assert report["dialogue_probe"]["provider_unavailable_code"] == "provider_billing_unavailable"
    assert report["dialogue_probe"]["dimension_failed_turn_counts"]["provider_availability"] == 1
    assert report["returncode"] == 0
    assert report["self_test_returncode"] == 0
    assert report["next_actions"] == [
        "inspect_conversation_audit_quality_findings",
        "fix_food_followup_intent_and_tool_policy",
        "rerun_or_archive_historical_audit_findings",
        "switch_model_or_refresh_provider_subscription",
        "run_live_replay_against_suggested_provider_values",
    ]


def test_quality_loop_summary_fails_on_dialogue_probe_regression(tmp_path, monkeypatch):
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
              "name": "deterministic-replay",
              "pass_rate": 1.0,
              "fallback_count": 0,
              "worker_tool_boundary_violations": [],
              "coverage": {"passed": true, "observed": {}, "missing": {}}
            }
          ],
          "failed": []
        }
        """,
        encoding="utf-8",
    )
    (iteration_dir / "dialogue_probe_report.json").write_text(
        """
        {
          "total_planned": 2,
          "total": 2,
          "passed_count": 1,
          "pass_rate": 0.5,
          "quality_metrics": {
            "provider_blocked": false,
            "turn_pass_rate": 0.5,
            "dimension_scores": {"route_accuracy": 0.5},
            "dimension_failed_turn_counts": {"route_accuracy": 1}
          },
          "failed": [{"id": "travel-context-loss", "violations": ["context_lost"]}]
        }
        """,
        encoding="utf-8",
    )
    (iteration_dir / "dialogue_probe_step.json").write_text(
        """
        {
          "name": "dialogue-probe",
          "returncode": 1,
          "elapsed_ms": 456,
          "out": "dialogue_probe_report.json"
        }
        """,
        encoding="utf-8",
    )

    report = summarize_iteration(1, iteration_dir, 0, 123)

    assert report["returncode"] == 1
    assert report["self_test_returncode"] == 0
    assert "dialogue-probe" in report["failed_steps"]
    assert report["dialogue_probe"]["failed_count"] == 1
    assert report["dialogue_probe"]["turn_pass_rate"] == 0.5
    assert report["dialogue_probe"]["dimension_scores"] == {"route_accuracy": 0.5}
    assert "inspect_dialogue_probe_context_or_quality_failures" in report["next_actions"]


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
            "environment_missing_assistant_response": 5,
            "incomplete_session_without_agent_output": 6,
            "overlapping_user_turn_before_assistant": 7,
        },
        "deterministic_quality_issue_regressions": ["food_affirmation_mode_drift"],
    }

    assert audit_uncovered_finding_types(report) == ["route_memory_tool_leak"]
    assert audit_quality_action_counts(report["audit_findings_by_type"]) == {
        "fix_food_followup_intent_and_tool_policy": 2,
        "fix_route_worker_tool_policy": 1,
    }
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
        "fix_food_followup_intent_and_tool_policy",
        "fix_route_worker_tool_policy",
        "add_deterministic_regression_for_audit_findings",
    ]


def test_quality_loop_groups_context_audit_findings_into_specific_actions():
    findings = {
        "restaurant_selection_context_loss": 2,
        "restaurant_route_context_loss": 1,
        "restaurant_selection_ack": 1,
        "travel_trip_meta_missing": 2,
        "travel_itinerary_day_mismatch": 1,
        "travel_prompt_text_extracted_as_poi": 1,
        "unknown_quality_issue": 1,
        "environment_failure": 9,
    }

    assert audit_quality_action_counts(findings) == {
        "fix_restaurant_context_carry_and_route_reference": 3,
        "fix_travel_workflow_state_extraction": 3,
        "fix_restaurant_context_carry_and_selection_ack": 1,
        "fix_travel_source_ingestion_prompt_filtering": 1,
        "inspect_unclassified_audit_quality_finding": 1,
    }


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
