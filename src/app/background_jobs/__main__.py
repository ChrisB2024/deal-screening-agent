"""Entry point: python -m app.background_jobs --concurrency 4

Starts a background job worker that claims and executes queued jobs.
"""

from __future__ import annotations

import argparse
import asyncio
import signal

from app.background_jobs import Worker, init_handlers
from app.database import async_session_factory


async def main(concurrency: int) -> None:
    # Bootstrap secrets/config
    from app.secrets_config import bootstrap as sc_bootstrap

    await sc_bootstrap()

    # Register handlers
    init_handlers()

    worker = Worker(async_session_factory, concurrency=concurrency)

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, worker.stop)

    await worker.run()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Background job worker")
    parser.add_argument("--concurrency", type=int, default=4, help="Number of concurrent claim slots")
    args = parser.parse_args()

    asyncio.run(main(args.concurrency))
