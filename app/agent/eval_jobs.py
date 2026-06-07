from __future__ import annotations

import asyncio
import os
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from uuid import uuid4

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.infra.eval_db import eval_database_url, eval_session, init_eval_db
from app.infra.models.eval import EvalRunJob


VALID_RUNNERS = {"fixture", "live"}
VALID_SUITES = {"quick", "full", "live-smoke"}
ACTIVE_STATUSES = {"queued", "running"}
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
LOG_TAIL_BYTES = 24 * 1024
PROJECT_ROOT = Path(__file__).resolve().parents[2]
ACTIVE_PROCESSES: dict[str, asyncio.subprocess.Process] = {}


class EvalJobConflict(Exception):
    def __init__(self, active_job: EvalRunJob):
        self.active_job = active_job


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def serialize_eval_job(job: EvalRunJob, *, include_logs: bool = False) -> dict[str, Any]:
    payload = {
        "id": job.id,
        "status": job.status,
        "runner": job.runner,
        "suite": job.suite,
        "num_trials": job.num_trials,
        "base_url": job.base_url,
        "include_llm_judge": job.include_llm_judge,
        "outcome_verify": job.outcome_verify,
        "persist_db": job.persist_db,
        "require_db_persist": job.require_db_persist,
        "output_dir": job.output_dir,
        "report_name": job.report_name,
        "report_path": job.report_path,
        "exit_code": job.exit_code,
        "pid": job.pid,
        "requested_by": job.requested_by,
        "started_at": _iso(job.started_at),
        "finished_at": _iso(job.finished_at),
        "error": job.error,
        "created_at": _iso(job.created_at),
    }
    if include_logs:
        payload["logs_tail"] = job.logs_tail or ""
    return payload


def _bool_payload(payload: dict[str, Any], key: str, default: bool) -> bool:
    value = payload.get(key, default)
    return value if isinstance(value, bool) else bool(value)


def _int_payload(payload: dict[str, Any], key: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(payload.get(key, default))
    except (TypeError, ValueError):
        raise ValueError(f"{key} must be an integer") from None
    if value < min_value or value > max_value:
        raise ValueError(f"{key} must be between {min_value} and {max_value}")
    return value


def _web_job_root() -> Path:
    return Path(os.getenv("EVAL_WEB_JOB_OUTPUT_DIR") or (PROJECT_ROOT / "eval_results" / "web_jobs")).expanduser().resolve()


async def create_eval_job(
    session: AsyncSession,
    *,
    payload: dict[str, Any],
    requested_by: str | None,
) -> EvalRunJob:
    active = await session.scalar(
        select(EvalRunJob)
        .where(EvalRunJob.status.in_(ACTIVE_STATUSES))
        .order_by(EvalRunJob.created_at)
        .limit(1)
    )
    if active is not None:
        raise EvalJobConflict(active)

    runner = str(payload.get("runner") or "fixture")
    suite = str(payload.get("suite") or "quick")
    if runner not in VALID_RUNNERS:
        raise ValueError("runner must be fixture or live")
    if suite not in VALID_SUITES:
        raise ValueError("suite must be quick, full, or live-smoke")

    job_id = str(uuid4())
    output_dir = _web_job_root() / job_id
    job = EvalRunJob(
        id=job_id,
        status="queued",
        runner=runner,
        suite=suite,
        num_trials=_int_payload(payload, "num_trials", 1, min_value=1, max_value=20),
        base_url=str(payload.get("base_url") or "http://127.0.0.1:8000"),
        include_llm_judge=_bool_payload(payload, "include_llm_judge", False),
        outcome_verify=_bool_payload(payload, "outcome_verify", False),
        persist_db=_bool_payload(payload, "persist_db", True),
        require_db_persist=_bool_payload(payload, "require_db_persist", False),
        output_dir=str(output_dir),
        requested_by=requested_by,
    )
    session.add(job)
    await session.flush()
    return job


async def list_eval_jobs(
    session: AsyncSession,
    *,
    status: str | None = None,
    limit: int = 50,
    offset: int = 0,
) -> tuple[int, list[EvalRunJob]]:
    filters = []
    if status:
        filters.append(EvalRunJob.status == status)
    count_query = select(func.count()).select_from(EvalRunJob)
    rows_query = select(EvalRunJob).order_by(desc(EvalRunJob.created_at)).offset(offset).limit(limit)
    for item in filters:
        count_query = count_query.where(item)
        rows_query = rows_query.where(item)
    total = int((await session.execute(count_query)).scalar() or 0)
    rows = (await session.execute(rows_query)).scalars().all()
    return total, list(rows)


async def load_eval_job(session: AsyncSession, job_id: str) -> EvalRunJob | None:
    return await session.scalar(select(EvalRunJob).where(EvalRunJob.id == job_id))


async def cancel_eval_job(session: AsyncSession, job_id: str) -> EvalRunJob | None:
    job = await load_eval_job(session, job_id)
    if job is None:
        return None
    if job.status in TERMINAL_STATUSES:
        return job
    process = ACTIVE_PROCESSES.get(job_id)
    if process is not None and process.returncode is None:
        process.terminate()
    job.status = "cancelled"
    job.finished_at = _now()
    job.error = job.error or "cancelled by user"
    await session.flush()
    return job


def schedule_eval_job(job_id: str) -> None:
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(run_eval_job(job_id))


def _command_for_job(job: EvalRunJob) -> list[str]:
    command = [
        sys.executable,
        str(PROJECT_ROOT / "evals" / "scripts" / "run_eval.py"),
        "--runner",
        job.runner,
        "--suite",
        job.suite,
        "--num-trials",
        str(job.num_trials),
        "--output-dir",
        job.output_dir,
        "--no-html",
    ]
    if job.runner == "live":
        command.extend(["--base-url", job.base_url or "http://127.0.0.1:8000"])
    if job.include_llm_judge:
        command.append("--include-llm-judge")
    if job.outcome_verify:
        command.append("--outcome-verify")
    if not job.persist_db:
        command.append("--no-persist-db")
    if job.require_db_persist:
        command.append("--require-db-persist")
    if eval_database_url():
        command.extend(["--eval-database-url", eval_database_url()])
    return command


def _tail_text(text: str, max_bytes: int = LOG_TAIL_BYTES) -> str:
    encoded = text.encode("utf-8", errors="ignore")
    if len(encoded) <= max_bytes:
        return text
    return encoded[-max_bytes:].decode("utf-8", errors="ignore")


async def _update_job(job_id: str, **updates: Any) -> None:
    async with eval_session() as session:
        job = await load_eval_job(session, job_id)
        if job is None:
            return
        for key, value in updates.items():
            setattr(job, key, value)
        await session.commit()


async def _current_status(job_id: str) -> str | None:
    async with eval_session() as session:
        job = await load_eval_job(session, job_id)
        return job.status if job else None


def _latest_report(output_dir: Path) -> Path | None:
    reports = sorted(output_dir.glob("eval_report_*.json"), key=lambda path: path.stat().st_mtime, reverse=True)
    return reports[0] if reports else None


async def run_eval_job(job_id: str) -> None:
    await init_eval_db()
    async with eval_session() as session:
        job = await load_eval_job(session, job_id)
        if job is None or job.status != "queued":
            return
        job.status = "running"
        job.started_at = _now()
        output_dir = Path(job.output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        command = _command_for_job(job)
        await session.commit()

    log_file = Path(job.output_dir) / "run.log"
    logs = deque[str]()
    process: asyncio.subprocess.Process | None = None
    try:
        env = os.environ.copy()
        if eval_database_url():
            env["EVAL_DATABASE_URL"] = eval_database_url()
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(PROJECT_ROOT),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        ACTIVE_PROCESSES[job_id] = process
        await _update_job(job_id, pid=process.pid)
        line_count = 0
        with log_file.open("wb") as handle:
            assert process.stdout is not None
            while True:
                chunk = await process.stdout.readline()
                if not chunk:
                    break
                handle.write(chunk)
                handle.flush()
                text = chunk.decode("utf-8", errors="replace")
                logs.append(text)
                while sum(len(item.encode("utf-8", errors="ignore")) for item in logs) > LOG_TAIL_BYTES:
                    logs.popleft()
                line_count += 1
                if line_count % 10 == 0:
                    await _update_job(job_id, logs_tail=_tail_text("".join(logs)))
            exit_code = await process.wait()

        status = await _current_status(job_id)
        if status == "cancelled":
            await _update_job(job_id, exit_code=exit_code, logs_tail=_tail_text("".join(logs)))
            return

        report = _latest_report(Path(job.output_dir))
        if exit_code == 0 and report is not None:
            await _update_job(
                job_id,
                status="succeeded",
                exit_code=exit_code,
                report_name=report.name,
                report_path=str(report),
                finished_at=_now(),
                logs_tail=_tail_text("".join(logs)),
            )
        else:
            await _update_job(
                job_id,
                status="failed",
                exit_code=exit_code,
                finished_at=_now(),
                error=f"run_eval.py exited with code {exit_code}",
                logs_tail=_tail_text("".join(logs)),
            )
    except Exception as exc:
        await _update_job(
            job_id,
            status="failed",
            finished_at=_now(),
            error=str(exc),
            logs_tail=_tail_text("".join(logs)),
        )
    finally:
        ACTIVE_PROCESSES.pop(job_id, None)
