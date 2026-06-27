"""Baseline vs candidate regression gate tests."""
from __future__ import annotations

from evals.scripts.check_regression import check_regression


def _report(
    *,
    success_rate: float = 1.0,
    case_success: float = 1.0,
    priority: str = "p1",
    task_success: float = 1.0,
    tool_accuracy: float = 1.0,
    safety_score: float = 1.0,
    no_leak: float = 1.0,
) -> dict:
    return {
        "overall_success_rate": success_rate,
        "results": [
            {
                "case_id": "case-1",
                "category": "safety" if priority == "p0" else "normal",
                "scene": "chat",
                "priority": priority,
                "success_rate": case_success,
                "avg_scores": {
                    "task_success": task_success,
                    "tool_accuracy": tool_accuracy,
                    "safety_score": safety_score,
                    "no_leak": no_leak,
                },
            }
        ],
    }


def test_regression_gate_blocks_new_p0_failure():
    passed, result = check_regression(
        _report(priority="p0", case_success=1.0),
        _report(priority="p0", case_success=0.0),
    )

    assert not passed
    assert result["violations"][0]["type"] == "p0_regression"


def test_regression_gate_blocks_overall_drop():
    passed, result = check_regression(
        _report(success_rate=0.90),
        _report(success_rate=0.85),
    )

    assert not passed
    assert any(item["type"] == "overall_success_drop" for item in result["violations"])


def test_regression_gate_blocks_core_metric_drop():
    passed, result = check_regression(
        _report(task_success=0.90),
        _report(task_success=0.83),
    )

    assert not passed
    assert any(item["metric"] == "task_success" for item in result["violations"])


def test_regression_gate_blocks_safety_drop():
    passed, result = check_regression(
        _report(safety_score=1.0, no_leak=1.0),
        _report(safety_score=0.99, no_leak=1.0),
    )

    assert not passed
    assert any(item["type"] == "safety_metric_drop" for item in result["violations"])


def test_regression_gate_allows_non_regression():
    passed, result = check_regression(
        _report(success_rate=0.85, task_success=0.8, tool_accuracy=0.8),
        _report(success_rate=0.87, task_success=0.82, tool_accuracy=0.81),
    )

    assert passed
    assert result["violations"] == []


def test_regression_gate_accepts_summary_case_results_schema():
    baseline = {
        "summary": {"overall_success_rate": 1.0},
        "case_results": [
            {
                "case_id": "case-1",
                "priority": "p0",
                "success": True,
                "scores": {"task_success": 1.0, "safety_score": 1.0},
            }
        ],
        "metrics": {"task_success": {"mean": 1.0}, "safety_score": {"mean": 1.0}},
    }
    candidate = {
        "summary": {"overall_success_rate": 0.9},
        "case_results": [
            {
                "case_id": "case-1",
                "priority": "p0",
                "success": False,
                "scores": {"task_success": 0.8, "safety_score": 0.9},
            }
        ],
        "metrics": {"task_success": {"mean": 0.8}, "safety_score": {"mean": 0.9}},
    }

    passed, result = check_regression(baseline, candidate)

    assert not passed
    assert any(item["type"] == "p0_regression" for item in result["violations"])
    assert any(item["type"] == "overall_success_drop" for item in result["violations"])
    assert any(item["type"] == "safety_metric_drop" for item in result["violations"])
