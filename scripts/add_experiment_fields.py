#!/usr/bin/env python3
"""Compatibility wrapper for the full eval platform migration.

Run: python scripts/add_experiment_fields.py

Safe to re-run. Prefer ``python scripts/migrate_eval_platform.py`` for new usage.
"""

import asyncio
import sys
from pathlib import Path

# Ensure project root on sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


async def main() -> None:
    from scripts.migrate_eval_platform import migrate
    from evals.persistence.postgres import resolve_eval_database_url

    database_url = resolve_eval_database_url()
    if not database_url:
        raise SystemExit("EVAL_DATABASE_URL or DATABASE_URL is required")
    await migrate(database_url)


if __name__ == "__main__":
    asyncio.run(main())
