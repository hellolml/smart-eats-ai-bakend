#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path


project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.agent.monitoring import create_dataset_case_from_trace
from evals.persistence.postgres import EvalPersistenceStore, resolve_eval_database_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export a production monitoring trace into an eval dataset case")
    parser.add_argument("--run-id", required=True, help="conversation_runs.id to export")
    parser.add_argument("--dataset", default="regression", help="Target dataset name")
    parser.add_argument("--version", default="draft", help="Target dataset version")
    parser.add_argument("--priority", default="p1", choices=["p0", "p1", "p2"], help="Eval case priority")
    parser.add_argument("--category", default="regression", help="Eval case category")
    parser.add_argument("--owner", default=None, help="Owner/reviewer id")
    parser.add_argument(
        "--review-status",
        default="draft",
        choices=["draft", "reviewing", "approved", "rejected", "needs_changes", "active", "archived"],
        help="Initial dataset case review status",
    )
    parser.add_argument("--eval-database-url", default=None, help="Evaluation database URL")
    return parser.parse_args()


async def main_async() -> int:
    args = parse_args()
    database_url = resolve_eval_database_url(args.eval_database_url)
    if not database_url:
        print("EVAL_DATABASE_URL is required", file=sys.stderr)
        return 2
    store = EvalPersistenceStore(database_url)
    try:
        await store.init_schema()
        async with store.session() as session:
            item = await create_dataset_case_from_trace(
                session,
                run_id=args.run_id,
                dataset_name=args.dataset,
                version=args.version,
                priority=args.priority,
                category=args.category,
                owner=args.owner,
                review_status=args.review_status,
            )
            if item is None:
                print(f"trace not found: {args.run_id}", file=sys.stderr)
                return 1
            await session.commit()
            print(f"created dataset case: {item['case_id']} -> {args.dataset}@{args.version}")
            return 0
    finally:
        await store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
