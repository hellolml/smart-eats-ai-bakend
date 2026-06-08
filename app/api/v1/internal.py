from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from sqlalchemy import desc, select

from app.agent.metrics import get_agent_metrics_snapshot, reset_agent_metrics
from app.agent.monitoring import parse_window_start
from app.api.deps import require_eval_admin
from app.common.config import settings

router = APIRouter()


@router.get("/metrics/agent")
async def get_agent_metrics(
    reset: bool = False,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
):
    stats = get_agent_metrics_snapshot()
    fallback = int(stats.get("fallback_final", 0))
    non_fallback = int(stats.get("non_fallback_final", 0))
    total_final = fallback + non_fallback
    fallback_rate = (fallback / total_final) if total_final > 0 else 0.0

    payload = {
        "metrics": stats,
        "summary": {
            "total_final": total_final,
            "fallback_rate": fallback_rate,
        },
    }
    if reset:
        reset_agent_metrics()
    return payload


def _eval_results_dir() -> Path:
    return Path(os.getenv("EVAL_RESULTS_DIR", "eval_results")).expanduser().resolve()


def _eval_database_url() -> str | None:
    return os.getenv("EVAL_DATABASE_URL") or settings.EVAL_DATABASE_URL or os.getenv("DATABASE_URL")


async def _eval_store():
    database_url = _eval_database_url()
    if not database_url:
        return None
    try:
        from evals.persistence.postgres import EvalPersistenceStore, is_supported_eval_database_url

        if not is_supported_eval_database_url(database_url):
            return None
        return EvalPersistenceStore(database_url)
    except Exception:
        return None


def _report_candidates(base_dir: Path) -> list[Path]:
    if not base_dir.exists() or not base_dir.is_dir():
        return []
    reports = sorted(
        [p for p in base_dir.glob("eval_report_*.json") if p.is_file()],
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    latest = base_dir / "latest.json"
    if latest.is_file():
        reports.insert(0, latest)
    return reports


def _safe_report_path(base_dir: Path, report: str) -> Path:
    if "/" in report or "\\" in report or report in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="invalid report name")
    path = (base_dir / report).resolve()
    try:
        path.relative_to(base_dir)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="invalid report path") from exc
    return path


def _load_report(base_dir: Path, report: str) -> tuple[Path, dict[str, Any]]:
    path = _safe_report_path(base_dir, report)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="report not found")
    try:
        return path, json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid report JSON: {path.name}") from exc


def _is_case_failed(result: dict[str, Any]) -> bool:
    return float(result.get("success_rate") or 0.0) < 1.0


def _failure_class_for_trial(trial: dict[str, Any]) -> str:
    existing = trial.get("failure_class")
    if existing:
        return str(existing)
    text = " ".join(str(part) for part in (trial.get("error_reason"), trial.get("error")) if part).lower()
    if not text and not trial.get("threshold_failures"):
        return "none"
    if any(token in text for token in ("provider", "api key", "unauthorized", "connection", "connect", "model", "timeout")):
        return "provider"
    if any(token in text for token in ("tool", "amap", "map", "http 4", "http 5", "not found")):
        return "tool_api"
    if any(token in text for token in ("evaluator", "missing weighted metrics", "schema", "eval")):
        return "eval_framework"
    return "agent_quality"


def _report_summary(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    results = data.get("results", []) if isinstance(data.get("results"), list) else []
    failed_cases = sum(1 for result in results if _is_case_failed(result))
    p0_failed_cases = sum(
        1 for result in results
        if result.get("priority") == "p0" and _is_case_failed(result)
    )
    metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
    return {
        "name": path.name,
        "timestamp": data.get("timestamp"),
        "total_cases": data.get("total_cases", len(results)),
        "total_trials": data.get("total_trials", sum(len(r.get("trials", [])) for r in results)),
        "overall_success_rate": data.get("overall_success_rate", 0.0),
        "failed_cases": failed_cases,
        "p0_failed_cases": p0_failed_cases,
        "duration_seconds": data.get("duration_seconds", 0.0),
        "suite": metadata.get("suite"),
        "runner": metadata.get("runner"),
        "size_bytes": path.stat().st_size,
        "modified_at": path.stat().st_mtime,
    }


def _metric_averages(data: dict[str, Any]) -> dict[str, float]:
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for result in data.get("results", []) if isinstance(data.get("results"), list) else []:
        scores = result.get("avg_scores", {})
        if not isinstance(scores, dict):
            continue
        for metric, value in scores.items():
            if isinstance(value, (int, float)):
                totals[metric] = totals.get(metric, 0.0) + float(value)
                counts[metric] = counts.get(metric, 0) + 1
    return {metric: totals[metric] / counts[metric] for metric in totals if counts.get(metric)}


def _case_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = data.get("results", []) if isinstance(data.get("results"), list) else []
    return {str(result.get("case_id")): result for result in results if result.get("case_id")}


def _score_delta_items(
    baseline: dict[str, dict[str, Any]],
    candidate: dict[str, dict[str, Any]],
    *,
    drop: bool,
    min_abs_delta: float = 0.01,
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline) & set(candidate)):
        base_scores = baseline[case_id].get("avg_scores", {})
        cand_scores = candidate[case_id].get("avg_scores", {})
        if not isinstance(base_scores, dict) or not isinstance(cand_scores, dict):
            continue
        for metric in sorted(set(base_scores) & set(cand_scores)):
            base_value = base_scores.get(metric)
            cand_value = cand_scores.get(metric)
            if not isinstance(base_value, (int, float)) or not isinstance(cand_value, (int, float)):
                continue
            delta = float(cand_value) - float(base_value)
            if drop and delta <= -min_abs_delta:
                items.append({"case_id": case_id, "metric": metric, "baseline": base_value, "candidate": cand_value, "delta": delta})
            if not drop and delta >= min_abs_delta:
                items.append({"case_id": case_id, "metric": metric, "baseline": base_value, "candidate": cand_value, "delta": delta})
    return items


def _breakdown_delta(
    baseline: dict[str, Any],
    candidate: dict[str, Any],
    key: str,
) -> dict[str, dict[str, float]]:
    base = baseline.get(key, {}) if isinstance(baseline.get(key), dict) else {}
    cand = candidate.get(key, {}) if isinstance(candidate.get(key), dict) else {}
    result: dict[str, dict[str, float]] = {}
    for name in sorted(set(base) | set(cand)):
        base_success = float((base.get(name) or {}).get("success_rate") or 0.0)
        cand_success = float((cand.get(name) or {}).get("success_rate") or 0.0)
        result[name] = {
            "baseline": base_success,
            "candidate": cand_success,
            "delta": cand_success - base_success,
        }
    return result


def _failure_summary_from_results(results: list[dict[str, Any]]) -> dict[str, Any]:
    summary: dict[str, Any] = {
        "by_error_reason": {},
        "by_case": {},
        "by_metric": {},
        "by_scene": {},
        "by_category": {},
        "by_tool": {},
        "by_worker": {},
        "by_failure_class": {},
    }
    for result in results:
        failed = _is_case_failed(result)
        if failed:
            summary["by_case"][result.get("case_id")] = {
                "success_rate": result.get("success_rate", 0.0),
                "category": result.get("category"),
                "scene": result.get("scene"),
            }
            scene = result.get("scene")
            category = result.get("category")
            if scene:
                summary["by_scene"][scene] = summary["by_scene"].get(scene, 0) + 1
            if category:
                summary["by_category"][category] = summary["by_category"].get(category, 0) + 1
        for trial in result.get("trials", []) if isinstance(result.get("trials"), list) else []:
            failure_class = _failure_class_for_trial(trial)
            if failure_class != "none":
                summary["by_failure_class"][failure_class] = summary["by_failure_class"].get(failure_class, 0) + 1
            reason = trial.get("error_reason")
            if reason:
                summary["by_error_reason"][reason] = summary["by_error_reason"].get(reason, 0) + 1
            worker = trial.get("actual_worker")
            if worker and (failed or failure_class != "none"):
                summary["by_worker"][worker] = summary["by_worker"].get(worker, 0) + 1
            for tool in trial.get("tool_calls", []) if isinstance(trial.get("tool_calls"), list) else []:
                if failed or failure_class != "none":
                    summary["by_tool"][tool] = summary["by_tool"].get(tool, 0) + 1
            for failure in trial.get("threshold_failures", []) if isinstance(trial.get("threshold_failures"), list) else []:
                metric = failure.get("metric")
                if metric:
                    summary["by_metric"][metric] = summary["by_metric"].get(metric, 0) + 1
    return summary


@router.get("/eval-access")
async def check_eval_access(admin: dict[str, str | None] = Depends(require_eval_admin)):
    """Return the current user's eval access status for frontend gating."""
    return {
        "allowed": True,
        "user_id": admin.get("user_id"),
        "email": admin.get("email"),
        "phone": admin.get("phone"),
    }


@router.get("/eval-hub/overview")
async def get_eval_hub_overview(
    window: str = Query("24h", pattern="^(5m|1h|24h|7d)$"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import eval_hub_overview
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        data = await eval_hub_overview(session, since=parse_window_start(window))
        await session.commit()
    data["window"] = window
    return data


@router.get("/eval-hub/live-sessions")
async def list_eval_hub_live_sessions(
    window: str = Query("24h", pattern="^(5m|1h|24h|7d)$"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_eval_hub_live_sessions
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        return await list_eval_hub_live_sessions(session, since=parse_window_start(window), limit=limit, offset=offset)


@router.get("/eval-hub/live-sessions/{session_id}")
async def get_eval_hub_live_session(
    session_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import load_eval_hub_live_session
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        detail = await load_eval_hub_live_session(session, session_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="session not found")
    return detail


@router.get("/eval-hub/traces")
async def list_eval_hub_traces(
    window: str = Query("24h", pattern="^(5m|1h|24h|7d)$"),
    scene: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_eval_hub_traces
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        return await list_eval_hub_traces(
            session,
            since=parse_window_start(window),
            scene=scene,
            status=status,
            limit=limit,
            offset=offset,
        )


@router.post("/eval-hub/traces/{trace_id}/dataset-cases")
async def create_eval_hub_dataset_case_from_trace(
    trace_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_dataset_case_from_trace
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import ConversationRun

    async with eval_session() as session:
        run = await session.scalar(select(ConversationRun).where((ConversationRun.trace_id == trace_id) | (ConversationRun.id == trace_id)))
        if not run:
            raise HTTPException(status_code=404, detail="trace not found")
        case = await create_dataset_case_from_trace(
            session,
            run_id=run.id,
            dataset_name=str(payload.get("dataset") or "regression"),
            version=str(payload.get("version") or "draft"),
            priority=str(payload.get("priority") or "p1"),
            category=str(payload.get("category") or "regression"),
            owner=admin.get("user_id"),
            review_status=str(payload.get("review_status") or "draft"),
        )
        await session.commit()
    return {"case": case}


@router.get("/eval-hub/traces/{trace_id}")
async def get_eval_hub_trace(
    trace_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import load_trace_detail_by_trace_id
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        detail = await load_trace_detail_by_trace_id(session, trace_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return detail


@router.get("/eval-hub/datasets")
async def list_eval_hub_datasets(
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await list_eval_datasets(_admin)


@router.post("/eval-hub/datasets")
async def create_eval_hub_dataset(
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_dataset_version
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        dataset = await create_dataset_version(
            session,
            dataset_name=str(payload.get("name") or payload.get("dataset") or "regression"),
            version=str(payload.get("version") or "draft"),
            status=str(payload.get("status") or "draft"),
            created_by=admin.get("user_id"),
        )
        await session.commit()
    return {"dataset": dataset}


@router.get("/eval-hub/datasets/{dataset}/cases")
async def list_eval_hub_dataset_cases(
    dataset: str,
    version: str | None = Query(None),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    if version is None:
        return await list_eval_dataset_cases(dataset, _admin)
    from app.agent.monitoring import list_persisted_dataset_cases
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        cases = await list_persisted_dataset_cases(session, dataset, version=version)
    return {"suite": dataset, "version": version, "total_cases": len(cases), "cases": cases}


@router.post("/eval-hub/datasets/{dataset}/cases")
async def create_eval_hub_dataset_case(
    dataset: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from uuid import uuid4

    from app.agent.monitoring import dataset_case_summary, ensure_eval_dataset
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalDatasetCase

    case = payload.get("case") if isinstance(payload.get("case"), dict) else payload
    case_id = str(case.get("id") or case.get("case_id") or f"manual-{uuid4().hex[:12]}")
    async with eval_session() as session:
        ds = await ensure_eval_dataset(
            session,
            name=dataset,
            version=str(payload.get("version") or "draft"),
            suite=dataset,
            status="draft",
            created_by=admin.get("user_id"),
        )
        item = EvalDatasetCase(
            id=str(uuid4()),
            dataset_id=ds.id,
            case_id=case_id,
            source=str(payload.get("source") or "manual"),
            case_json={**case, "id": case_id},
            scene=case.get("scene"),
            category=case.get("category"),
            priority=case.get("priority") or "p1",
            owner=admin.get("user_id"),
            review_status=str(payload.get("review_status") or "draft"),
        )
        session.add(item)
        await session.commit()
        result = dataset_case_summary(ds, item)
    return {"case": result}


@router.get("/eval-hub/datasets/{dataset}/versions")
async def list_eval_hub_dataset_versions(
    dataset: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await list_eval_dataset_versions(dataset, _admin)


@router.post("/eval-hub/datasets/{dataset}/versions")
async def create_eval_hub_dataset_version(
    dataset: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await create_eval_dataset_version(dataset, payload, admin)


@router.get("/eval-hub/experiments")
async def list_eval_hub_experiments(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_experiments
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        return await list_experiments(session, limit=limit, offset=offset)


@router.post("/eval-hub/experiments")
async def create_eval_hub_experiment(
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_experiment
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        experiment = await create_experiment(session, payload, owner=admin.get("user_id"))
        await session.commit()
    return {"experiment": experiment}


@router.get("/eval-hub/experiments/{experiment_id}")
async def get_eval_hub_experiment(
    experiment_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import load_experiment
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        detail = await load_experiment(session, experiment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return detail


@router.post("/eval-hub/experiments/{experiment_id}/runs")
async def add_eval_hub_experiment_run(
    experiment_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import add_experiment_run
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        run = await add_experiment_run(session, experiment_id, payload)
        if run is None:
            raise HTTPException(status_code=404, detail="experiment not found")
        await session.commit()
    return {"run": run}


@router.get("/eval-hub/experiments/{experiment_id}/compare")
async def compare_eval_hub_experiment(
    experiment_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import load_experiment
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        detail = await load_experiment(session, experiment_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    runs = detail.get("runs", [])
    baseline = next((row for row in runs if row.get("role") == "baseline"), None)
    candidate = next((row for row in runs if row.get("role") == "candidate"), None)
    if not baseline or not candidate or not baseline.get("report_name") or not candidate.get("report_name"):
        return {"experiment_id": experiment_id, "available": False, "reason": "baseline and candidate report_name are required", **detail}
    store = await _eval_store()
    if store is None:
        return {"experiment_id": experiment_id, "available": False, "reason": "eval database unavailable", **detail}
    try:
        comparison = await store.compare_reports(str(baseline["report_name"]), str(candidate["report_name"]))
    finally:
        await store.close()
    return {"experiment_id": experiment_id, "available": True, "baseline": baseline, "candidate": candidate, "comparison": comparison}


@router.get("/eval-hub/evaluators")
async def list_eval_hub_evaluators(
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_evaluator_definitions
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        evaluators = await list_evaluator_definitions(session)
        await session.commit()
    return {"evaluators": evaluators}


@router.post("/eval-hub/evaluators")
async def create_eval_hub_evaluator(
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_evaluator_definition
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        evaluator = await create_evaluator_definition(session, payload, owner=admin.get("user_id"))
        await session.commit()
    return {"evaluator": evaluator}


@router.post("/eval-hub/playground/runs")
async def create_eval_hub_playground_run(
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_playground_run
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        run = await create_playground_run(session, payload, owner=admin.get("user_id"))
        await session.commit()
    return {"run": run}


@router.get("/eval-hub/playground/runs/{run_id}")
async def get_eval_hub_playground_run(
    run_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import load_playground_run
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        run = await load_playground_run(session, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="playground run not found")
    return {"run": run}


@router.get("/eval-hub/simulations/scenarios")
async def list_eval_hub_simulation_scenarios(
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_simulation_scenarios
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        scenarios = await list_simulation_scenarios(session)
    return {"scenarios": scenarios}


@router.post("/eval-hub/simulations/scenarios")
async def create_eval_hub_simulation_scenario(
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_simulation_scenario
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        scenario = await create_simulation_scenario(session, payload, owner=admin.get("user_id"))
        await session.commit()
    return {"scenario": scenario}


@router.post("/eval-hub/simulations/scenarios/{scenario_id}/runs")
async def create_eval_hub_simulation_run(
    scenario_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_simulation_run
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        max_turns = payload.get("max_turns")
        run = await create_simulation_run(
            session,
            scenario_id,
            max_turns_override=int(max_turns) if max_turns not in (None, "") else None,
            runner=str(payload.get("runner") or "deterministic"),
        )
        if run is None:
            raise HTTPException(status_code=404, detail="scenario not found")
        await session.commit()
    return {"run": run}


@router.get("/eval-hub/simulations/scenarios/{scenario_id}/runs/{run_id}")
async def get_eval_hub_simulation_run(
    scenario_id: str,
    run_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import load_simulation_run
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        run = await load_simulation_run(session, scenario_id, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="simulation run not found")
    return {"run": run}


@router.post("/eval-hub/simulations/scenarios/{scenario_id}/runs/{run_id}/to-dataset")
async def convert_eval_hub_simulation_run_to_dataset(
    scenario_id: str,
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import simulation_run_to_dataset_case
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        case = await simulation_run_to_dataset_case(
            session,
            scenario_id=scenario_id,
            run_id=run_id,
            dataset_name=str(payload.get("dataset") or "regression"),
            version=str(payload.get("version") or "draft"),
            owner=admin.get("user_id"),
            priority=str(payload.get("priority") or "p1"),
        )
        if case is None:
            raise HTTPException(status_code=404, detail="simulation run not found")
        await session.commit()
    return {"case": case}


@router.get("/eval-hub/annotation/queue")
async def list_eval_hub_annotation_queue(
    decision: str = Query("pending"),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await list_monitoring_reviews(decision=decision, limit=limit, offset=offset, _admin=_admin)


@router.post("/eval-hub/annotation/{run_id}")
async def update_eval_hub_annotation(
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await upsert_monitoring_review(run_id, payload, admin)


@router.get("/eval-hub/monitoring/overview")
async def get_eval_hub_monitoring_overview(
    window: str = Query("24h", pattern="^(5m|1h|24h|7d)$"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await get_monitoring_overview(window, _admin)


@router.get("/eval-hub/alerts")
async def list_eval_hub_alerts(
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await get_eval_alerts(status=status, limit=limit, offset=offset, _admin=_admin)


@router.post("/eval-hub/alerts/{alert_id}/ack")
async def ack_eval_hub_alert(
    alert_id: str,
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await acknowledge_eval_alert(alert_id, admin)


@router.post("/eval-hub/alerts/{alert_id}/resolve")
async def resolve_eval_hub_alert(
    alert_id: str,
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    return await resolve_eval_alert(alert_id, admin)


@router.get("/eval-report")
async def get_eval_report(
    report: str = Query("latest.json", description="Report JSON filename under EVAL_RESULTS_DIR"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """Return a saved evaluation report for the static eval dashboard."""
    base_dir = _eval_results_dir()
    reports = _report_candidates(base_dir)
    report_list = [
        {
            "name": path.name,
            "size_bytes": path.stat().st_size,
            "modified_at": path.stat().st_mtime,
        }
        for path in reports
    ]

    if "/" in report or "\\" in report or report in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="invalid report name")

    store = await _eval_store()
    if store is not None:
        try:
            data = await store.load_report(report)
            db_reports = await store.list_reports()
            if data is not None:
                return {
                    "available": True,
                    "source": "db",
                    "results_dir": str(base_dir),
                    "selected": report,
                    "reports": db_reports,
                    "report": data,
                }
        except Exception:
            pass
        finally:
            await store.close()

    selected = _safe_report_path(base_dir, report)
    if not selected.is_file():
        return {
            "available": False,
            "source": "json",
            "results_dir": str(base_dir),
            "selected": selected.name,
            "reports": report_list,
            "message": "No evaluation report found. Run run_eval.py with --output-dir pointing to this directory.",
        }

    try:
        data = json.loads(selected.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=500, detail=f"invalid report JSON: {selected.name}") from exc

    return {
        "available": True,
        "source": "json",
        "results_dir": str(base_dir),
        "selected": selected.name,
        "reports": report_list,
        "report": data,
    }


@router.get("/eval-reports")
async def list_eval_reports(
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    base_dir = _eval_results_dir()
    store = await _eval_store()
    if store is not None:
        try:
            reports = await store.list_reports()
            if reports:
                return {"source": "db", "results_dir": str(base_dir), "reports": reports}
        except Exception:
            pass
        finally:
            await store.close()

    reports = []
    for path in _report_candidates(base_dir):
        try:
            _, data = _load_report(base_dir, path.name)
        except HTTPException:
            continue
        reports.append(_report_summary(path, data))
    reports.sort(key=lambda item: float(item.get("modified_at") or 0.0), reverse=True)
    return {"source": "json", "results_dir": str(base_dir), "reports": reports}


@router.get("/eval-runs/{run_id}/stability")
async def get_eval_run_stability(
    run_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalRun

    store = await _eval_store()
    if store is not None:
        try:
            report = await store.load_report(run_id)
            if report is not None:
                return {"run_id": run_id, "source": "db_report", "stability": _stability_from_report(report)}
        except Exception:
            pass
        finally:
            await store.close()

    async with eval_session() as session:
        row = await session.scalar(select(EvalRun).where((EvalRun.id == run_id) | (EvalRun.report_name == run_id)))
    if row and isinstance(row.raw_report_json, dict):
        return {"run_id": run_id, "source": "db_raw", "stability": _stability_from_report(row.raw_report_json)}

    base_dir = _eval_results_dir()
    try:
        _, report = _load_report(base_dir, run_id)
    except HTTPException:
        raise HTTPException(status_code=404, detail="eval run not found")
    return {"run_id": run_id, "source": "json", "stability": _stability_from_report(report)}


def _component_eval_report(component: str, dataset: str, owner: str | None = None) -> dict[str, Any]:
    from evals.component_eval import build_component_report

    return build_component_report(component, dataset, owner=owner)


@router.get("/eval-component-runs")
async def list_eval_component_runs(
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalRun

    async with eval_session() as session:
        rows = (await session.execute(
            select(EvalRun)
            .where(EvalRun.runner == "component")
            .order_by(desc(EvalRun.timestamp))
            .offset(offset)
            .limit(limit)
        )).scalars().all()
    return {
        "total": len(rows),
        "records": [
            {
                "id": row.id,
                "report_name": row.report_name,
                "component": (row.suite or "").replace("component:", ""),
                "suite": row.suite,
                "runner": row.runner,
                "success_rate": row.overall_success_rate,
                "total_cases": row.total_cases,
                "timestamp": row.timestamp.isoformat() if row.timestamp else None,
                "report": row.raw_report_json,
            }
            for row in rows
        ],
    }


@router.post("/eval-component-runs")
async def create_eval_component_run(
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from uuid import uuid4

    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalRun

    component = str(payload.get("component") or "router")
    if component not in {"router", "tool", "rag", "schema", "llm"}:
        raise HTTPException(status_code=422, detail="invalid component")
    dataset = str(payload.get("dataset") or "component-regression")
    report = _component_eval_report(component, dataset, owner=admin.get("user_id"))
    run_id = str(uuid4())
    report_name = f"component_{component}_{run_id}.json"
    async with eval_session() as session:
        row = EvalRun(
            id=run_id,
            report_name=report_name,
            timestamp=datetime.now(timezone.utc),
            suite=f"component:{component}",
            runner="component",
            overall_success_rate=float(report.get("overall_success_rate") or 0.0),
            total_cases=int(report.get("total_cases") or 0),
            total_trials=int(report.get("total_trials") or 0),
            duration_seconds=float(report.get("duration_seconds") or 0.0),
            raw_report_json=report,
            owner=admin.get("user_id"),
            tags_json={"component": component, "dataset": dataset},
        )
        session.add(row)
        await session.commit()
    return {"run": {"id": run_id, "report_name": report_name, "component": component, "dataset": dataset, "report": report}}


@router.get("/eval-report/case")
async def get_eval_report_case(
    report: str = Query("latest.json", description="Report JSON filename under EVAL_RESULTS_DIR"),
    case_id: str = Query(..., description="Eval case id"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    base_dir = _eval_results_dir()
    if "/" in report or "\\" in report or report in {"", ".", ".."}:
        raise HTTPException(status_code=400, detail="invalid report name")
    store = await _eval_store()
    if store is not None:
        try:
            detail = await store.load_case(report, case_id)
            if detail is not None:
                detail["source"] = "db"
                return detail
        except Exception:
            pass
        finally:
            await store.close()

    selected, data = _load_report(base_dir, report)
    cases = _case_map(data)
    case = cases.get(case_id)
    if case is None:
        raise HTTPException(status_code=404, detail="case not found")
    trials = []
    for trial in case.get("trials", []) if isinstance(case.get("trials"), list) else []:
        trial_copy = dict(trial)
        trial_copy.setdefault("failure_class", _failure_class_for_trial(trial_copy))
        trial_copy.setdefault("trace_timeline", [])
        trials.append(trial_copy)
    return {
        "source": "json",
        "report": selected.name,
        "case": case,
        "trials": trials,
    }


@router.get("/eval-report/compare")
async def compare_eval_reports(
    baseline: str = Query(..., description="Baseline report JSON filename"),
    candidate: str = Query(..., description="Candidate report JSON filename"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    base_dir = _eval_results_dir()
    for name in (baseline, candidate):
        if "/" in name or "\\" in name or name in {"", ".", ".."}:
            raise HTTPException(status_code=400, detail="invalid report name")
    store = await _eval_store()
    if store is not None:
        try:
            compared = await store.compare_reports(baseline, candidate)
            if compared is not None:
                compared["source"] = "db"
                return compared
        except Exception:
            pass
        finally:
            await store.close()

    baseline_path, baseline_data = _load_report(base_dir, baseline)
    candidate_path, candidate_data = _load_report(base_dir, candidate)
    baseline_summary = _report_summary(baseline_path, baseline_data)
    candidate_summary = _report_summary(candidate_path, candidate_data)
    baseline_cases = _case_map(baseline_data)
    candidate_cases = _case_map(candidate_data)

    regressions = []
    fixes = []
    for case_id in sorted(set(baseline_cases) & set(candidate_cases)):
        base_failed = _is_case_failed(baseline_cases[case_id])
        cand_failed = _is_case_failed(candidate_cases[case_id])
        if not base_failed and cand_failed:
            regressions.append({"case_id": case_id, "baseline": baseline_cases[case_id], "candidate": candidate_cases[case_id]})
        if base_failed and not cand_failed:
            fixes.append({"case_id": case_id, "baseline": baseline_cases[case_id], "candidate": candidate_cases[case_id]})

    baseline_metrics = _metric_averages(baseline_data)
    candidate_metrics = _metric_averages(candidate_data)
    metric_delta = {}
    for metric in sorted(set(baseline_metrics) | set(candidate_metrics)):
        base_value = baseline_metrics.get(metric, 0.0)
        cand_value = candidate_metrics.get(metric, 0.0)
        metric_delta[metric] = {
            "baseline": base_value,
            "candidate": cand_value,
            "delta": cand_value - base_value,
        }

    return {
        "source": "json",
        "baseline": baseline_path.name,
        "candidate": candidate_path.name,
        "summary_delta": {
            "overall_success_rate": float(candidate_summary.get("overall_success_rate") or 0.0) - float(baseline_summary.get("overall_success_rate") or 0.0),
            "failed_cases": int(candidate_summary.get("failed_cases") or 0) - int(baseline_summary.get("failed_cases") or 0),
            "p0_failed_cases": int(candidate_summary.get("p0_failed_cases") or 0) - int(baseline_summary.get("p0_failed_cases") or 0),
            "duration_seconds": float(candidate_summary.get("duration_seconds") or 0.0) - float(baseline_summary.get("duration_seconds") or 0.0),
        },
        "baseline_summary": baseline_summary,
        "candidate_summary": candidate_summary,
        "case_changes": {
            "regressions": regressions,
            "fixes": fixes,
            "score_drops": _score_delta_items(baseline_cases, candidate_cases, drop=True),
            "score_gains": _score_delta_items(baseline_cases, candidate_cases, drop=False),
        },
        "scene_delta": _breakdown_delta(baseline_data, candidate_data, "scene_breakdown"),
        "category_delta": _breakdown_delta(baseline_data, candidate_data, "category_breakdown"),
        "metric_delta": metric_delta,
    }


@router.post("/eval-jobs")
async def create_eval_run_job(
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.eval_jobs import EvalJobConflict, create_eval_job, schedule_eval_job, serialize_eval_job
    from app.infra.eval_db import eval_session, init_eval_db

    await init_eval_db()
    async with eval_session() as session:
        try:
            job = await create_eval_job(session, payload=payload, requested_by=admin.get("user_id"))
        except EvalJobConflict as exc:
            raise HTTPException(
                status_code=409,
                detail={
                    "message": "an eval job is already queued or running",
                    "active_job": serialize_eval_job(exc.active_job, include_logs=True),
                },
            ) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        data = serialize_eval_job(job, include_logs=True)
        await session.commit()
    schedule_eval_job(data["id"])
    return {"job": data}


@router.get("/eval-jobs")
async def list_eval_run_jobs(
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.eval_jobs import list_eval_jobs, serialize_eval_job
    from app.infra.eval_db import eval_session, init_eval_db

    await init_eval_db()
    async with eval_session() as session:
        total, rows = await list_eval_jobs(session, status=status, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "records": [serialize_eval_job(row) for row in rows]}


@router.get("/eval-jobs/{job_id}")
async def get_eval_run_job(
    job_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.eval_jobs import load_eval_job, serialize_eval_job
    from app.infra.eval_db import eval_session, init_eval_db

    await init_eval_db()
    async with eval_session() as session:
        job = await load_eval_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="eval job not found")
        data = serialize_eval_job(job, include_logs=True)
    return {"job": data}


@router.post("/eval-jobs/{job_id}/cancel")
async def cancel_eval_run_job(
    job_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.eval_jobs import cancel_eval_job, serialize_eval_job
    from app.infra.eval_db import eval_session, init_eval_db

    await init_eval_db()
    async with eval_session() as session:
        job = await cancel_eval_job(session, job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="eval job not found")
        data = serialize_eval_job(job, include_logs=True)
        await session.commit()
    return {"job": data}


# ---------------------------------------------------------------------------
# 评测数据集 / 在线监控 API
# ---------------------------------------------------------------------------


def _dataset_files_for_suite(dataset_dir: Path, suite: str) -> list[Path]:
    if suite == "quick":
        return [dataset_dir / "fixture_cases.json"]
    if suite == "full":
        return [dataset_dir / "full_cases.json", dataset_dir / "golden_cases.jsonl"]
    if suite == "live-smoke":
        return [dataset_dir / "full_cases.json", dataset_dir / "golden_cases.jsonl"]
    raise HTTPException(status_code=404, detail="dataset not found")


def _load_dataset_cases(suite: str) -> list[dict[str, Any]]:
    from evals.runners.harness import EvalHarness, HarnessConfig

    dataset_dir = (Path(__file__).resolve().parents[3] / "evals" / "datasets").resolve()
    harness = EvalHarness(HarnessConfig(dataset_dir=str(dataset_dir), suite=suite, runner="fixture"))
    cases = []
    for path in _dataset_files_for_suite(dataset_dir, suite):
        if path.exists():
            cases.extend(harness._load_case_file(path))  # noqa: SLF001 - reuse eval loader for schema compatibility.
    if suite == "quick":
        cases = [case for case in cases if case.priority in {"p0", "p1"}]
    elif suite == "live-smoke":
        preferred_ids = {"food-001", "chef-001", "route-001", "travel-001", "chat-001"}
        preferred = [case for case in cases if case.id in preferred_ids]
        cases = (preferred or cases)[:5]
    return [case.to_dict() for case in cases]


def _dataset_case_summary(case: dict[str, Any]) -> dict[str, Any]:
    expectations = case.get("expectations") if isinstance(case.get("expectations"), dict) else {}
    scoring = case.get("scoring") if isinstance(case.get("scoring"), dict) else {}
    return {
        "case_id": case.get("id"),
        "task": case.get("task"),
        "scene": case.get("scene"),
        "category": case.get("category"),
        "priority": case.get("priority"),
        "difficulty": case.get("difficulty"),
        "tags": case.get("tags") or [],
        "expectations_summary": {
            "expected_scene": expectations.get("expected_scene"),
            "expected_tools": expectations.get("expected_tools") or expectations.get("tool_calls"),
            "must_include": expectations.get("must_include"),
            "must_not_include": expectations.get("must_not_include"),
        },
        "scoring_summary": {
            "metrics": sorted(scoring.keys()),
            "weights": scoring,
        },
    }


def _trial_passed_for_stability(trial: dict[str, Any]) -> bool:
    if trial.get("error") or trial.get("error_reason"):
        return False
    if trial.get("threshold_failures") or trial.get("missing_metrics"):
        return False
    try:
        score = float(trial.get("weighted_score") or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return score >= 0.7


def _variance(values: list[float]) -> float:
    if len(values) <= 1:
        return 0.0
    mean = sum(values) / len(values)
    return round(sum((value - mean) ** 2 for value in values) / len(values), 6)


def _stability_from_report(report: dict[str, Any]) -> dict[str, Any]:
    if isinstance(report.get("stability"), dict):
        return report["stability"]
    results = report.get("results") if isinstance(report.get("results"), list) else []
    if not results:
        return {"k": 0, "pass_at_k": 0.0, "pass_all_k": 0.0, "trial_variance": 0.0, "flaky_cases": [], "cases": []}
    case_rows = []
    flaky_cases = []
    pass_at = 0
    pass_all = 0
    variances = []
    k = 0
    for result in results:
        trials = result.get("trials") if isinstance(result.get("trials"), list) else []
        passes = [_trial_passed_for_stability(trial) for trial in trials if isinstance(trial, dict)]
        scores = []
        for trial in trials:
            if isinstance(trial, dict):
                try:
                    scores.append(float(trial.get("weighted_score") or 0.0))
                except (TypeError, ValueError):
                    scores.append(0.0)
        k = max(k, len(trials))
        passed_once = any(passes) if passes else False
        passed_all = all(passes) if passes else False
        pass_at += 1 if passed_once else 0
        pass_all += 1 if passed_all else 0
        variance = _variance(scores)
        variances.append(variance)
        flaky = bool(passes) and (any(passes) != all(passes) or variance >= 0.05)
        row = {
            "case_id": result.get("case_id"),
            "trials": len(trials),
            "pass_count": sum(1 for value in passes if value),
            "pass_at_k": passed_once,
            "pass_all_k": passed_all,
            "scores": scores,
            "variance": variance,
            "flaky": flaky,
        }
        case_rows.append(row)
        if flaky:
            flaky_cases.append(row)
    total = len(results)
    return {
        "k": k,
        "pass_at_k": round(pass_at / total, 4) if total else 0.0,
        "pass_all_k": round(pass_all / total, 4) if total else 0.0,
        "trial_variance": round(sum(variances) / len(variances), 6) if variances else 0.0,
        "flaky_cases": flaky_cases,
        "cases": case_rows,
    }


@router.get("/eval-datasets")
async def list_eval_datasets(
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    datasets = []
    for suite in ("quick", "full", "live-smoke"):
        cases = _load_dataset_cases(suite)
        by_scene: dict[str, int] = {}
        by_category: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        for case in cases:
            for key, target in (("scene", by_scene), ("category", by_category), ("priority", by_priority)):
                value = str(case.get(key) or "unknown")
                target[value] = target.get(value, 0) + 1
        datasets.append({
            "suite": suite,
            "name": suite,
            "version": "file",
            "status": "active",
            "total_cases": len(cases),
            "by_scene": by_scene,
            "by_category": by_category,
            "by_priority": by_priority,
        })
    try:
        from app.agent.monitoring import list_persisted_datasets
        from app.infra.eval_db import eval_session

        async with eval_session() as session:
            persisted = await list_persisted_datasets(session)
        datasets.extend(persisted)
    except Exception:
        pass
    return {"datasets": datasets}


@router.get("/eval-datasets/{dataset}/cases")
async def list_eval_dataset_cases(
    dataset: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    cases = [_dataset_case_summary(case) for case in _load_dataset_cases(dataset)] if dataset in {"quick", "full", "live-smoke"} else []
    try:
        from app.agent.monitoring import list_persisted_dataset_cases
        from app.infra.eval_db import eval_session

        async with eval_session() as session:
            persisted = await list_persisted_dataset_cases(session, dataset)
        cases.extend(persisted)
    except Exception:
        pass
    if not cases and dataset not in {"quick", "full", "live-smoke"}:
        raise HTTPException(status_code=404, detail="dataset not found")
    return {"suite": dataset, "total_cases": len(cases), "cases": cases}


@router.get("/eval-datasets/{dataset}/versions")
async def list_eval_dataset_versions(
    dataset: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_dataset_versions
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        versions = await list_dataset_versions(session, dataset)
    return {"dataset": dataset, "versions": versions}


@router.post("/eval-datasets/{dataset}/versions")
async def create_eval_dataset_version(
    dataset: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_dataset_version
    from app.infra.eval_db import eval_session

    version = str(payload.get("version") or "draft")
    status = str(payload.get("status") or "draft")
    if status not in {"draft", "reviewing", "active", "archived"}:
        raise HTTPException(status_code=422, detail="invalid dataset status")
    async with eval_session() as session:
        item = await create_dataset_version(
            session,
            dataset_name=dataset,
            version=version,
            status=status,
            created_by=admin.get("user_id"),
        )
        await session.commit()
    return {"dataset": item}


@router.post("/eval-datasets/{dataset}/versions/{version}/activate")
async def activate_eval_dataset_version(
    dataset: str,
    version: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import activate_dataset_version
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        item = await activate_dataset_version(session, dataset_name=dataset, version=version)
        if item is None:
            raise HTTPException(status_code=404, detail="dataset version not found")
        await session.commit()
    return {"dataset": item}


@router.post("/eval-datasets/{dataset}/cases/from-trace")
async def create_eval_case_from_trace(
    dataset: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import create_dataset_case_from_trace
    from app.infra.eval_db import eval_session

    run_id = str(payload.get("run_id") or "")
    if not run_id:
        raise HTTPException(status_code=422, detail="run_id is required")
    async with eval_session() as session:
        item = await create_dataset_case_from_trace(
            session,
            run_id=run_id,
            dataset_name=dataset,
            version=str(payload.get("version") or "draft"),
            priority=str(payload.get("priority") or "p1"),
            category=str(payload.get("category") or "regression"),
            owner=admin.get("user_id"),
            review_status=str(payload.get("review_status") or "draft"),
        )
        if item is None:
            raise HTTPException(status_code=404, detail="trace not found")
        await session.commit()
    return {"case": item}


@router.post("/eval-datasets/{dataset}/cases/generate")
async def generate_eval_dataset_cases(
    dataset: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import generate_dataset_cases
    from app.infra.eval_db import eval_session

    source = str(payload.get("source") or "manual")
    version = str(payload.get("version") or "draft")
    async with eval_session() as session:
        cases = await generate_dataset_cases(
            session,
            dataset_name=dataset,
            source=source,
            payload=payload,
            version=version,
            owner=admin.get("user_id"),
        )
        await session.commit()
    return {"dataset": dataset, "version": version, "source": source, "generated": len(cases), "cases": cases}


@router.patch("/eval-datasets/{dataset}/cases/{case_id}/review")
async def review_eval_dataset_case(
    dataset: str,
    case_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import review_dataset_case
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        item = await review_dataset_case(
            session,
            dataset_name=dataset,
            case_id=case_id,
            version=payload.get("version"),
            decision=str(payload.get("decision") or "reviewing"),
            reviewer=admin.get("user_id"),
            notes=payload.get("notes"),
        )
        if item is None:
            raise HTTPException(status_code=404, detail="dataset case not found")
        await session.commit()
    return {"case": item}


@router.get("/monitoring/overview")
async def get_monitoring_overview(
    window: str = Query("1h", pattern="^(5m|1h|24h|7d)$"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import aggregate_monitoring_overview
    from app.infra.eval_db import eval_session

    since = parse_window_start(window)
    async with eval_session() as session:
        data = await aggregate_monitoring_overview(session, since=since)
    data["window"] = window
    return data


@router.get("/monitoring/traces")
async def list_monitoring_traces(
    from_: datetime | None = Query(None, alias="from"),
    to: datetime | None = Query(None),
    session_id: str | None = Query(None),
    user_id: str | None = Query(None),
    scene: str | None = Query(None),
    worker: str | None = Query(None),
    tool: str | None = Query(None),
    status: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import conversation_run_summary, list_conversation_runs, load_chat_session_titles
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        total, rows = await list_conversation_runs(
            session,
            since=from_,
            until=to,
            session_id=session_id,
            user_id=user_id,
            scene=scene,
            worker=worker,
            status=status,
            tool=tool,
            limit=limit,
            offset=offset,
        )
        title_map = await load_chat_session_titles([row.session_id for row in rows])
    return {
        "total": total,
        "limit": limit,
        "offset": offset,
        "records": [conversation_run_summary(row, session_title=title_map.get(row.session_id)) for row in rows],
    }


@router.get("/monitoring/traces/{run_id}")
async def get_monitoring_trace(
    run_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import load_conversation_trace
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        detail = await load_conversation_trace(session, run_id)
    if detail is None:
        raise HTTPException(status_code=404, detail="trace not found")
    return detail


@router.get("/monitoring/failures")
async def get_monitoring_failures(
    window: str = Query("24h", pattern="^(5m|1h|24h|7d)$"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import aggregate_failures
    from app.infra.eval_db import eval_session

    since = parse_window_start(window)
    async with eval_session() as session:
        data = await aggregate_failures(session, since=since)
    data["window"] = window
    return data


@router.get("/monitoring/cost-latency")
async def get_monitoring_cost_latency(
    window: str = Query("24h", pattern="^(5m|1h|24h|7d)$"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import aggregate_cost_latency
    from app.infra.eval_db import eval_session

    since = parse_window_start(window)
    async with eval_session() as session:
        data = await aggregate_cost_latency(session, since=since)
    data["window"] = window
    return data


@router.get("/monitoring/safety")
async def get_monitoring_safety(
    window: str = Query("24h", pattern="^(5m|1h|24h|7d)$"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import aggregate_safety
    from app.infra.eval_db import eval_session

    since = parse_window_start(window)
    async with eval_session() as session:
        data = await aggregate_safety(session, since=since)
    data["window"] = window
    return data


@router.get("/monitoring/reviews")
async def list_monitoring_reviews(
    decision: str | None = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_reviews
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        total, items = await list_reviews(session, decision=decision, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "records": items}


@router.post("/monitoring/reviews/{run_id}")
async def upsert_monitoring_review(
    run_id: str,
    payload: dict[str, Any] = Body(default_factory=dict),
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import upsert_review
    from app.infra.eval_db import eval_session

    decision = str(payload.get("decision") or "pending")
    if decision not in {"pending", "accepted", "rejected", "needs_followup", "converted_to_case"}:
        raise HTTPException(status_code=422, detail="invalid review decision")
    async with eval_session() as session:
        review = await upsert_review(
            session,
            run_id=run_id,
            reviewer_id=admin.get("user_id"),
            decision=decision,
            reason=payload.get("reason"),
            notes=payload.get("notes"),
            failure_reason=payload.get("failure_reason"),
            failure_tags=payload.get("failure_tags") if isinstance(payload.get("failure_tags"), list) else [],
            corrected_answer=payload.get("corrected_answer"),
            expected_behavior=payload.get("expected_behavior"),
            review_confidence=float(payload["review_confidence"]) if payload.get("review_confidence") is not None else None,
            dataset_candidate=bool(payload.get("dataset_candidate") or decision == "converted_to_case"),
        )
        if review is None:
            raise HTTPException(status_code=404, detail="trace not found")
        converted_case = None
        if decision == "converted_to_case" or payload.get("convert_to_case"):
            from app.agent.monitoring import create_dataset_case_from_trace

            converted_case = await create_dataset_case_from_trace(
                session,
                run_id=run_id,
                dataset_name=str(payload.get("dataset") or "regression"),
                version=str(payload.get("dataset_version") or "draft"),
                priority=str(payload.get("priority") or "p1"),
                category=str(payload.get("category") or "regression"),
                owner=admin.get("user_id"),
                review_status="approved" if decision == "converted_to_case" else "draft",
            )
        await session.commit()
    return {"review": review, "converted_case": converted_case}


def _extract_outcomes_from_report(data: dict[str, Any]) -> list[dict[str, Any]]:
    outcomes: list[dict[str, Any]] = []
    for result in data.get("results", []) if isinstance(data.get("results"), list) else []:
        case_id = result.get("case_id")
        for trial in result.get("trials", []) if isinstance(result.get("trials"), list) else []:
            for detail in trial.get("outcome_details", []) if isinstance(trial.get("outcome_details"), list) else []:
                outcomes.append({
                    "case_id": case_id,
                    "trial_number": trial.get("trial_number", 0),
                    **detail,
                })
    return outcomes


def _extract_judge_from_report(data: dict[str, Any]) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
    for result in data.get("results", []) if isinstance(data.get("results"), list) else []:
        case_id = result.get("case_id")
        for trial in result.get("trials", []) if isinstance(result.get("trials"), list) else []:
            judge_scores = trial.get("judge_scores") if isinstance(trial.get("judge_scores"), dict) else {}
            judge_reasons = trial.get("judge_reasons") if isinstance(trial.get("judge_reasons"), dict) else {}
            if not judge_scores and trial.get("llm_judge_skipped_reason"):
                results.append({
                    "case_id": case_id,
                    "trial_number": trial.get("trial_number", 0),
                    "metric": "llm_judge",
                    "score": None,
                    "reason": None,
                    "confidence": None,
                    "rubric_version": "v1",
                    "judge_model": metadata.get("judge_model"),
                    "skipped_reason": trial.get("llm_judge_skipped_reason"),
                })
            for metric, score in judge_scores.items():
                results.append({
                    "case_id": case_id,
                    "trial_number": trial.get("trial_number", 0),
                    "metric": metric,
                    "score": score,
                    "reason": judge_reasons.get(metric),
                    "confidence": None,
                    "rubric_version": "v1",
                    "judge_model": metadata.get("judge_model"),
                    "skipped_reason": None,
                })
    return results


@router.get("/eval-runs/{run_id}/outcomes")
async def get_eval_run_outcomes(
    run_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalOutcomeResult, EvalRun

    store = await _eval_store()
    if store is not None:
        try:
            report = await store.load_report(run_id)
            if report is not None:
                return {"run_id": run_id, "source": "eval_db_report", "outcomes": _extract_outcomes_from_report(report)}
        except Exception:
            pass
        finally:
            await store.close()

    async with eval_session() as session:
        rows = (await session.execute(
            select(EvalOutcomeResult).where(EvalOutcomeResult.run_id == run_id)
        )).scalars().all()
        if rows:
            return {
                "run_id": run_id,
                "source": "db",
                "outcomes": [
                    {
                        "case_id": row.case_id,
                        "trial_number": row.trial_number,
                        "verifier": row.verifier,
                        "score": row.score,
                        "passed": row.passed,
                        "failures": row.failures_json or [],
                        "details": row.details_json or {},
                    }
                    for row in rows
                ],
            }
        run = await session.scalar(select(EvalRun).where((EvalRun.id == run_id) | (EvalRun.report_name == run_id)))
    if run and isinstance(run.raw_report_json, dict):
        return {"run_id": run_id, "source": "report", "outcomes": _extract_outcomes_from_report(run.raw_report_json)}
    raise HTTPException(status_code=404, detail="eval run not found")


@router.get("/eval-runs/{run_id}/judge-results")
async def get_eval_run_judge_results(
    run_id: str,
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalJudgeResult, EvalRun

    store = await _eval_store()
    if store is not None:
        try:
            report = await store.load_report(run_id)
            if report is not None:
                return {"run_id": run_id, "source": "eval_db_report", "judge_results": _extract_judge_from_report(report)}
        except Exception:
            pass
        finally:
            await store.close()

    async with eval_session() as session:
        rows = (await session.execute(
            select(EvalJudgeResult).where(EvalJudgeResult.run_id == run_id)
        )).scalars().all()
        if rows:
            return {
                "run_id": run_id,
                "source": "db",
                "judge_results": [
                    {
                        "case_id": row.case_id,
                        "trial_number": row.trial_number,
                        "metric": row.metric,
                        "score": row.score,
                        "reason": row.reason,
                        "confidence": row.confidence,
                        "rubric_version": row.rubric_version,
                        "judge_model": row.judge_model,
                        "skipped_reason": row.skipped_reason,
                    }
                    for row in rows
                ],
            }
        run = await session.scalar(select(EvalRun).where((EvalRun.id == run_id) | (EvalRun.report_name == run_id)))
    if run and isinstance(run.raw_report_json, dict):
        return {"run_id": run_id, "source": "report", "judge_results": _extract_judge_from_report(run.raw_report_json)}
    raise HTTPException(status_code=404, detail="eval run not found")


@router.get("/eval-runs/judge-agreement")
async def get_global_judge_human_agreement(
    window: str = Query("30d", description="Time window for agreement calculation"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """Calculate global agreement between LLM Judge and Human Review."""
    from app.agent.monitoring import calculate_judge_human_agreement, parse_window_start
    from app.infra.eval_db import eval_session

    since = parse_window_start(window)
    async with eval_session() as session:
        return await calculate_judge_human_agreement(session, since=since, run_id=None)


@router.get("/eval-runs/{run_id}/judge-agreement")
async def get_judge_human_agreement(
    run_id: str | None = None,
    window: str = Query("30d", description="Time window for agreement calculation"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """Calculate agreement between LLM Judge and Human Review."""
    from app.agent.monitoring import calculate_judge_human_agreement, parse_window_start
    from app.infra.eval_db import eval_session

    since = parse_window_start(window)
    async with eval_session() as session:
        return await calculate_judge_human_agreement(session, since=since, run_id=run_id)


@router.get("/eval-alerts")
async def get_eval_alerts(
    status: str | None = Query(None),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import list_alerts
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        total, alerts = await list_alerts(session, status=status, limit=limit, offset=offset)
    return {"total": total, "limit": limit, "offset": offset, "alerts": alerts}


@router.post("/eval-alerts/{alert_id}/ack")
async def acknowledge_eval_alert(
    alert_id: str,
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import update_alert_status
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        alert = await update_alert_status(session, alert_id=alert_id, status="acknowledged", actor=admin.get("user_id"))
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        await session.commit()
    return {"alert": alert}


@router.post("/eval-alerts/{alert_id}/resolve")
async def resolve_eval_alert(
    alert_id: str,
    admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    from app.agent.monitoring import update_alert_status
    from app.infra.eval_db import eval_session

    async with eval_session() as session:
        alert = await update_alert_status(session, alert_id=alert_id, status="resolved", actor=admin.get("user_id"))
        if alert is None:
            raise HTTPException(status_code=404, detail="alert not found")
        await session.commit()
    return {"alert": alert}


@router.patch("/eval-runs/{run_id}/experiment")
async def update_eval_run_experiment(
    run_id: str,
    payload: dict[str, Any] = Body(...),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """Update experiment management fields for an eval run."""
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalRun

    allowed_fields = {"baseline_pin", "tags_json", "notes", "owner", "release_marker"}
    updates = {k: v for k, v in payload.items() if k in allowed_fields}
    if not updates:
        raise HTTPException(status_code=400, detail="no valid fields to update")

    store = await _eval_store()
    if store is not None:
        try:
            from app.infra.models.eval import EvalRun

            async with store.session() as session:
                run = await session.scalar(select(EvalRun).where((EvalRun.id == run_id) | (EvalRun.report_name == run_id)))
                if run:
                    for key, value in updates.items():
                        setattr(run, key, value)
                    await session.commit()
                    return {"run_id": run_id, "source": "eval_db", "updated": list(updates.keys())}
        except Exception:
            pass
        finally:
            await store.close()

    async with eval_session() as session:
        run = await session.scalar(select(EvalRun).where((EvalRun.id == run_id) | (EvalRun.report_name == run_id)))
        if not run:
            raise HTTPException(status_code=404, detail="eval run not found")
        for key, value in updates.items():
            setattr(run, key, value)
        await session.commit()
    return {"run_id": run_id, "source": "eval_db", "updated": list(updates.keys())}


@router.get("/rubric")
async def get_rubric_config(
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """Return current rubric configuration and version."""
    from evals.rubric import get_full_rubric_config
    return get_full_rubric_config()


@router.get("/realtime-eval/recent")
async def list_realtime_evals(
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    scene: str | None = Query(None, description="Filter by scene"),
    min_quality: float | None = Query(None, ge=0.0, le=1.0, description="Minimum overall_quality"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """查询实时评测记录（兼容旧前端；优先读 conversation_* 表）."""
    from app.agent.monitoring import conversation_run_summary, list_conversation_runs, load_chat_session_titles
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalRun

    async with eval_session() as session:
        total, rows = await list_conversation_runs(session, scene=scene, limit=limit, offset=offset)
        title_map = await load_chat_session_titles([row.session_id for row in rows])
        records = [conversation_run_summary(row, session_title=title_map.get(row.session_id)) for row in rows]
        if min_quality is not None:
            records = [row for row in records if float(row.get("overall_quality") or 0.0) >= min_quality]
            total = len(records)
        if records:
            return {"total": total, "records": records}

        query = select(EvalRun).where(EvalRun.suite == "realtime").order_by(desc(EvalRun.timestamp))
        rows = (await session.execute(query)).scalars().all()

    records = []
    for row in rows:
        raw = row.raw_report_json if isinstance(row.raw_report_json, dict) else {}
        if scene and raw.get("scene") != scene:
            continue
        if min_quality is not None and float(row.overall_success_rate or 0.0) < min_quality:
            continue
        records.append({
            "id": row.id,
            "session_id": raw.get("session_id", ""),
            "scene": raw.get("scene"),
            "agent_id": raw.get("agent_id"),
            "worker": raw.get("agent_id"),
            "is_fallback": raw.get("is_fallback", False),
            "has_content": raw.get("has_content", False),
            "overall_quality": row.overall_success_rate,
            "efficiency": raw.get("efficiency", 0.0),
            "schema_compliance": raw.get("schema_compliance", 0.0),
            "no_fallback": raw.get("no_fallback", 0.0),
            "has_content_score": raw.get("has_content_score", 0.0),
            "no_leak": raw.get("no_leak", 1.0),
            "tool_call_count": raw.get("tool_call_count", 0),
            "repeated_action_rate": raw.get("repeated_action_rate", 0.0),
            "tool_names": raw.get("tool_names", []),
            "total_duration_ms": raw.get("total_duration_ms", 0.0),
            "latency_ms": raw.get("total_duration_ms", 0.0),
            "error": raw.get("error"),
            "error_reason": raw.get("error_reason"),
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        })
    sliced = records[offset:offset + limit]

    return {"total": len(records), "records": sliced}


@router.get("/realtime-eval/summary")
async def get_realtime_eval_summary(
    hours: int = Query(24, ge=1, le=720, description="Look-back window in hours"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """实时评测聚合统计（兼容旧前端；优先读 conversation_* 表）."""
    from app.agent.monitoring import aggregate_monitoring_overview
    from app.infra.eval_db import eval_session
    from app.infra.models.eval import EvalRun

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with eval_session() as session:
        overview = await aggregate_monitoring_overview(session, since=since)
        if overview.get("total_runs"):
            return {
                "hours": hours,
                "total_evals": overview["total_runs"],
                "avg_quality": overview.get("task_success_proxy", 0.0),
                "fallback_rate": overview.get("fallback_rate", 0.0),
                "no_content_rate": 0.0,
                "leak_rate": overview.get("secret_leak_rate", 0.0),
                "avg_efficiency": 0.0,
                "avg_schema_compliance": 0.0,
                "avg_duration_ms": overview.get("latency_p50_ms", 0.0),
                "quality_trend": [],
                "scene_distribution": {},
            }

        rows = (await session.execute(
            select(EvalRun).where(EvalRun.suite == "realtime", EvalRun.timestamp >= since).order_by(EvalRun.timestamp)
        )).scalars().all()

    total_evals = len(rows)
    if not total_evals:
        return {
            "hours": hours,
            "total_evals": 0,
            "avg_quality": 0.0,
            "fallback_rate": 0.0,
            "no_content_rate": 0.0,
            "leak_rate": 0.0,
            "avg_efficiency": 0.0,
            "avg_schema_compliance": 0.0,
            "avg_duration_ms": 0.0,
            "quality_trend": [],
            "scene_distribution": {},
        }

    fallback_count = 0
    no_content_count = 0
    leak_count = 0
    efficiency_sum = 0.0
    schema_sum = 0.0
    quality_sum = 0.0
    duration_sum = 0.0
    scene_counts: dict[str, int] = {}
    quality_buckets: dict[str, list[float]] = {}
    for row in rows:
        raw = row.raw_report_json if isinstance(row.raw_report_json, dict) else {}
        quality = float(row.overall_success_rate or 0.0)
        quality_sum += quality
        duration_sum += float(row.duration_seconds or 0.0) * 1000
        if raw.get("is_fallback"):
            fallback_count += 1
        if not raw.get("has_content"):
            no_content_count += 1
        if raw.get("no_leak", 1.0) < 1.0:
            leak_count += 1
        efficiency_sum += float(raw.get("efficiency") or 0.0)
        schema_sum += float(raw.get("schema_compliance") or 0.0)
        scene = raw.get("scene") or "unknown"
        scene_counts[scene] = scene_counts.get(scene, 0) + 1
        if row.timestamp:
            bucket = row.timestamp.replace(minute=0, second=0, microsecond=0).isoformat()
            quality_buckets.setdefault(bucket, []).append(quality)

    return {
        "hours": hours,
        "total_evals": total_evals,
        "avg_quality": round(quality_sum / total_evals, 3),
        "fallback_rate": round(fallback_count / total_evals, 3) if total_evals else 0.0,
        "no_content_rate": round(no_content_count / total_evals, 3) if total_evals else 0.0,
        "leak_rate": round(leak_count / total_evals, 3) if total_evals else 0.0,
        "avg_efficiency": round(efficiency_sum / total_evals, 3) if total_evals else 0.0,
        "avg_schema_compliance": round(schema_sum / total_evals, 3) if total_evals else 0.0,
        "avg_duration_ms": round(duration_sum / total_evals, 0),
        "quality_trend": [
            {"hour": hour, "avg_quality": round(sum(values) / len(values), 3), "count": len(values)}
            for hour, values in sorted(quality_buckets.items())
        ],
        "scene_distribution": scene_counts,
    }
