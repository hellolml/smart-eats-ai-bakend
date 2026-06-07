from __future__ import annotations

import pytest

from evals.persistence.postgres import EvalPersistenceStore, compare_report_dicts


def _report(success_rate: float = 1.0, *, case_id: str = "case-1", metric: float = 1.0) -> dict:
    return {
        "metadata": {
            "suite": "quick",
            "runner": "fixture",
            "report_schema_version": "1.1",
        },
        "timestamp": "2026-06-06T00:00:00",
        "total_cases": 1,
        "total_trials": 1,
        "overall_success_rate": success_rate,
        "category_breakdown": {"normal": {"success_rate": success_rate}},
        "scene_breakdown": {"chat": {"success_rate": success_rate}},
        "failure_summary": {},
        "duration_seconds": 0.1,
        "results": [
            {
                "case_id": case_id,
                "category": "normal",
                "scene": "chat",
                "task": "你好",
                "priority": "p1",
                "success_rate": success_rate,
                "avg_scores": {"task_success": metric},
                "trials": [
                    {
                        "trial_number": 0,
                        "scores": {"task_success": metric},
                        "weighted_score": metric,
                        "threshold_failures": [],
                        "missing_metrics": [],
                        "failure_class": "none",
                        "tool_calls": ["memory_search"],
                        "trace_timeline": [
                            {"index": 0, "event_type": "context", "label": "路由到 chat", "data": {"scene": "chat"}}
                        ],
                    }
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_eval_persistence_roundtrip_and_idempotent_upsert():
    store = EvalPersistenceStore("sqlite+aiosqlite:///:memory:")
    try:
        report = _report()
        first_run_id = await store.upsert_report("eval_report_20260606_000000.json", report)
        second_run_id = await store.upsert_report("eval_report_20260606_000000.json", report)

        reports = await store.list_reports()
        loaded = await store.load_report("eval_report_20260606_000000.json")
        detail = await store.load_case("eval_report_20260606_000000.json", "case-1")

        assert first_run_id == second_run_id
        assert len(reports) == 1
        assert loaded["metadata"]["report_schema_version"] == "1.1"
        assert loaded["results"][0]["trials"][0]["trace_timeline"][0]["event_type"] == "context"
        assert detail["case"]["case_id"] == "case-1"
        assert detail["trials"][0]["tool_calls"] == ["memory_search"]
    finally:
        await store.close()


@pytest.mark.asyncio
async def test_eval_persistence_compare_reports():
    store = EvalPersistenceStore("sqlite+aiosqlite:///:memory:")
    try:
        await store.upsert_report("baseline.json", _report(1.0, metric=1.0))
        await store.upsert_report("candidate.json", _report(0.0, metric=0.2))

        compared = await store.compare_reports("baseline.json", "candidate.json")

        assert compared["summary_delta"]["overall_success_rate"] == -1.0
        assert compared["case_changes"]["regressions"][0]["case_id"] == "case-1"
        assert compared["case_changes"]["score_drops"][0]["metric"] == "task_success"
    finally:
        await store.close()


def test_compare_report_dicts_handles_old_schema():
    old_report = _report()
    old_report.pop("metadata")
    old_report["results"][0]["trials"][0].pop("trace_timeline")

    compared = compare_report_dicts("old.json", old_report, "new.json", _report())

    assert compared["baseline_summary"]["suite"] is None
    assert compared["summary_delta"]["overall_success_rate"] == 0.0
