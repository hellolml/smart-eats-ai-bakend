from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.agent.metrics import get_agent_metrics_snapshot, reset_agent_metrics
from app.api.deps import require_eval_admin

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
    return os.getenv("EVAL_DATABASE_URL") or os.getenv("DATABASE_URL")


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


# ---------------------------------------------------------------------------
# 实时评测监控 API
# ---------------------------------------------------------------------------


@router.get("/realtime-eval/recent")
async def list_realtime_evals(
    limit: int = Query(50, ge=1, le=200, description="Max results to return"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
    scene: str | None = Query(None, description="Filter by scene"),
    min_quality: float | None = Query(None, ge=0.0, le=1.0, description="Minimum overall_quality"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """查询实时评测记录（最近 N 条）."""
    from sqlalchemy import select, func, desc
    from app.infra.db import AsyncSessionLocal
    from app.infra.models.eval import EvalRun

    async with AsyncSessionLocal() as session:
        base_query = select(EvalRun).where(EvalRun.suite == "realtime")
        count_query = select(func.count()).select_from(EvalRun).where(EvalRun.suite == "realtime")

        if scene:
            # scene 信息存在 raw_report_json 中
            base_query = base_query.where(EvalRun.raw_report_json["scene"].astext == scene)
            count_query = count_query.where(EvalRun.raw_report_json["scene"].astext == scene)
        if min_quality is not None:
            base_query = base_query.where(EvalRun.overall_success_rate >= min_quality)
            count_query = count_query.where(EvalRun.overall_success_rate >= min_quality)

        total_result = await session.execute(count_query)
        total = total_result.scalar() or 0

        query = base_query.order_by(desc(EvalRun.timestamp)).offset(offset).limit(limit)
        result = await session.execute(query)
        rows = result.scalars().all()

    records = []
    for row in rows:
        raw = row.raw_report_json or {}
        records.append({
            "id": row.id,
            "session_id": raw.get("session_id", ""),
            "scene": raw.get("scene"),
            "agent_id": raw.get("agent_id"),
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
            "error": raw.get("error"),
            "error_reason": raw.get("error_reason"),
            "timestamp": row.timestamp.isoformat() if row.timestamp else None,
        })

    return {"total": total, "records": records}


@router.get("/realtime-eval/summary")
async def get_realtime_eval_summary(
    hours: int = Query(24, ge=1, le=720, description="Look-back window in hours"),
    _admin: dict[str, str | None] = Depends(require_eval_admin),
) -> dict[str, Any]:
    """实时评测聚合统计（最近 N 小时）."""
    from datetime import datetime, timedelta, timezone
    from sqlalchemy import select, func, case
    from app.infra.db import AsyncSessionLocal
    from app.infra.models.eval import EvalRun

    since = datetime.now(timezone.utc) - timedelta(hours=hours)

    async with AsyncSessionLocal() as session:
        base = select(EvalRun).where(
            EvalRun.suite == "realtime",
            EvalRun.timestamp >= since,
        )

        # 总量统计
        count_result = await session.execute(
            select(func.count()).select_from(EvalRun).where(
                EvalRun.suite == "realtime",
                EvalRun.timestamp >= since,
            )
        )
        total_evals = count_result.scalar() or 0

        if total_evals == 0:
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

        # 聚合统计
        agg_result = await session.execute(
            select(
                func.avg(EvalRun.overall_success_rate).label("avg_quality"),
                func.avg(EvalRun.duration_seconds).label("avg_duration_s"),
            ).where(
                EvalRun.suite == "realtime",
                EvalRun.timestamp >= since,
            )
        )
        agg = agg_result.one()

        # Fallback / no_content / leak 统计（从 raw_report_json 提取）
        all_result = await session.execute(
            select(EvalRun.raw_report_json).where(
                EvalRun.suite == "realtime",
                EvalRun.timestamp >= since,
            ).order_by(EvalRun.timestamp)
        )
        raw_rows = all_result.all()

        fallback_count = 0
        no_content_count = 0
        leak_count = 0
        efficiency_sum = 0.0
        schema_sum = 0.0
        scene_counts: dict[str, int] = {}
        # 按小时聚合趋势
        hourly_buckets: dict[str, list[float]] = {}

        for (raw,) in raw_rows:
            if not isinstance(raw, dict):
                continue
            if raw.get("is_fallback"):
                fallback_count += 1
            if not raw.get("has_content"):
                no_content_count += 1
            if raw.get("no_leak", 1.0) < 1.0:
                leak_count += 1
            efficiency_sum += raw.get("efficiency", 0.0)
            schema_sum += raw.get("schema_compliance", 0.0)
            scene = raw.get("scene") or "unknown"
            scene_counts[scene] = scene_counts.get(scene, 0) + 1

        # 按小时聚合 quality 趋势
        trend_result = await session.execute(
            select(
                func.date_trunc("hour", EvalRun.timestamp).label("hour"),
                func.avg(EvalRun.overall_success_rate).label("avg_quality"),
                func.count().label("count"),
            ).where(
                EvalRun.suite == "realtime",
                EvalRun.timestamp >= since,
            ).group_by("hour").order_by("hour")
        )
        quality_trend = []
        for row in trend_result:
            quality_trend.append({
                "hour": row.hour.isoformat() if row.hour else None,
                "avg_quality": round(float(row.avg_quality or 0), 3),
                "count": int(row.count or 0),
            })

    return {
        "hours": hours,
        "total_evals": total_evals,
        "avg_quality": round(float(agg.avg_quality or 0), 3),
        "fallback_rate": round(fallback_count / total_evals, 3) if total_evals else 0.0,
        "no_content_rate": round(no_content_count / total_evals, 3) if total_evals else 0.0,
        "leak_rate": round(leak_count / total_evals, 3) if total_evals else 0.0,
        "avg_efficiency": round(efficiency_sum / total_evals, 3) if total_evals else 0.0,
        "avg_schema_compliance": round(schema_sum / total_evals, 3) if total_evals else 0.0,
        "avg_duration_ms": round(float(agg.avg_duration_s or 0) * 1000, 0),
        "quality_trend": quality_trend,
        "scene_distribution": scene_counts,
    }
