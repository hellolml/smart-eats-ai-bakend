#!/usr/bin/env python3
"""Baseline vs candidate regression gate."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.persistence.postgres import compare_report_dicts


CORE_METRICS = (
    "task_success",
    "intent_accuracy",
    "tool_accuracy",
    "constraint_satisfaction",
    "schema_compliance",
    "recovery_score",
    "efficiency",
)
SAFETY_METRICS = ("safety_score", "no_leak", "graceful_reject")


def check_regression(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    *,
    max_overall_drop: float = 0.03,
    max_core_metric_drop: float = 0.05,
) -> tuple[bool, dict[str, Any]]:
    baseline = _normalize_report(baseline)
    candidate = _normalize_report(candidate)
    comparison = compare_report_dicts("baseline", baseline, "candidate", candidate)
    violations: list[dict[str, Any]] = []

    overall_delta = float(comparison["summary_delta"].get("overall_success_rate") or 0.0)
    if overall_delta < -max_overall_drop:
        violations.append({
            "type": "overall_success_drop",
            "metric": "overall_success_rate",
            "baseline": baseline.get("overall_success_rate", 0.0),
            "candidate": candidate.get("overall_success_rate", 0.0),
            "delta": overall_delta,
            "allowed_drop": max_overall_drop,
        })

    baseline_cases = _case_map(baseline)
    candidate_cases = _case_map(candidate)
    for case_id, base_case in baseline_cases.items():
        cand_case = candidate_cases.get(case_id)
        if not cand_case:
            continue
        if base_case.get("priority") != "p0":
            continue
        base_success = float(base_case.get("success_rate") or 0.0)
        cand_success = float(cand_case.get("success_rate") or 0.0)
        if base_success >= 1.0 and cand_success < 1.0:
            violations.append({
                "type": "p0_regression",
                "case_id": case_id,
                "baseline": base_success,
                "candidate": cand_success,
                "delta": cand_success - base_success,
            })

    baseline_metrics = _metric_averages(baseline)
    candidate_metrics = _metric_averages(candidate)
    metric_delta = _merge_metric_delta(comparison["metric_delta"], baseline_metrics, candidate_metrics)
    for metric in CORE_METRICS:
        if metric not in baseline_metrics or metric not in candidate_metrics:
            continue
        delta = candidate_metrics[metric] - baseline_metrics[metric]
        if delta < -max_core_metric_drop:
            violations.append({
                "type": "core_metric_drop",
                "metric": metric,
                "baseline": baseline_metrics[metric],
                "candidate": candidate_metrics[metric],
                "delta": delta,
                "allowed_drop": max_core_metric_drop,
            })

    for metric in SAFETY_METRICS:
        if metric not in baseline_metrics or metric not in candidate_metrics:
            continue
        delta = candidate_metrics[metric] - baseline_metrics[metric]
        if delta < 0:
            violations.append({
                "type": "safety_metric_drop",
                "metric": metric,
                "baseline": baseline_metrics[metric],
                "candidate": candidate_metrics[metric],
                "delta": delta,
                "allowed_drop": 0.0,
            })

    passed = not violations
    return passed, {
        "passed": passed,
        "summary_delta": comparison["summary_delta"],
        "case_changes": comparison["case_changes"],
        "metric_delta": metric_delta,
        "scene_delta": comparison["scene_delta"],
        "category_delta": comparison["category_delta"],
        "config": {
            "max_overall_drop": max_overall_drop,
            "max_core_metric_drop": max_core_metric_drop,
            "core_metrics": list(CORE_METRICS),
            "safety_metrics": list(SAFETY_METRICS),
        },
        "violations": violations,
    }


def _normalize_report(report: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(report)
    summary = normalized.get("summary") if isinstance(normalized.get("summary"), dict) else {}
    if "overall_success_rate" not in normalized:
        for key in ("overall_success_rate", "success_rate"):
            value = summary.get(key, normalized.get(key))
            if isinstance(value, (int, float)):
                normalized["overall_success_rate"] = float(value)
                break

    if not isinstance(normalized.get("results"), list) and isinstance(normalized.get("case_results"), list):
        normalized["results"] = [_normalize_case_result(item) for item in normalized["case_results"] if isinstance(item, dict)]
    return normalized


def _normalize_case_result(result: dict[str, Any]) -> dict[str, Any]:
    normalized = dict(result)
    if "success_rate" not in normalized:
        if isinstance(normalized.get("success"), bool):
            normalized["success_rate"] = 1.0 if normalized["success"] else 0.0
        elif isinstance(normalized.get("passed"), bool):
            normalized["success_rate"] = 1.0 if normalized["passed"] else 0.0
    if "avg_scores" not in normalized and isinstance(normalized.get("scores"), dict):
        normalized["avg_scores"] = normalized["scores"]
    return normalized


def _case_map(report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(result.get("case_id")): result
        for result in report.get("results", []) if isinstance(result, dict) and result.get("case_id")
    }


def _metric_averages(report: dict[str, Any]) -> dict[str, float]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for result in report.get("results", []) if isinstance(report.get("results"), list) else []:
        scores = result.get("avg_scores", {})
        if not isinstance(scores, dict):
            continue
        for metric, value in scores.items():
            if isinstance(value, (int, float)):
                totals[metric] = totals.get(metric, 0.0) + float(value)
                counts[metric] = counts.get(metric, 0) + 1
    averages = {metric: totals[metric] / counts[metric] for metric in totals if counts.get(metric)}
    metrics = report.get("metrics", {}) if isinstance(report.get("metrics"), dict) else {}
    for metric, value in metrics.items():
        if metric in averages:
            continue
        extracted = _extract_metric_value(value)
        if extracted is not None:
            averages[str(metric)] = extracted
    return averages


def _extract_metric_value(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if not isinstance(value, dict):
        return None
    for key in ("mean", "avg", "average", "value", "score"):
        item = value.get(key)
        if isinstance(item, (int, float)):
            return float(item)
    return None


def _merge_metric_delta(
    comparison_delta: dict[str, Any],
    baseline_metrics: dict[str, float],
    candidate_metrics: dict[str, float],
) -> dict[str, Any]:
    merged = dict(comparison_delta)
    for metric in sorted(set(baseline_metrics) | set(candidate_metrics)):
        if metric in merged:
            continue
        base_value = baseline_metrics.get(metric, 0.0)
        cand_value = candidate_metrics.get(metric, 0.0)
        merged[metric] = {
            "baseline": base_value,
            "candidate": cand_value,
            "delta": cand_value - base_value,
        }
    return merged


def _load_report(path: str | Path) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Check baseline vs candidate eval regression")
    parser.add_argument("--baseline", required=True, help="Baseline eval report JSON")
    parser.add_argument("--candidate", required=True, help="Candidate eval report JSON")
    parser.add_argument("--max-overall-drop", type=float, default=0.03)
    parser.add_argument("--max-core-metric-drop", type=float, default=0.05)
    args = parser.parse_args()

    passed, result = check_regression(
        _load_report(args.baseline),
        _load_report(args.candidate),
        max_overall_drop=args.max_overall_drop,
        max_core_metric_drop=args.max_core_metric_drop,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    raise SystemExit(0 if passed else 1)


if __name__ == "__main__":
    main()
