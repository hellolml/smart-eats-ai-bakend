from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator
from uuid import uuid4

from sqlalchemy import delete, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool, StaticPool

from app.infra.models.base import Base
from app.infra.models.eval import EvalCase, EvalJudgeResult, EvalOutcomeResult, EvalRun, EvalScore, EvalTraceEvent, EvalTrial


def resolve_eval_database_url(cli_url: str | None = None) -> str | None:
    if cli_url:
        return cli_url
    if os.getenv("EVAL_DATABASE_URL"):
        return os.getenv("EVAL_DATABASE_URL")
    try:
        from app.common.config import settings

        if settings.EVAL_DATABASE_URL:
            return settings.EVAL_DATABASE_URL
    except Exception:
        pass
    return os.getenv("DATABASE_URL")


def is_supported_eval_database_url(url: str | None) -> bool:
    return bool(url and (url.startswith("postgresql+") or url.startswith("sqlite+aiosqlite")))


def normalize_report_name(report_path: str | Path, report: dict[str, Any]) -> str:
    path = Path(report_path)
    if path.name != "latest.json":
        return path.name
    timestamp = str(report.get("timestamp") or "").replace("-", "").replace(":", "").replace("T", "_")
    if timestamp:
        return f"eval_report_{timestamp[:15]}.json"
    return path.name


def parse_report_timestamp(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    text = str(value)
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


class EvalPersistenceStore:
    def __init__(self, database_url: str):
        self.database_url = database_url
        engine_kwargs: dict[str, Any] = {
            "pool_pre_ping": True,
        }
        if database_url.startswith("sqlite+aiosqlite"):
            engine_kwargs["connect_args"] = {"timeout": 30}
            if database_url in {"sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://"}:
                engine_kwargs["poolclass"] = StaticPool
            else:
                engine_kwargs["poolclass"] = NullPool
        else:
            engine_kwargs["poolclass"] = NullPool
        self.engine = create_async_engine(
            database_url,
            **engine_kwargs,
        )
        self.session_factory = async_sessionmaker(self.engine, expire_on_commit=False)

    async def close(self) -> None:
        await self.engine.dispose()

    async def init_schema(self) -> None:
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[AsyncSession]:
        async with self.session_factory() as db:
            yield db

    async def upsert_report(self, report_name: str, report: dict[str, Any]) -> str:
        await self.init_schema()
        async with self.session_factory() as db:
            run_id = await self._upsert_report(db, report_name, report)
            await db.commit()
            return run_id

    async def _upsert_report(self, db: AsyncSession, report_name: str, report: dict[str, Any]) -> str:
        existing = await db.scalar(select(EvalRun).where(EvalRun.report_name == report_name))
        if existing:
            run_id = existing.id
            await db.execute(delete(EvalTraceEvent).where(EvalTraceEvent.run_id == run_id))
            await db.execute(delete(EvalJudgeResult).where(EvalJudgeResult.run_id == run_id))
            await db.execute(delete(EvalOutcomeResult).where(EvalOutcomeResult.run_id == run_id))
            await db.execute(delete(EvalScore).where(EvalScore.run_id == run_id))
            await db.execute(delete(EvalTrial).where(EvalTrial.run_id == run_id))
            await db.execute(delete(EvalCase).where(EvalCase.run_id == run_id))
            run = existing
        else:
            run_id = str(uuid4())
            run = EvalRun(id=run_id, report_name=report_name, raw_report_json=report)
            db.add(run)

        metadata = report.get("metadata", {}) if isinstance(report.get("metadata"), dict) else {}
        results = report.get("results", []) if isinstance(report.get("results"), list) else []
        run.timestamp = parse_report_timestamp(report.get("timestamp"))
        run.suite = metadata.get("suite")
        run.runner = metadata.get("runner")
        run.base_url = metadata.get("base_url")
        run.commit_sha = metadata.get("commit_sha")
        run.branch = metadata.get("branch")
        run.model_provider = metadata.get("model_provider")
        run.model_name = metadata.get("model_name")
        run.overall_success_rate = float(report.get("overall_success_rate") or 0.0)
        run.total_cases = int(report.get("total_cases") or len(results))
        run.total_trials = int(report.get("total_trials") or sum(len(item.get("trials", [])) for item in results if isinstance(item, dict)))
        run.duration_seconds = float(report.get("duration_seconds") or 0.0)
        run.raw_report_json = report
        await db.flush()

        for case_data in results:
            if not isinstance(case_data, dict):
                continue
            case_row = EvalCase(
                id=str(uuid4()),
                run_id=run_id,
                case_id=str(case_data.get("case_id") or ""),
                task=case_data.get("task"),
                scene=case_data.get("scene"),
                category=case_data.get("category"),
                priority=case_data.get("priority"),
                success_rate=float(case_data.get("success_rate") or 0.0),
                avg_scores_json=case_data.get("avg_scores") if isinstance(case_data.get("avg_scores"), dict) else {},
            )
            db.add(case_row)
            await db.flush()
            for trial_data in case_data.get("trials", []) if isinstance(case_data.get("trials"), list) else []:
                if not isinstance(trial_data, dict):
                    continue
                trial = EvalTrial(
                    id=str(uuid4()),
                    run_id=run_id,
                    case_row_id=case_row.id,
                    case_id=case_row.case_id,
                    trial_number=int(trial_data.get("trial_number") or 0),
                    weighted_score=float(trial_data.get("weighted_score") or 0.0),
                    expected_scene=trial_data.get("expected_scene"),
                    actual_scene=trial_data.get("actual_scene"),
                    actual_worker=trial_data.get("actual_worker"),
                    tool_calls_json=trial_data.get("tool_calls") if isinstance(trial_data.get("tool_calls"), list) else [],
                    failure_class=trial_data.get("failure_class"),
                    error_reason=trial_data.get("error_reason"),
                    error=trial_data.get("error"),
                    final_answer_preview=trial_data.get("final_answer_preview"),
                    threshold_failures_json=trial_data.get("threshold_failures") if isinstance(trial_data.get("threshold_failures"), list) else [],
                    missing_metrics_json=trial_data.get("missing_metrics") if isinstance(trial_data.get("missing_metrics"), list) else [],
                )
                db.add(trial)
                await db.flush()
                scores = trial_data.get("scores", {}) if isinstance(trial_data.get("scores"), dict) else {}
                for metric, value in scores.items():
                    if isinstance(value, (int, float)):
                        db.add(EvalScore(
                            id=str(uuid4()),
                            run_id=run_id,
                            trial_id=trial.id,
                            case_id=case_row.case_id,
                            metric=str(metric),
                            score=float(value),
                        ))
                for detail in trial_data.get("outcome_details", []) if isinstance(trial_data.get("outcome_details"), list) else []:
                    if not isinstance(detail, dict):
                        continue
                    db.add(EvalOutcomeResult(
                        id=str(uuid4()),
                        run_id=run_id,
                        case_id=case_row.case_id,
                        trial_number=trial.trial_number,
                        verifier=str(detail.get("verifier") or "unknown"),
                        score=float(detail.get("score") or 0.0),
                        passed=bool(detail.get("passed")),
                        failures_json=detail.get("failures") if isinstance(detail.get("failures"), list) else [],
                        details_json=detail.get("details") if isinstance(detail.get("details"), dict) else {},
                    ))
                judge_scores = trial_data.get("judge_scores") if isinstance(trial_data.get("judge_scores"), dict) else {}
                judge_reasons = trial_data.get("judge_reasons") if isinstance(trial_data.get("judge_reasons"), dict) else {}
                if not judge_scores and trial_data.get("llm_judge_skipped_reason"):
                    db.add(EvalJudgeResult(
                        id=str(uuid4()),
                        run_id=run_id,
                        case_id=case_row.case_id,
                        trial_number=trial.trial_number,
                        metric="llm_judge",
                        skipped_reason=str(trial_data.get("llm_judge_skipped_reason")),
                        rubric_version="v1",
                    ))
                for metric, value in judge_scores.items():
                    db.add(EvalJudgeResult(
                        id=str(uuid4()),
                        run_id=run_id,
                        case_id=case_row.case_id,
                        trial_number=trial.trial_number,
                        metric=str(metric),
                        score=float(value) if isinstance(value, (int, float)) else None,
                        reason=judge_reasons.get(metric) if isinstance(judge_reasons, dict) else None,
                        rubric_version="v1",
                        raw_json={"scores": judge_scores, "reasons": judge_reasons},
                    ))
                timeline = trial_data.get("trace_timeline", []) if isinstance(trial_data.get("trace_timeline"), list) else []
                for index, event in enumerate(timeline):
                    if not isinstance(event, dict):
                        continue
                    db.add(EvalTraceEvent(
                        id=str(uuid4()),
                        run_id=run_id,
                        trial_id=trial.id,
                        case_id=case_row.case_id,
                        event_index=int(event.get("index") if event.get("index") is not None else index),
                        event_type=event.get("event_type"),
                        label=event.get("label"),
                        tool_name=event.get("tool_name"),
                        duration_ms=float(event["duration_ms"]) if isinstance(event.get("duration_ms"), (int, float)) else None,
                        timestamp=float(event["timestamp"]) if isinstance(event.get("timestamp"), (int, float)) else None,
                        data_json=event.get("data") if isinstance(event.get("data"), dict) else {},
                    ))
        return run_id

    async def list_reports(self) -> list[dict[str, Any]]:
        await self.init_schema()
        async with self.session_factory() as db:
            rows = (await db.execute(
                select(EvalRun)
                .where(or_(EvalRun.suite.is_(None), EvalRun.suite != "realtime"))
                .order_by(EvalRun.timestamp.desc().nullslast(), EvalRun.created_at.desc())
            )).scalars().all()
            return [self._run_summary(row) for row in rows]

    async def load_report(self, report_name: str) -> dict[str, Any] | None:
        await self.init_schema()
        async with self.session_factory() as db:
            run = await self._find_run(db, report_name)
            if not run:
                return None
            return dict(run.raw_report_json)

    async def load_case(self, report_name: str, case_id: str) -> dict[str, Any] | None:
        report = await self.load_report(report_name)
        if not report:
            return None
        for case in report.get("results", []) if isinstance(report.get("results"), list) else []:
            if str(case.get("case_id")) == case_id:
                return {
                    "report": report_name,
                    "case": case,
                    "trials": case.get("trials", []) if isinstance(case.get("trials"), list) else [],
                }
        return None

    async def compare_reports(self, baseline: str, candidate: str) -> dict[str, Any] | None:
        baseline_report = await self.load_report(baseline)
        candidate_report = await self.load_report(candidate)
        if not baseline_report or not candidate_report:
            return None
        return compare_report_dicts(baseline, baseline_report, candidate, candidate_report)

    async def _find_run(self, db: AsyncSession, report_name: str) -> EvalRun | None:
        run = await db.scalar(select(EvalRun).where(EvalRun.report_name == report_name))
        if run:
            return run
        if report_name == "latest.json":
            return await db.scalar(
                select(EvalRun)
                .where(or_(EvalRun.suite.is_(None), EvalRun.suite != "realtime"))
                .order_by(EvalRun.timestamp.desc().nullslast(), EvalRun.created_at.desc())
                .limit(1)
            )
        return None

    def _run_summary(self, run: EvalRun) -> dict[str, Any]:
        raw = run.raw_report_json if isinstance(run.raw_report_json, dict) else {}
        results = raw.get("results", []) if isinstance(raw.get("results"), list) else []
        failed_cases = sum(1 for result in results if float(result.get("success_rate") or 0.0) < 1.0)
        p0_failed_cases = sum(1 for result in results if result.get("priority") == "p0" and float(result.get("success_rate") or 0.0) < 1.0)
        return {
            "name": run.report_name,
            "timestamp": run.timestamp.isoformat() if run.timestamp else raw.get("timestamp"),
            "total_cases": run.total_cases,
            "total_trials": run.total_trials,
            "overall_success_rate": run.overall_success_rate,
            "failed_cases": failed_cases,
            "p0_failed_cases": p0_failed_cases,
            "duration_seconds": run.duration_seconds,
            "suite": run.suite,
            "runner": run.runner,
            "size_bytes": len(json.dumps(raw, ensure_ascii=False, default=str)),
            "modified_at": run.timestamp.timestamp() if run.timestamp else 0.0,
        }


def _is_case_failed(result: dict[str, Any]) -> bool:
    return float(result.get("success_rate") or 0.0) < 1.0


def _case_map(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = data.get("results", []) if isinstance(data.get("results"), list) else []
    return {str(result.get("case_id")): result for result in results if result.get("case_id")}


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


def _summary(name: str, data: dict[str, Any]) -> dict[str, Any]:
    results = data.get("results", []) if isinstance(data.get("results"), list) else []
    failed_cases = sum(1 for result in results if _is_case_failed(result))
    p0_failed_cases = sum(1 for result in results if result.get("priority") == "p0" and _is_case_failed(result))
    metadata = data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {}
    return {
        "name": name,
        "timestamp": data.get("timestamp"),
        "total_cases": data.get("total_cases", len(results)),
        "total_trials": data.get("total_trials", sum(len(r.get("trials", [])) for r in results)),
        "overall_success_rate": data.get("overall_success_rate", 0.0),
        "failed_cases": failed_cases,
        "p0_failed_cases": p0_failed_cases,
        "duration_seconds": data.get("duration_seconds", 0.0),
        "suite": metadata.get("suite"),
        "runner": metadata.get("runner"),
    }


def _score_delta_items(baseline: dict[str, dict[str, Any]], candidate: dict[str, dict[str, Any]], *, drop: bool, min_abs_delta: float = 0.01) -> list[dict[str, Any]]:
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


def _breakdown_delta(baseline: dict[str, Any], candidate: dict[str, Any], key: str) -> dict[str, dict[str, float]]:
    base = baseline.get(key, {}) if isinstance(baseline.get(key), dict) else {}
    cand = candidate.get(key, {}) if isinstance(candidate.get(key), dict) else {}
    result: dict[str, dict[str, float]] = {}
    for name in sorted(set(base) | set(cand)):
        base_success = float((base.get(name) or {}).get("success_rate") or 0.0)
        cand_success = float((cand.get(name) or {}).get("success_rate") or 0.0)
        result[name] = {"baseline": base_success, "candidate": cand_success, "delta": cand_success - base_success}
    return result


def compare_report_dicts(baseline_name: str, baseline_data: dict[str, Any], candidate_name: str, candidate_data: dict[str, Any]) -> dict[str, Any]:
    baseline_summary = _summary(baseline_name, baseline_data)
    candidate_summary = _summary(candidate_name, candidate_data)
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
        metric_delta[metric] = {"baseline": base_value, "candidate": cand_value, "delta": cand_value - base_value}
    return {
        "baseline": baseline_name,
        "candidate": candidate_name,
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
