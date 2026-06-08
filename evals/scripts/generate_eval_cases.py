#!/usr/bin/env python3
"""Generate draft evaluation cases into the eval dataset store."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.monitoring import generate_dataset_cases
from app.infra.eval_db import eval_session


def _load_payload(args: argparse.Namespace) -> dict[str, Any]:
    if args.payload:
        return json.loads(args.payload)
    if args.payload_file:
        return json.loads(Path(args.payload_file).read_text(encoding="utf-8"))
    return {
        "task": args.task or "请补充这条评测任务",
        "scene": args.scene,
        "category": args.category,
        "priority": args.priority,
        "notes": args.notes,
    }


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Generate draft eval cases")
    parser.add_argument("--source", choices=["trace", "manual", "document", "report", "failure_report", "simulation"], default="manual")
    parser.add_argument("--dataset", default="regression")
    parser.add_argument("--version", default="draft")
    parser.add_argument("--task")
    parser.add_argument("--scene", default="chat")
    parser.add_argument("--category", default="regression")
    parser.add_argument("--priority", default="p1")
    parser.add_argument("--notes")
    parser.add_argument("--payload", help="JSON payload")
    parser.add_argument("--payload-file", help="JSON payload file")
    args = parser.parse_args()

    payload = _load_payload(args)
    async with eval_session() as session:
        cases = await generate_dataset_cases(
            session,
            dataset_name=args.dataset,
            source=args.source,
            payload=payload,
            version=args.version,
            owner="cli",
        )
        await session.commit()
    print(json.dumps({"generated": len(cases), "cases": cases}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_main())
