#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from scripts.agent_provider_health import summarize_provider_health
from scripts.agent_dialogue_quality_probe import (
    SCENARIOS as DIALOGUE_SCENARIOS,
    aggregate_quality_metrics as aggregate_dialogue_quality_metrics,
    preflight_provider as preflight_dialogue_provider,
)


DIALOGUE_PROVIDER_UNAVAILABLE_EXIT_CODE = 2
ENVIRONMENT_AUDIT_FINDING_TYPES = {
    "environment_failure",
    "environment_missing_assistant_response",
    "incomplete_session_without_agent_output",
    "overlapping_user_turn_before_assistant",
}
AUDIT_FINDING_ACTIONS = {
    "food_affirmation_mode_drift": "fix_food_followup_intent_and_tool_policy",
    "restaurant_selection_ack": "fix_restaurant_context_carry_and_selection_ack",
    "restaurant_selection_context_loss": "fix_restaurant_context_carry_and_route_reference",
    "restaurant_route_context_loss": "fix_restaurant_context_carry_and_route_reference",
    "route_memory_tool_leak": "fix_route_worker_tool_policy",
    "travel_itinerary_day_mismatch": "fix_travel_workflow_state_extraction",
    "travel_prompt_text_extracted_as_poi": "fix_travel_source_ingestion_prompt_filtering",
    "travel_revision_context_stale": "fix_travel_revision_context_reset",
    "travel_tool_explosion": "fix_travel_tool_budget_and_candidate_dedup",
    "travel_trip_meta_missing": "fix_travel_workflow_state_extraction",
}


@dataclass(frozen=True)
class LoopConfig:
    profile: str
    audit: bool
    db: str
    limit_sessions: int
    audit_session_ids: tuple[str, ...]
    live_replay: bool
    live_replay_base_url: str
    dialogue_probe: bool
    dialogue_probe_base_url: str
    dialogue_probe_model_value: str | None
    dialogue_probe_scenario_ids: tuple[str, ...]
    dialogue_probe_timeout_seconds: float
    dialogue_probe_request_delay_seconds: float
    dialogue_probe_persist_wait_seconds: float
    dialogue_probe_persist_poll_seconds: float
    dialogue_probe_continue_on_provider_unavailable: bool
    dialogue_probe_skip_provider_preflight: bool
    dialogue_probe_managed_server: bool
    dialogue_probe_managed_host: str
    dialogue_probe_managed_port: int
    dialogue_probe_managed_startup_timeout_seconds: float
    out_root: Path
    interval_seconds: float
    iterations: int | None
    duration_seconds: float | None
    stop_on_failure: bool
    fail_on_quality_findings: bool


def build_self_test_command(config: LoopConfig, iteration_dir: Path) -> list[str]:
    command = [
        select_script_python(),
        "scripts/agent_self_test.py",
        "--profile",
        config.profile,
        "--out-dir",
        str(iteration_dir),
    ]
    if config.audit:
        command.extend(
            [
                "--audit",
                "--db",
                config.db,
                "--limit-sessions",
                str(config.limit_sessions),
            ]
        )
        for session_id in config.audit_session_ids:
            command.extend(["--audit-session-id", session_id])
        if config.fail_on_quality_findings:
            command.append("--fail-on-audit-quality-findings")
    if config.live_replay:
        command.extend(
            [
                "--live-replay",
                "--live-replay-base-url",
                config.live_replay_base_url,
            ]
        )
    return command


def build_dialogue_probe_command(config: LoopConfig, iteration_dir: Path) -> list[str]:
    out = iteration_dir / "dialogue_probe_report.json"
    command = [
        select_script_python(),
        "scripts/agent_dialogue_quality_probe.py",
        "--base-url",
        config.dialogue_probe_base_url,
        "--out",
        str(out),
        "--timeout-seconds",
        str(config.dialogue_probe_timeout_seconds),
        "--request-delay-seconds",
        str(config.dialogue_probe_request_delay_seconds),
        "--persist-wait-seconds",
        str(config.dialogue_probe_persist_wait_seconds),
        "--persist-poll-seconds",
        str(config.dialogue_probe_persist_poll_seconds),
    ]
    if config.dialogue_probe_model_value:
        command.extend(["--model-value", config.dialogue_probe_model_value])
    for scenario_id in config.dialogue_probe_scenario_ids:
        command.extend(["--scenario-id", scenario_id])
    if config.dialogue_probe_continue_on_provider_unavailable:
        command.append("--continue-on-provider-unavailable")
    if config.dialogue_probe_skip_provider_preflight:
        command.append("--skip-provider-preflight")
    return command


def select_script_python() -> str:
    override = os.getenv("AGENT_TEST_PYTHON")
    if override:
        return override
    pytest_bin = os.getenv("PYTEST_BIN")
    if pytest_bin:
        parts = shlex.split(pytest_bin)
        if parts:
            candidate = _python_from_shebang(Path(parts[0]))
            if candidate:
                return candidate
    found_pytest = shutil.which("pytest")
    if found_pytest:
        candidate = _python_from_shebang(Path(found_pytest))
        if candidate:
            return candidate
    return sys.executable


def _python_from_shebang(path: Path) -> str | None:
    try:
        first_line = path.read_text(encoding="utf-8", errors="ignore").splitlines()[0]
    except (OSError, IndexError):
        return None
    if not first_line.startswith("#!"):
        return None
    command = first_line[2:].strip()
    if not command or command.startswith("/usr/bin/env "):
        return None
    executable = command.split(" ", 1)[0]
    return executable if Path(executable).exists() else None


def load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def collect_provider_config_via_project_python() -> dict[str, Any] | None:
    env = dict(os.environ)
    env["PYTHONPATH"] = _prepend_pythonpath(".", env.get("PYTHONPATH"))
    try:
        completed = subprocess.run(
            [select_script_python(), "scripts/agent_provider_health.py", "--config-only"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if completed.returncode != 0:
        return None
    try:
        data = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _prepend_pythonpath(value: str, existing: str | None) -> str:
    return f"{value}{os.pathsep}{existing}" if existing else value


def run_suggested_provider_replays(
    report: dict[str, Any],
    *,
    iteration_dir: Path,
    base_url: str,
    limit: int = 4,
) -> list[dict[str, Any]]:
    suggestions = report.get("provider_suggested_provider_values")
    if not isinstance(suggestions, list) or not suggestions:
        return []
    results: list[dict[str, Any]] = []
    for index, value in enumerate([item for item in suggestions if isinstance(item, str) and item.strip()][:limit], start=1):
        safe_name = _safe_filename(value)
        out = iteration_dir / f"suggested_provider_replay_{index:02d}_{safe_name}.json"
        command = [
            select_script_python(),
            "scripts/replay_eval.py",
            "--base-url",
            base_url,
            "--out",
            str(out),
            "--model-value",
            value,
            "--allow-environment-failure-class",
            "upstream_error",
            "--max-cases",
            "5",
            "--timeout-seconds",
            "15",
            "--request-delay-seconds",
            "0.25",
        ]
        print(f"\n== suggested-provider-replay {value} ==")
        print(" ".join(command))
        completed = subprocess.run(command, text=True)
        item: dict[str, Any] = {
            "model_value": value,
            "returncode": completed.returncode,
            "out": str(out),
        }
        if out.exists():
            replay = load_json(out)
            item.update(
                {
                    "pass_rate": replay.get("pass_rate"),
                    "fallback_count": replay.get("fallback_count"),
                    "environment_failure_count": replay.get("environment_failure_count"),
                    "provider_issue_counts": replay.get("provider_issue_counts"),
                    "provider_action_counts": replay.get("provider_action_counts"),
                    "model_config_counts": replay.get("model_config_counts"),
                    "worker_tool_boundary_violations": replay.get("worker_tool_boundary_violations"),
                }
            )
        results.append(item)
    aggregate = iteration_dir / "suggested_provider_replays.json"
    aggregate.write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
    return results


def run_dialogue_probe(config: LoopConfig, iteration_dir: Path) -> dict[str, Any]:
    command = build_dialogue_probe_command(config, iteration_dir)
    out = iteration_dir / "dialogue_probe_report.json"
    print("\n== dialogue-probe ==")
    print(" ".join(command))
    started = time.monotonic()
    try:
        if config.dialogue_probe_managed_server:
            completed = run_dialogue_probe_with_managed_server(config, iteration_dir, command)
        else:
            completed = subprocess.run(command, text=True)
    except Exception as exc:
        write_dialogue_runtime_error_report(config, iteration_dir, exc)
        completed = subprocess.CompletedProcess(command, 1)
    elapsed_ms = int((time.monotonic() - started) * 1000)
    summary = summarize_dialogue_probe_report(load_json(out))
    summary.update(
        {
            "name": "dialogue-probe",
            "returncode": completed.returncode,
            "elapsed_ms": elapsed_ms,
            "out": str(out),
        }
    )
    (iteration_dir / "dialogue_probe_step.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def run_dialogue_probe_with_managed_server(
    config: LoopConfig,
    iteration_dir: Path,
    command: list[str],
) -> subprocess.CompletedProcess:
    if not config.dialogue_probe_skip_provider_preflight:
        preflight = preflight_dialogue_provider(
            config.dialogue_probe_model_value,
            timeout_seconds=min(config.dialogue_probe_timeout_seconds, 30.0),
        )
        if preflight:
            write_dialogue_preflight_blocker_report(config, iteration_dir, preflight)
            return subprocess.CompletedProcess(command, DIALOGUE_PROVIDER_UNAVAILABLE_EXIT_CODE)

    server_command = build_managed_server_command(config)
    server_log = iteration_dir / "dialogue_probe_server.log"
    print("\n== dialogue-probe-managed-server ==")
    print(" ".join(server_command))
    with server_log.open("w", encoding="utf-8") as log_file:
        process = subprocess.Popen(
            server_command,
            stdout=log_file,
            stderr=subprocess.STDOUT,
            text=True,
            env=managed_server_env(),
        )
        try:
            wait_for_managed_server_ready(
                config.dialogue_probe_base_url,
                timeout_seconds=config.dialogue_probe_managed_startup_timeout_seconds,
            )
            return subprocess.run(command, text=True)
        finally:
            stop_managed_server(process)


def write_dialogue_preflight_blocker_report(
    config: LoopConfig,
    iteration_dir: Path,
    provider_unavailable: dict[str, Any],
) -> None:
    total_planned = dialogue_scenario_count(config)
    report = {
        "total_planned": total_planned,
        "total": 0,
        "passed_count": 0,
        "pass_rate": None,
        "quality_metrics": aggregate_dialogue_quality_metrics(
            [],
            total_planned=total_planned,
            provider_unavailable=provider_unavailable,
        ),
        "provider_unavailable": provider_unavailable,
        "failed": [],
        "results": [],
    }
    (iteration_dir / "dialogue_probe_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def write_dialogue_runtime_error_report(
    config: LoopConfig,
    iteration_dir: Path,
    exc: Exception,
) -> None:
    total_planned = dialogue_scenario_count(config)
    violation = f"dialogue_probe_runtime_error:{type(exc).__name__}"
    report = {
        "total_planned": total_planned,
        "total": 0,
        "passed_count": 0,
        "pass_rate": 0.0,
        "quality_metrics": aggregate_dialogue_quality_metrics([], total_planned=total_planned),
        "provider_unavailable": None,
        "failed": [
            {
                "id": "dialogue_probe_runtime",
                "session_id": None,
                "violations": [violation],
                "error": str(exc),
            }
        ],
        "results": [],
    }
    (iteration_dir / "dialogue_probe_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def dialogue_scenario_count(config: LoopConfig) -> int:
    selected = {item for item in config.dialogue_probe_scenario_ids if item}
    if not selected:
        return len(DIALOGUE_SCENARIOS)
    return sum(1 for item in DIALOGUE_SCENARIOS if item.get("id") in selected)


def build_managed_server_command(config: LoopConfig) -> list[str]:
    return [
        select_script_python(),
        "-m",
        "uvicorn",
        "app.main:app",
        "--host",
        config.dialogue_probe_managed_host,
        "--port",
        str(config.dialogue_probe_managed_port),
    ]


def managed_server_env() -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _prepend_pythonpath(".", env.get("PYTHONPATH"))
    return env


def self_test_env(iteration_dir: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONPATH"] = _prepend_pythonpath(".", env.get("PYTHONPATH"))
    database_url = os.getenv("AGENT_QUALITY_SELF_TEST_DATABASE_URL") or "sqlite+aiosqlite:///:memory:"
    eval_database_url = os.getenv("AGENT_QUALITY_SELF_TEST_EVAL_DATABASE_URL") or database_url
    env["DATABASE_URL"] = database_url
    env["EVAL_DATABASE_URL"] = eval_database_url
    env.setdefault("JWT_SECRET", "test-secret")
    env.setdefault("LANGGRAPH_STORE_BACKEND", "memory")
    env.setdefault("AGENT_QUALITY_LOOP_ITERATION_DIR", str(iteration_dir))
    return env


def wait_for_managed_server_ready(base_url: str, *, timeout_seconds: float) -> None:
    import httpx

    deadline = time.monotonic() + timeout_seconds
    last_error: str | None = None
    while time.monotonic() <= deadline:
        try:
            response = httpx.get(f"{base_url.rstrip('/')}/api/v1/chat/providers", timeout=2.0)
            if response.status_code < 500:
                return
        except httpx.HTTPError as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        time.sleep(0.25)
    raise RuntimeError(f"managed dialogue probe server did not become ready: {last_error or 'timeout'}")


def stop_managed_server(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=10)


def summarize_dialogue_probe_report(report: dict[str, Any]) -> dict[str, Any]:
    provider_unavailable = report.get("provider_unavailable")
    failed = report.get("failed") if isinstance(report.get("failed"), list) else []
    quality_metrics = report.get("quality_metrics") if isinstance(report.get("quality_metrics"), dict) else {}
    return {
        "total_planned": report.get("total_planned"),
        "total": report.get("total"),
        "passed_count": report.get("passed_count"),
        "pass_rate": report.get("pass_rate"),
        "quality_metrics": quality_metrics,
        "dimension_scores": quality_metrics.get("dimension_scores") if isinstance(quality_metrics.get("dimension_scores"), dict) else None,
        "dimension_failed_turn_counts": quality_metrics.get("dimension_failed_turn_counts") if isinstance(quality_metrics.get("dimension_failed_turn_counts"), dict) else None,
        "turn_pass_rate": quality_metrics.get("turn_pass_rate"),
        "scenario_pass_rate": quality_metrics.get("scenario_pass_rate"),
        "agent_failed_turn_count": quality_metrics.get("agent_failed_turn_count"),
        "user_visible_fallback_rate": quality_metrics.get("user_visible_fallback_rate"),
        "provider_unavailable": provider_unavailable if isinstance(provider_unavailable, dict) else None,
        "provider_unavailable_code": provider_unavailable.get("code") if isinstance(provider_unavailable, dict) else None,
        "provider_unavailable_http_status": provider_unavailable.get("http_status") if isinstance(provider_unavailable, dict) else None,
        "failed": failed,
        "failed_count": len(failed),
    }


def provider_config_from_dialogue_probe_report(report: dict[str, Any]) -> dict[str, Any] | None:
    model_config = _first_dialogue_model_config(report)
    if not model_config:
        return None
    provider = str(model_config.get("provider") or "").strip()
    model = str(model_config.get("model_planner") or model_config.get("model_writer") or "").strip()
    provider_value = str(model_config.get("provider_value") or "").strip()
    configured_provider = provider_value or (f"{provider}:{model}" if provider and model else provider)
    configured_models = [configured_provider] if configured_provider else []
    return {
        "source": "dialogue_probe_runtime",
        "configured_provider": configured_provider or None,
        "enabled_providers": [provider] if provider else [],
        "configured_models": configured_models,
        "provider": provider or None,
        "model_planner": model_config.get("model_planner"),
        "model_writer": model_config.get("model_writer"),
        "api_key_set": None,
    }


def _first_dialogue_model_config(report: dict[str, Any]) -> dict[str, Any] | None:
    for scenario in report.get("results") or []:
        if not isinstance(scenario, dict):
            continue
        for turn in scenario.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            agent_result = turn.get("agent_result")
            if not isinstance(agent_result, dict):
                continue
            diagnostics = agent_result.get("diagnostics")
            if not isinstance(diagnostics, dict):
                continue
            model_config = diagnostics.get("model_config")
            if isinstance(model_config, dict):
                return model_config
    return None


def combine_iteration_returncode(self_test_returncode: int, dialogue_probe: dict[str, Any] | None = None) -> int:
    if self_test_returncode != 0:
        return self_test_returncode
    if not isinstance(dialogue_probe, dict) or not dialogue_probe:
        return 0
    provider_unavailable = dialogue_probe.get("provider_unavailable")
    failed_count = dialogue_probe.get("failed_count")
    dialogue_returncode = dialogue_probe.get("returncode")
    if provider_unavailable and dialogue_returncode == DIALOGUE_PROVIDER_UNAVAILABLE_EXIT_CODE:
        return 0
    if isinstance(failed_count, int) and failed_count > 0:
        return 1
    if isinstance(dialogue_returncode, int) and dialogue_returncode != 0:
        return dialogue_returncode
    return 0


def apply_suggested_provider_replay_results(report: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    if not results:
        return report
    report["suggested_provider_replays"] = results
    viable = [
        item.get("model_value")
        for item in results
        if item.get("returncode") == 0
        and item.get("environment_failure_count") == 0
        and item.get("pass_rate") == 1.0
    ]
    report["provider_viable_values"] = [item for item in viable if isinstance(item, str)]
    report["next_actions"] = suggest_next_actions(report)
    return report


def _safe_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value.strip())
    return safe[:80] or "model"


def summarize_iteration(iteration: int, iteration_dir: Path, returncode: int, elapsed_ms: int) -> dict[str, Any]:
    summary = load_json(iteration_dir / "summary.json")
    results = summary.get("results")
    if not isinstance(results, list):
        results = []

    audit = next((item for item in results if isinstance(item, dict) and item.get("name") == "conversation-audit"), {})
    deterministic = next((item for item in results if isinstance(item, dict) and item.get("name") == "deterministic-replay"), {})
    live = next((item for item in results if isinstance(item, dict) and item.get("name") == "live-replay"), {})
    dialogue_probe_report = load_json(iteration_dir / "dialogue_probe_report.json")
    dialogue_probe_step = load_json(iteration_dir / "dialogue_probe_step.json")
    dialogue_probe = summarize_dialogue_probe_report(dialogue_probe_report) if dialogue_probe_report else None
    if dialogue_probe and dialogue_probe_step:
        dialogue_probe.update(
            {
                "name": dialogue_probe_step.get("name"),
                "returncode": dialogue_probe_step.get("returncode"),
                "elapsed_ms": dialogue_probe_step.get("elapsed_ms"),
                "out": dialogue_probe_step.get("out"),
            }
        )
    combined_returncode = combine_iteration_returncode(returncode, dialogue_probe)
    failed = summary.get("failed")
    if not isinstance(failed, list):
        failed = []
    failed_steps = [item.get("name") for item in failed if isinstance(item, dict)]
    if combined_returncode != 0 and isinstance(dialogue_probe, dict) and dialogue_probe.get("returncode"):
        provider_unavailable = dialogue_probe.get("provider_unavailable")
        if not provider_unavailable:
            failed_steps.append("dialogue-probe")

    report = {
        "iteration": iteration,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(iteration_dir),
        "returncode": combined_returncode,
        "self_test_returncode": returncode,
        "elapsed_ms": elapsed_ms,
        "failed_steps": failed_steps,
        "quality_finding_count": audit.get("quality_finding_count"),
        "audit_findings_by_type": audit.get("findings_by_type"),
        "audit_quality_action_counts": audit_quality_action_counts(audit.get("findings_by_type")),
        "environment_failure_count": audit.get("environment_failure_count"),
        "environment_failure_rate": audit.get("environment_failure_rate"),
        "fallback_rate": audit.get("fallback_rate"),
        "status_counts": audit.get("status_counts"),
        "worker_counts": audit.get("worker_counts"),
        "failure_class_counts": audit.get("failure_class_counts"),
        "provider_issue_counts": audit.get("provider_issue_counts"),
        "provider_issue_category_counts": audit.get("provider_issue_category_counts"),
        "provider_action_counts": audit.get("provider_action_counts"),
        "deterministic_pass_rate": deterministic.get("pass_rate"),
        "deterministic_fallback_count": deterministic.get("fallback_count"),
        "deterministic_active_tool_counts": deterministic.get("active_tool_counts"),
        "deterministic_active_skill_counts": deterministic.get("active_skill_counts"),
        "deterministic_tool_call_counts": deterministic.get("tool_call_counts"),
        "deterministic_worker_tool_counts": deterministic.get("worker_tool_counts"),
        "deterministic_worker_tool_call_counts": deterministic.get("worker_tool_call_counts"),
        "deterministic_worker_tool_boundary_violations": deterministic.get("worker_tool_boundary_violations"),
        "deterministic_coverage": deterministic.get("coverage"),
        "deterministic_coverage_passed": _coverage_passed(deterministic.get("coverage")),
        "deterministic_quality_issue_regressions": _observed_coverage_values(
            deterministic.get("coverage"),
            "quality_issue_regressions",
        ),
        "deterministic_failed": deterministic.get("failed"),
        "live_pass_rate": live.get("pass_rate"),
        "live_environment_failure_count": live.get("environment_failure_count"),
        "live_provider_issue_counts": live.get("provider_issue_counts"),
        "live_provider_issue_category_counts": live.get("provider_issue_category_counts"),
        "live_provider_action_counts": live.get("provider_action_counts"),
        "live_model_config_counts": live.get("model_config_counts"),
        "live_active_tool_counts": live.get("active_tool_counts"),
        "live_active_skill_counts": live.get("active_skill_counts"),
        "live_tool_call_counts": live.get("tool_call_counts"),
        "live_worker_tool_counts": live.get("worker_tool_counts"),
        "live_worker_tool_call_counts": live.get("worker_tool_call_counts"),
        "live_worker_tool_boundary_violations": live.get("worker_tool_boundary_violations"),
        "live_failed": live.get("failed"),
        "dialogue_probe": dialogue_probe,
    }
    provider_config = provider_config_from_dialogue_probe_report(dialogue_probe_report) or collect_provider_config_via_project_python()
    report["provider_health"] = summarize_provider_health(
        [report, dialogue_probe_report],
        provider_config=provider_config,
    )
    provider_health = report.get("provider_health")
    report["provider_suggested_provider_values"] = (
        provider_health.get("suggested_provider_values")
        if isinstance(provider_health, dict)
        else None
    )
    report["audit_uncovered_finding_types"] = audit_uncovered_finding_types(report)
    report["next_actions"] = suggest_next_actions(report)
    return report


def suggest_next_actions(report: dict[str, Any]) -> list[str]:
    actions: list[str] = []
    failed_steps = report.get("failed_steps")
    if isinstance(failed_steps, list) and failed_steps:
        actions.append("inspect_failed_self_test_steps")

    quality_finding_count = report.get("quality_finding_count")
    if isinstance(quality_finding_count, int) and quality_finding_count > 0:
        actions.append("inspect_conversation_audit_quality_findings")
        for action in _quality_actions_from_report(report):
            _append_once(actions, action)
        uncovered = report.get("audit_uncovered_finding_types")
        if isinstance(uncovered, list) and uncovered:
            actions.append("add_deterministic_regression_for_audit_findings")
        else:
            actions.append("rerun_or_archive_historical_audit_findings")

    environment_failure_count = report.get("environment_failure_count")
    live_environment_failure_count = report.get("live_environment_failure_count")
    has_environment_failure = (
        (isinstance(environment_failure_count, int) and environment_failure_count > 0)
        or (isinstance(live_environment_failure_count, int) and live_environment_failure_count > 0)
    )
    health_action = _provider_action_from_health(report.get("provider_health"))
    dialogue_provider_action = _provider_action_from_dialogue_probe(report.get("dialogue_probe"))
    if dialogue_provider_action and health_action in {None, "inspect_provider_error_and_model_config", "check_model_provider_or_runtime_environment"}:
        provider_action = dialogue_provider_action
    else:
        provider_action = health_action
    if provider_action:
        _append_once(actions, provider_action)
        suggested = report.get("provider_suggested_provider_values")
        if isinstance(suggested, list) and suggested:
            _append_once(actions, "run_live_replay_against_suggested_provider_values")
        viable = report.get("provider_viable_values")
        if isinstance(viable, list) and viable:
            _append_once(actions, "switch_default_provider_to_verified_value")
    elif has_environment_failure:
        provider_action = _first_count_key(report.get("provider_action_counts")) or _first_count_key(
            report.get("live_provider_action_counts")
        )
        _append_once(actions, provider_action or "check_model_provider_or_runtime_environment")

    live_failed = report.get("live_failed")
    if isinstance(live_failed, list) and live_failed:
        actions.append("inspect_live_replay_contract_or_route_failures")

    dialogue_probe = report.get("dialogue_probe")
    if isinstance(dialogue_probe, dict):
        provider_unavailable = dialogue_probe.get("provider_unavailable")
        failed_count = dialogue_probe.get("failed_count")
        if not provider_unavailable and isinstance(failed_count, int) and failed_count > 0:
            actions.append("inspect_dialogue_probe_context_or_quality_failures")

    live_tool_boundary_violations = report.get("live_worker_tool_boundary_violations")
    if isinstance(live_tool_boundary_violations, list) and live_tool_boundary_violations:
        actions.append("inspect_worker_tool_boundary_violations")
    deterministic_tool_boundary_violations = report.get("deterministic_worker_tool_boundary_violations")
    if isinstance(deterministic_tool_boundary_violations, list) and deterministic_tool_boundary_violations:
        _append_once(actions, "inspect_worker_tool_boundary_violations")

    if report.get("deterministic_coverage_passed") is False:
        actions.append("expand_deterministic_replay_coverage")

    if not actions:
        actions.append("expand_replay_cases_or_start_next_quality_iteration")
    return actions


def _provider_action_from_dialogue_probe(dialogue_probe: Any) -> str | None:
    if not isinstance(dialogue_probe, dict):
        return None
    provider_unavailable = dialogue_probe.get("provider_unavailable")
    if not isinstance(provider_unavailable, dict):
        return None
    action = provider_unavailable.get("action")
    if isinstance(action, str) and action:
        return action
    code = str(provider_unavailable.get("code") or "")
    if code == "provider_billing_unavailable":
        return "recharge_provider_or_switch_model"
    if code in {"provider_auth_failed", "subscription_expired"}:
        return "check_api_key_model_permission_or_provider_config"
    if code == "provider_rate_limited":
        return "retry_later_or_switch_model"
    return "inspect_provider_error_and_model_config"


def _coverage_passed(coverage: Any) -> bool | None:
    if not isinstance(coverage, dict):
        return None
    passed = coverage.get("passed")
    return passed if isinstance(passed, bool) else None


def _observed_coverage_values(coverage: Any, key: str) -> list[str] | None:
    if not isinstance(coverage, dict):
        return None
    observed = coverage.get("observed")
    if not isinstance(observed, dict):
        return None
    values = observed.get(key)
    if not isinstance(values, list):
        return None
    return sorted({item for item in values if isinstance(item, str) and item})


def audit_uncovered_finding_types(report: dict[str, Any]) -> list[str] | None:
    findings_by_type = report.get("audit_findings_by_type")
    if not isinstance(findings_by_type, dict):
        return None
    quality_findings = {
        str(key)
        for key, value in findings_by_type.items()
        if key not in ENVIRONMENT_AUDIT_FINDING_TYPES and isinstance(value, int) and value > 0
    }
    regressions = report.get("deterministic_quality_issue_regressions")
    regression_set = {item for item in regressions if isinstance(item, str)} if isinstance(regressions, list) else set()
    return sorted(quality_findings - regression_set)


def audit_quality_action_counts(findings_by_type: Any) -> dict[str, int] | None:
    if not isinstance(findings_by_type, dict):
        return None
    counts: dict[str, int] = {}
    for finding_type, count in findings_by_type.items():
        if finding_type in ENVIRONMENT_AUDIT_FINDING_TYPES or not isinstance(count, int) or count <= 0:
            continue
        action = AUDIT_FINDING_ACTIONS.get(str(finding_type), "inspect_unclassified_audit_quality_finding")
        counts[action] = counts.get(action, 0) + count
    return dict(sorted(counts.items(), key=lambda item: item[1], reverse=True))


def _quality_actions_from_report(report: dict[str, Any]) -> list[str]:
    action_counts = report.get("audit_quality_action_counts")
    if not isinstance(action_counts, dict):
        action_counts = audit_quality_action_counts(report.get("audit_findings_by_type"))
    if not isinstance(action_counts, dict):
        return []
    return [str(action) for action, count in action_counts.items() if isinstance(count, int) and count > 0]


def _provider_action_from_health(provider_health: Any) -> str | None:
    if not isinstance(provider_health, dict):
        return None
    if provider_health.get("status") not in {"unhealthy", "degraded"}:
        return None
    action = provider_health.get("action")
    return action if isinstance(action, str) and action else None


def _first_count_key(value: Any) -> str | None:
    if not isinstance(value, dict):
        return None
    key = next(iter(value.keys()), None)
    return str(key) if key else None


def _append_once(actions: list[str], action: str) -> None:
    if action not in actions:
        actions.append(action)


def append_jsonl(path: Path, item: dict[str, Any]) -> None:
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(item, ensure_ascii=False) + "\n")


def should_continue(start_monotonic: float, completed_iterations: int, config: LoopConfig) -> bool:
    if config.iterations is not None and completed_iterations >= config.iterations:
        return False
    if config.duration_seconds is not None and (time.monotonic() - start_monotonic) >= config.duration_seconds:
        return False
    return True


def run_loop(config: LoopConfig) -> list[dict[str, Any]]:
    config.out_root.mkdir(parents=True, exist_ok=True)
    index_path = config.out_root / "iterations.jsonl"
    latest_path = config.out_root / "latest_summary.json"
    reports: list[dict[str, Any]] = []
    started = time.monotonic()
    iteration = 0

    while should_continue(started, iteration, config):
        iteration += 1
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        iteration_dir = config.out_root / f"iter_{iteration:04d}_{stamp}"
        iteration_dir.mkdir(parents=True, exist_ok=True)
        command = build_self_test_command(config, iteration_dir)
        print(f"\n== quality-loop iteration {iteration} ==")
        print(" ".join(command))
        step_started = time.monotonic()
        completed = subprocess.run(command, text=True, env=self_test_env(iteration_dir))
        elapsed_ms = int((time.monotonic() - step_started) * 1000)
        if config.dialogue_probe:
            run_dialogue_probe(config, iteration_dir)
        report = summarize_iteration(iteration, iteration_dir, completed.returncode, elapsed_ms)
        if config.live_replay:
            suggested_replays = run_suggested_provider_replays(
                report,
                iteration_dir=iteration_dir,
                base_url=config.live_replay_base_url,
            )
            report = apply_suggested_provider_replay_results(report, suggested_replays)
        reports.append(report)
        append_jsonl(index_path, report)
        latest_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(json.dumps(report, ensure_ascii=False, indent=2))

        failed = int(report.get("returncode") or 0) != 0
        if failed and config.stop_on_failure:
            break
        if should_continue(started, iteration, config):
            time.sleep(config.interval_seconds)
    return reports


def parse_args() -> LoopConfig:
    parser = argparse.ArgumentParser(description="Run repeated Smart Eats agent self-test/audit/replay iterations")
    parser.add_argument("--profile", choices=["quick", "extended"], default="extended")
    parser.add_argument("--no-audit", action="store_true", help="Disable local conversation DB audit")
    parser.add_argument("--db", default="local.db")
    parser.add_argument("--limit-sessions", type=int, default=30)
    parser.add_argument(
        "--audit-session-id",
        action="append",
        default=[],
        help="Specific session id for audit; can be repeated.",
    )
    parser.add_argument("--live-replay", action="store_true")
    parser.add_argument("--live-replay-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--dialogue-probe", action="store_true", help="Run five-turn live dialogue quality scenarios against a running backend")
    parser.add_argument("--dialogue-probe-base-url", default=None)
    parser.add_argument("--dialogue-probe-model-value", default=None)
    parser.add_argument("--dialogue-probe-scenario-id", action="append", default=[])
    parser.add_argument("--dialogue-probe-timeout-seconds", type=float, default=150.0)
    parser.add_argument("--dialogue-probe-request-delay-seconds", type=float, default=1.0)
    parser.add_argument("--dialogue-probe-persist-wait-seconds", type=float, default=20.0)
    parser.add_argument("--dialogue-probe-persist-poll-seconds", type=float, default=0.25)
    parser.add_argument("--dialogue-probe-continue-on-provider-unavailable", action="store_true")
    parser.add_argument(
        "--dialogue-probe-skip-provider-preflight",
        action="store_true",
        help="Skip provider ping before live dialogue probe; useful for slow local model endpoints.",
    )
    parser.add_argument("--dialogue-probe-managed-server", action="store_true", help="Start and stop a temporary backend for dialogue probe")
    parser.add_argument("--dialogue-probe-managed-host", default="127.0.0.1")
    parser.add_argument("--dialogue-probe-managed-port", type=int, default=8031)
    parser.add_argument("--dialogue-probe-managed-startup-timeout-seconds", type=float, default=90.0)
    parser.add_argument("--out-root", default="/tmp/smarteats_agent_quality_loop")
    parser.add_argument("--interval-seconds", type=float, default=300.0)
    parser.add_argument("--iterations", type=int, default=1)
    parser.add_argument("--duration-hours", type=float, default=None)
    parser.add_argument("--stop-on-failure", action="store_true")
    parser.add_argument(
        "--fail-on-quality-findings",
        action="store_true",
        help="Fail an iteration when audit finds product-quality issues; environment failures are allowed.",
    )
    args = parser.parse_args()

    duration_seconds = args.duration_hours * 3600 if args.duration_hours is not None else None
    iterations = args.iterations if args.iterations and args.iterations > 0 else None
    if duration_seconds is not None and args.iterations == 1:
        iterations = None

    dialogue_probe_base_url = args.dialogue_probe_base_url or args.live_replay_base_url
    if args.dialogue_probe_managed_server and not args.dialogue_probe_base_url:
        dialogue_probe_base_url = f"http://{args.dialogue_probe_managed_host}:{args.dialogue_probe_managed_port}"

    return LoopConfig(
        profile=args.profile,
        audit=not args.no_audit,
        db=args.db,
        limit_sessions=args.limit_sessions,
        audit_session_ids=tuple(str(item).strip() for item in args.audit_session_id if str(item).strip()),
        live_replay=args.live_replay,
        live_replay_base_url=args.live_replay_base_url,
        dialogue_probe=args.dialogue_probe,
        dialogue_probe_base_url=dialogue_probe_base_url,
        dialogue_probe_model_value=str(args.dialogue_probe_model_value).strip() if args.dialogue_probe_model_value else None,
        dialogue_probe_scenario_ids=tuple(str(item).strip() for item in args.dialogue_probe_scenario_id if str(item).strip()),
        dialogue_probe_timeout_seconds=max(float(args.dialogue_probe_timeout_seconds), 1.0),
        dialogue_probe_request_delay_seconds=max(float(args.dialogue_probe_request_delay_seconds), 0.0),
        dialogue_probe_persist_wait_seconds=max(float(args.dialogue_probe_persist_wait_seconds), 0.0),
        dialogue_probe_persist_poll_seconds=max(float(args.dialogue_probe_persist_poll_seconds), 0.05),
        dialogue_probe_continue_on_provider_unavailable=bool(args.dialogue_probe_continue_on_provider_unavailable),
        dialogue_probe_skip_provider_preflight=bool(args.dialogue_probe_skip_provider_preflight),
        dialogue_probe_managed_server=bool(args.dialogue_probe_managed_server),
        dialogue_probe_managed_host=str(args.dialogue_probe_managed_host or "127.0.0.1"),
        dialogue_probe_managed_port=max(int(args.dialogue_probe_managed_port), 1),
        dialogue_probe_managed_startup_timeout_seconds=max(float(args.dialogue_probe_managed_startup_timeout_seconds), 1.0),
        out_root=Path(args.out_root),
        interval_seconds=max(args.interval_seconds, 0.0),
        iterations=iterations,
        duration_seconds=duration_seconds,
        stop_on_failure=args.stop_on_failure,
        fail_on_quality_findings=args.fail_on_quality_findings,
    )


def main() -> None:
    reports = run_loop(parse_args())
    if any(item.get("returncode") != 0 for item in reports):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
