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


@dataclass(frozen=True)
class LoopConfig:
    profile: str
    audit: bool
    db: str
    limit_sessions: int
    audit_session_ids: tuple[str, ...]
    live_replay: bool
    live_replay_base_url: str
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
    failed = summary.get("failed")
    if not isinstance(failed, list):
        failed = []

    report = {
        "iteration": iteration,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "out_dir": str(iteration_dir),
        "returncode": returncode,
        "elapsed_ms": elapsed_ms,
        "failed_steps": [item.get("name") for item in failed if isinstance(item, dict)],
        "quality_finding_count": audit.get("quality_finding_count"),
        "audit_findings_by_type": audit.get("findings_by_type"),
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
    }
    report["provider_health"] = summarize_provider_health(
        [report],
        provider_config=collect_provider_config_via_project_python(),
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
    provider_action = _provider_action_from_health(report.get("provider_health"))
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
        if key != "environment_failure" and isinstance(value, int) and value > 0
    }
    regressions = report.get("deterministic_quality_issue_regressions")
    regression_set = {item for item in regressions if isinstance(item, str)} if isinstance(regressions, list) else set()
    return sorted(quality_findings - regression_set)


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
        completed = subprocess.run(command, text=True)
        elapsed_ms = int((time.monotonic() - step_started) * 1000)
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

        failed = completed.returncode != 0
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

    return LoopConfig(
        profile=args.profile,
        audit=not args.no_audit,
        db=args.db,
        limit_sessions=args.limit_sessions,
        audit_session_ids=tuple(str(item).strip() for item in args.audit_session_id if str(item).strip()),
        live_replay=args.live_replay,
        live_replay_base_url=args.live_replay_base_url,
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
