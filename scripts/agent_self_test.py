#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


def select_pytest_command() -> list[str]:
    override = os.getenv("PYTEST_BIN")
    if override:
        return shlex.split(override)
    pytest_bin = shutil.which("pytest")
    if pytest_bin:
        return [pytest_bin]
    return [sys.executable, "-m", "pytest"]


def select_script_python() -> str:
    override = os.getenv("AGENT_TEST_PYTHON")
    if override:
        return override
    pytest_bin = shutil.which("pytest")
    if pytest_bin:
        candidate = _python_from_shebang(Path(pytest_bin))
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


PYTEST = select_pytest_command()
SCRIPT_PYTHON = select_script_python()


@dataclass(frozen=True)
class Step:
    name: str
    command: list[str]


QUICK_STEPS = [
    Step(
        "contract-router-skill",
        [
            *PYTEST,
            "app/tests/test_agent_contracts.py",
            "app/tests/test_agent_self_eval_matrix.py",
            "app/tests/test_agent_conversation_audit.py",
            "app/tests/test_agent_supervisor_graph.py",
            "app/tests/test_agent_supervisor_workers.py",
            "app/tests/test_supervisor_worker_preparation.py",
            "app/tests/test_skill_hooks_tool_result_handler.py",
            "-q",
        ],
    ),
    Step(
        "runtime-stream-contract",
        [
            *PYTEST,
            "app/tests/test_replay_eval.py",
            "app/tests/test_chat.py::test_run_chat_stream_preserves_core_event_contract",
            "app/tests/test_chat.py::test_run_chat_stream_supervisor_runtime_uses_direct_ai_text_without_state_final_json",
            "app/tests/test_app_chat.py::test_run_chat_stream_uses_supervisor_direct_ai_text_when_no_final_json",
            "app/tests/test_app_chat.py::test_app_chat_stream_stop",
            "app/tests/test_chat.py::test_chat_stream_stop",
            "-q",
        ],
    ),
    Step(
        "travel-domain",
        [
            *PYTEST,
            "app/tests/test_travel_workflow.py",
            "app/tests/test_travel_skill_integration.py",
            "-q",
        ],
    ),
]


EXTENDED_STEPS = [
    *QUICK_STEPS,
    Step(
        "runtime-eval-monitoring",
        [
            *PYTEST,
            "app/tests/test_agent_runtime_graph_builder.py",
            "app/tests/test_agent_runtime_tool_postprocess.py",
            "app/tests/test_runtime_finalization.py",
            "app/tests/test_internal_metrics_api.py",
            "app/tests/test_agent_metrics_summary.py",
            "app/tests/agent_eval/test_task_completion.py",
            "-q",
        ],
    ),
]


def run_step(step: Step) -> dict[str, object]:
    print(f"\n== {step.name} ==")
    print(" ".join(step.command))
    completed = subprocess.run(step.command, text=True)
    return {"name": step.name, "returncode": completed.returncode}


def run_audit(
    db_path: str,
    limit_sessions: int,
    out_dir: Path,
    *,
    session_ids: list[str] | None = None,
    fail_on_quality_findings: bool = False,
) -> dict[str, object]:
    out = out_dir / "agent_conversation_audit.json"
    command = [
        SCRIPT_PYTHON,
        "scripts/agent_conversation_audit.py",
        "--db",
        db_path,
        "--limit-sessions",
        str(limit_sessions),
        "--out",
        str(out),
    ]
    for session_id in session_ids or []:
        command.extend(["--session-id", session_id])
    if fail_on_quality_findings:
        command.append("--fail-on-quality-findings")
    print("\n== conversation-audit ==")
    print(" ".join(command))
    completed = subprocess.run(command, text=True)
    summary: dict[str, object] = {"name": "conversation-audit", "returncode": completed.returncode, "out": str(out)}
    if out.exists():
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {}
        if isinstance(report, dict):
            summary.update(
                {
                    "finding_count": report.get("finding_count"),
                    "quality_finding_count": report.get("quality_finding_count"),
                    "findings_by_type": report.get("findings_by_type"),
                    "fallback_rate": report.get("fallback_rate"),
                    "environment_failure_count": report.get("environment_failure_count"),
                    "environment_failure_rate": report.get("environment_failure_rate"),
                    "status_counts": report.get("status_counts"),
                    "worker_counts": report.get("worker_counts"),
                    "failure_class_counts": report.get("failure_class_counts"),
                    "provider_issue_counts": report.get("provider_issue_counts"),
                    "provider_issue_category_counts": report.get("provider_issue_category_counts"),
                    "provider_action_counts": report.get("provider_action_counts"),
                    "session_ids": report.get("session_ids"),
                }
            )
    return summary


def run_live_replay(base_url: str, out_dir: Path) -> dict[str, object]:
    out = out_dir / "live_replay_report.json"
    command = [
        SCRIPT_PYTHON,
        "scripts/replay_eval.py",
        "--base-url",
        base_url,
        "--out",
        str(out),
        "--allow-environment-failure-class",
        "upstream_error",
        "--timeout-seconds",
        "20",
        "--request-delay-seconds",
        "0.25",
    ]
    print("\n== live-replay ==")
    print(" ".join(command))
    completed = subprocess.run(command, text=True)
    summary: dict[str, object] = {"name": "live-replay", "returncode": completed.returncode, "out": str(out)}
    if out.exists():
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {}
        if isinstance(report, dict):
            summary.update(
                {
                    "total": report.get("total"),
                    "passed_count": report.get("passed_count"),
                    "pass_rate": report.get("pass_rate"),
                    "fallback_count": report.get("fallback_count"),
                    "fallback_rate": report.get("fallback_rate"),
                    "environment_failure_count": report.get("environment_failure_count"),
                    "environment_failure_rate": report.get("environment_failure_rate"),
                    "provider_issue_counts": report.get("provider_issue_counts"),
                    "provider_issue_category_counts": report.get("provider_issue_category_counts"),
                    "provider_action_counts": report.get("provider_action_counts"),
                    "model_config_counts": report.get("model_config_counts"),
                    "active_tool_counts": report.get("active_tool_counts"),
                    "active_skill_counts": report.get("active_skill_counts"),
                    "tool_call_counts": report.get("tool_call_counts"),
                    "worker_tool_counts": report.get("worker_tool_counts"),
                    "worker_tool_call_counts": report.get("worker_tool_call_counts"),
                    "worker_tool_boundary_violations": report.get("worker_tool_boundary_violations"),
                    "coverage": report.get("coverage"),
                    "failed": report.get("failed"),
                }
            )
    return summary


def run_deterministic_replay(out_dir: Path) -> dict[str, object]:
    out = out_dir / "deterministic_replay_report.json"
    command = [
        SCRIPT_PYTHON,
        "scripts/agent_deterministic_replay.py",
        "--out",
        str(out),
    ]
    print("\n== deterministic-replay ==")
    print(" ".join(command))
    completed = subprocess.run(command, text=True)
    summary: dict[str, object] = {"name": "deterministic-replay", "returncode": completed.returncode, "out": str(out)}
    if out.exists():
        try:
            report = json.loads(out.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            report = {}
        if isinstance(report, dict):
            summary.update(
                {
                    "total": report.get("total"),
                    "passed_count": report.get("passed_count"),
                    "pass_rate": report.get("pass_rate"),
                    "fallback_count": report.get("fallback_count"),
                    "active_tool_counts": report.get("active_tool_counts"),
                    "active_skill_counts": report.get("active_skill_counts"),
                    "tool_call_counts": report.get("tool_call_counts"),
                    "worker_tool_counts": report.get("worker_tool_counts"),
                    "worker_tool_call_counts": report.get("worker_tool_call_counts"),
                    "worker_tool_boundary_violations": report.get("worker_tool_boundary_violations"),
                    "coverage": report.get("coverage"),
                    "failed": report.get("failed"),
                }
            )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Run stable Smart Eats agent self-test groups")
    parser.add_argument("--profile", choices=["quick", "extended"], default="quick")
    parser.add_argument("--audit", action="store_true", help="Also audit local conversation DB")
    parser.add_argument("--live-replay", action="store_true", help="Also replay fixture cases against a running backend")
    parser.add_argument("--live-replay-base-url", default="http://127.0.0.1:8000")
    parser.add_argument("--db", default="local.db", help="SQLite DB for --audit")
    parser.add_argument("--limit-sessions", type=int, default=30)
    parser.add_argument(
        "--audit-session-id",
        action="append",
        default=[],
        help="Specific session id for --audit; can be repeated.",
    )
    parser.add_argument(
        "--fail-on-audit-quality-findings",
        action="store_true",
        help="Fail when conversation audit finds product-quality issues; environment failures are allowed.",
    )
    parser.add_argument("--out-dir", default="/tmp/smarteats_agent_self_test")
    args = parser.parse_args()

    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    steps = QUICK_STEPS if args.profile == "quick" else EXTENDED_STEPS
    results = [run_step(step) for step in steps]
    results.append(run_deterministic_replay(out_dir))
    if args.audit:
        results.append(
            run_audit(
                args.db,
                args.limit_sessions,
                out_dir,
                session_ids=[item for item in args.audit_session_id if item],
                fail_on_quality_findings=args.fail_on_audit_quality_findings,
            )
        )
    if args.live_replay:
        results.append(run_live_replay(args.live_replay_base_url, out_dir))

    failed = [item for item in results if item.get("returncode") != 0]
    summary = {"profile": args.profile, "failed": failed, "results": results}
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"\nsummary: {summary_path}")
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
