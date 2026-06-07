#!/usr/bin/env python3
"""Idempotent migration for the evaluation and monitoring platform.

The project does not currently use Alembic. This script is the production-safe
bridge for existing databases: it creates the new eval/monitoring tables and
adds columns that ``Base.metadata.create_all`` cannot add to old tables.

Usage:
    python scripts/migrate_eval_platform.py
    python scripts/migrate_eval_platform.py --eval-database-url "$EVAL_DATABASE_URL"

Safe to re-run.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool, StaticPool


PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Migrate Smart-Eats eval/monitoring platform tables")
    parser.add_argument("--eval-database-url", default=None, help="Override EVAL_DATABASE_URL/DATABASE_URL")
    parser.add_argument("--dry-run", action="store_true", help="Print planned operations without applying")
    return parser.parse_args()


def _engine_for_url(database_url: str):
    kwargs: dict[str, Any] = {"pool_pre_ping": True}
    if database_url.startswith("sqlite+aiosqlite"):
        kwargs["connect_args"] = {"timeout": 30}
        kwargs["poolclass"] = StaticPool if database_url in {"sqlite+aiosqlite:///:memory:", "sqlite+aiosqlite://"} else NullPool
    else:
        kwargs["poolclass"] = NullPool
    return create_async_engine(database_url, **kwargs)


def _dialect(database_url: str) -> str:
    if database_url.startswith("postgresql"):
        return "postgresql"
    if database_url.startswith("sqlite+aiosqlite"):
        return "sqlite"
    raise ValueError("Only postgresql+* and sqlite+aiosqlite URLs are supported")


def _json_type(dialect: str) -> str:
    return "JSONB" if dialect == "postgresql" else "JSON"


def _bool_type(dialect: str, *, default: bool | None = None) -> str:
    base = "BOOLEAN"
    if default is None:
        return base
    return f"{base} DEFAULT {'FALSE' if dialect == 'postgresql' else '0'}" if default is False else f"{base} DEFAULT {'TRUE' if dialect == 'postgresql' else '1'}"


def _timestamp_type(dialect: str) -> str:
    return "TIMESTAMP WITH TIME ZONE" if dialect == "postgresql" else "DATETIME"


def _column_plan(dialect: str) -> dict[str, list[tuple[str, str]]]:
    json_type = _json_type(dialect)
    timestamp_type = _timestamp_type(dialect)
    return {
        "eval_runs": [
            ("baseline_pin", "VARCHAR(255)"),
            ("tags_json", json_type),
            ("notes", "TEXT"),
            ("owner", "VARCHAR(255)"),
            ("release_marker", "VARCHAR(64)"),
        ],
        "conversation_human_reviews": [
            ("failure_reason", "VARCHAR(255)"),
            ("failure_tags_json", json_type),
            ("corrected_answer", "TEXT"),
            ("expected_behavior", "TEXT"),
            ("review_confidence", "FLOAT"),
            ("dataset_candidate", _bool_type(dialect, default=False)),
        ],
        "conversation_costs": [
            ("cached_tokens", "INTEGER DEFAULT 0"),
            ("reasoning_tokens", "INTEGER DEFAULT 0"),
            ("total_tokens", "INTEGER DEFAULT 0"),
            ("provider", "VARCHAR(128)"),
            ("model_name", "VARCHAR(255)"),
            ("cost_estimated", _bool_type(dialect, default=False)),
            ("pricing_json", json_type),
        ],
        "evaluation_alerts": [
            ("notification_sent", _bool_type(dialect, default=False)),
            ("notification_sent_at", timestamp_type),
            ("notification_error", "VARCHAR(512)"),
        ],
    }


def _index_plan() -> list[tuple[str, str, str, bool]]:
    return [
        ("ix_eval_runs_timestamp", "eval_runs", "timestamp", False),
        ("ix_eval_runs_suite_runner", "eval_runs", "suite, runner", False),
        ("uq_eval_runs_report_name_idx", "eval_runs", "report_name", True),
        ("ix_eval_run_jobs_status", "eval_run_jobs", "status", False),
        ("ix_eval_run_jobs_created_at", "eval_run_jobs", "created_at", False),
        ("ix_eval_run_jobs_requested_by", "eval_run_jobs", "requested_by", False),
        ("ix_eval_run_jobs_runner_suite", "eval_run_jobs", "runner, suite", False),
        ("ix_eval_cases_case_id", "eval_cases", "case_id", False),
        ("ix_eval_cases_scene_category", "eval_cases", "scene, category", False),
        ("ix_eval_trials_failure_class", "eval_trials", "failure_class", False),
        ("ix_eval_scores_metric", "eval_scores", "metric", False),
        ("ix_eval_trace_events_event_type", "eval_trace_events", "event_type", False),
        ("ix_conversation_runs_started_at", "conversation_runs", "started_at", False),
        ("ix_conversation_runs_session_id", "conversation_runs", "session_id", False),
        ("ix_conversation_runs_user_id", "conversation_runs", "user_id", False),
        ("ix_conversation_runs_scene_worker", "conversation_runs", "scene, worker", False),
        ("ix_conversation_runs_status", "conversation_runs", "status", False),
        ("ix_conversation_trace_events_event_type", "conversation_trace_events", "event_type", False),
        ("ix_conversation_trace_events_tool_name", "conversation_trace_events", "tool_name", False),
        ("ix_conversation_tool_calls_tool_name", "conversation_tool_calls", "tool_name", False),
        ("ix_conversation_tool_calls_success", "conversation_tool_calls", "success", False),
        ("ix_conversation_metrics_metric_name", "conversation_metrics", "metric_name", False),
        ("ix_conversation_metrics_window", "conversation_metrics", "window_start, window_end", False),
        ("ix_conversation_eval_jobs_status", "conversation_eval_jobs", "status", False),
        ("ix_conversation_eval_jobs_job_type", "conversation_eval_jobs", "job_type", False),
        ("ix_conversation_human_reviews_decision", "conversation_human_reviews", "decision", False),
        ("ix_eval_datasets_suite_status", "eval_datasets", "suite, status", False),
        ("uq_eval_datasets_name_version_idx", "eval_datasets", "name, version", True),
        ("ix_eval_dataset_cases_case_id", "eval_dataset_cases", "case_id", False),
        ("ix_eval_dataset_cases_source", "eval_dataset_cases", "source", False),
        ("ix_eval_dataset_cases_review_status", "eval_dataset_cases", "review_status", False),
        ("ix_eval_dataset_cases_scene_category", "eval_dataset_cases", "scene, category", False),
        ("ix_eval_case_lineage_source_run", "eval_case_lineage", "source_run_id", False),
        ("ix_eval_case_lineage_source_trace", "eval_case_lineage", "source_trace_id", False),
        ("ix_eval_case_lineage_target_case", "eval_case_lineage", "target_case_id", False),
        ("ix_eval_outcome_results_run_case", "eval_outcome_results", "run_id, case_id", False),
        ("ix_eval_outcome_results_verifier", "eval_outcome_results", "verifier", False),
        ("ix_eval_outcome_results_passed", "eval_outcome_results", "passed", False),
        ("ix_eval_judge_results_run_case", "eval_judge_results", "run_id, case_id", False),
        ("ix_eval_judge_results_metric", "eval_judge_results", "metric", False),
        ("ix_eval_judge_results_rubric", "eval_judge_results", "rubric_version", False),
        ("ix_evaluation_alerts_type_status", "evaluation_alerts", "alert_type, status", False),
        ("ix_evaluation_alerts_severity_status", "evaluation_alerts", "severity, status", False),
        ("ix_evaluation_alerts_created_at", "evaluation_alerts", "created_at", False),
    ]


async def _table_exists(conn, dialect: str, table: str) -> bool:
    if dialect == "postgresql":
        result = await conn.exec_driver_sql(f"SELECT to_regclass('{table}')")
        return result.scalar() is not None
    result = await conn.exec_driver_sql("SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,))
    return result.scalar() is not None


async def _column_exists(conn, dialect: str, table: str, column: str) -> bool:
    if not await _table_exists(conn, dialect, table):
        return False
    if dialect == "postgresql":
        result = await conn.exec_driver_sql(
            f"""
            SELECT 1
            FROM information_schema.columns
            WHERE table_schema = current_schema()
              AND table_name = '{table}'
              AND column_name = '{column}'
            """
        )
        return result.scalar() is not None
    result = await conn.exec_driver_sql(f"PRAGMA table_info({table})")
    return column in {row[1] for row in result.fetchall()}


async def _add_column(conn, dialect: str, table: str, column: str, column_type: str, *, dry_run: bool) -> None:
    if not await _table_exists(conn, dialect, table):
        print(f"  · table {table} missing before create_all; skip column {column}")
        return
    if await _column_exists(conn, dialect, table, column):
        print(f"  · {table}.{column} already exists")
        return
    sql = (
        f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {column} {column_type}"
        if dialect == "postgresql"
        else f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
    )
    print(f"  + {table}.{column} {column_type}")
    if not dry_run:
        await conn.exec_driver_sql(sql)


async def _create_index(conn, name: str, table: str, columns: str, unique: bool, *, dry_run: bool) -> None:
    sql = f"CREATE {'UNIQUE ' if unique else ''}INDEX IF NOT EXISTS {name} ON {table} ({columns})"
    print(f"  + index {name} on {table}({columns})")
    if not dry_run:
        try:
            await conn.exec_driver_sql(sql)
        except Exception as exc:
            message = str(exc).lower()
            if "does not exist" in message or "no such table" in message:
                print(f"    · skipped {name}: table missing")
            else:
                raise


async def migrate(database_url: str, *, dry_run: bool = False) -> None:
    # Import model modules before create_all so metadata contains eval tables.
    from app.infra.models import eval as _eval_models  # noqa: F401
    from app.infra.models.base import Base

    dialect = _dialect(database_url)
    engine = _engine_for_url(database_url)
    try:
        async with engine.begin() as conn:
            print(f"Target dialect: {dialect}")
            if dry_run:
                print("Dry run: no changes will be applied")
            else:
                await conn.run_sync(Base.metadata.create_all)
                print("  ✓ Base.metadata.create_all completed")

            for table, columns in _column_plan(dialect).items():
                for column, column_type in columns:
                    await _add_column(conn, dialect, table, column, column_type, dry_run=dry_run)

            for name, table, columns, unique in _index_plan():
                await _create_index(conn, name, table, columns, unique, dry_run=dry_run)
    finally:
        await engine.dispose()


async def main() -> None:
    args = parse_args()
    from evals.persistence.postgres import resolve_eval_database_url

    database_url = resolve_eval_database_url(args.eval_database_url)
    if not database_url:
        raise SystemExit("EVAL_DATABASE_URL or DATABASE_URL is required")
    await migrate(database_url, dry_run=args.dry_run)
    print("\nDone. Eval platform schema is up to date.")


if __name__ == "__main__":
    asyncio.run(main())
