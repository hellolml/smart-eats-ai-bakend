#!/usr/bin/env python3
"""Run a lightweight component-level evaluation and persist it as an EvalRun."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evals.component_eval import build_component_report
from app.infra.eval_db import eval_session
from app.infra.models.eval import EvalRun


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run component eval")
    parser.add_argument("--component", choices=["router", "tool", "rag", "schema", "llm"], required=True)
    parser.add_argument("--dataset", default="component-regression")
    args = parser.parse_args()

    run_id = str(uuid4())
    report_name = f"component_{args.component}_{run_id}.json"
    report = build_component_report(args.component, args.dataset, owner="cli")
    async with eval_session() as session:
        session.add(EvalRun(
            id=run_id,
            report_name=report_name,
            timestamp=datetime.now(timezone.utc),
            suite=f"component:{args.component}",
            runner="component",
            overall_success_rate=float(report.get("overall_success_rate") or 0.0),
            total_cases=int(report.get("total_cases") or 0),
            total_trials=int(report.get("total_trials") or 0),
            duration_seconds=float(report.get("duration_seconds") or 0.0),
            raw_report_json=report,
            tags_json={"component": args.component, "dataset": args.dataset},
        ))
        await session.commit()
    print(json.dumps({"run_id": run_id, "report_name": report_name, "report": report}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_main())
