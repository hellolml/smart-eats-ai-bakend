#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path


project_root = Path(__file__).resolve().parent.parent.parent
if str(project_root) not in sys.path:
    sys.path.insert(0, str(project_root))

from app.agent.monitoring import evaluate_alert_rules, parse_window_start
from evals.persistence.postgres import EvalPersistenceStore, resolve_eval_database_url


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate monitoring alert rules")
    parser.add_argument("--window", default="24h", choices=["5m", "1h", "24h", "7d"], help="Lookback window")
    parser.add_argument("--eval-database-url", default=None, help="Evaluation database URL")
    parser.add_argument("--fail-on-alert", action="store_true", help="Exit 1 when alerts are open")
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
            alerts = await evaluate_alert_rules(session, since=parse_window_start(args.window))
            await session.commit()
        print(json.dumps({"window": args.window, "alerts": alerts}, ensure_ascii=False, indent=2, default=str))
        return 1 if args.fail_on_alert and alerts else 0
    finally:
        await store.close()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main_async()))
