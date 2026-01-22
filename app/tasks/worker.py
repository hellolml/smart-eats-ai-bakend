from __future__ import annotations

import logging


logger = logging.getLogger("worker")


async def run_summary_worker() -> None:
    logger.warning("summary worker is deprecated; exiting")
    return


if __name__ == "__main__":
    asyncio.run(run_summary_worker())
