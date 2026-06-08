#!/usr/bin/env python3
"""Run a deterministic v1 multi-turn synthetic user simulation."""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.agent.monitoring import create_simulation_run
from app.infra.eval_db import eval_session


async def _main() -> None:
    parser = argparse.ArgumentParser(description="Run synthetic user simulation")
    parser.add_argument("--scenario-id", required=True)
    parser.add_argument("--max-turns", type=int, help="Reserved for future live simulation runner")
    parser.add_argument("--runner", choices=["deterministic", "live_agent"], default="deterministic")
    args = parser.parse_args()

    async with eval_session() as session:
        run = await create_simulation_run(
            session,
            args.scenario_id,
            max_turns_override=args.max_turns,
            runner=args.runner,
        )
        if run is None:
            raise SystemExit(f"scenario not found: {args.scenario_id}")
        await session.commit()
    print(json.dumps({"run": run}, ensure_ascii=False, indent=2, default=str))


if __name__ == "__main__":
    asyncio.run(_main())
