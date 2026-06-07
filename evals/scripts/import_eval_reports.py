#!/usr/bin/env python3
"""Import existing JSON evaluation reports into the eval database."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from evals.persistence.postgres import EvalPersistenceStore, normalize_report_name, resolve_eval_database_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import eval_report_*.json into eval DB")
    parser.add_argument("--results-dir", default="eval_results", help="Directory containing eval JSON reports")
    parser.add_argument("--eval-database-url", default=None, help="Evaluation PostgreSQL database URL")
    parser.add_argument("--include-latest", action="store_true", help="Import latest.json if no timestamp report is available")
    return parser.parse_args()


def report_files(results_dir: Path, include_latest: bool) -> list[Path]:
    files = sorted(results_dir.glob("eval_report_*.json"))
    if include_latest and (results_dir / "latest.json").is_file():
        files.append(results_dir / "latest.json")
    return [path for path in files if path.is_file()]


async def import_reports(database_url: str, files: list[Path]) -> int:
    store = EvalPersistenceStore(database_url)
    count = 0
    try:
        for path in files:
            report = json.loads(path.read_text(encoding="utf-8"))
            report_name = normalize_report_name(path, report)
            await store.upsert_report(report_name, report)
            count += 1
    finally:
        await store.close()
    return count


def main() -> None:
    args = parse_args()
    database_url = resolve_eval_database_url(args.eval_database_url)
    if not database_url:
        raise SystemExit("EVAL_DATABASE_URL is required")
    results_dir = Path(args.results_dir).expanduser().resolve()
    files = report_files(results_dir, args.include_latest)
    count = asyncio.run(import_reports(database_url, files))
    print(f"Imported {count} eval report(s) into DB")


if __name__ == "__main__":
    main()
