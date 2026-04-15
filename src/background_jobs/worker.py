"""Spec-documented entry point: python -m background_jobs.worker --concurrency 4

Delegates to the canonical implementation at app.background_jobs.__main__.
"""

from __future__ import annotations

import argparse
import asyncio

from app.background_jobs.__main__ import main  # noqa: F401

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Background job worker")
    parser.add_argument(
        "--concurrency", type=int, default=4, help="Number of concurrent claim slots",
    )
    args = parser.parse_args()
    asyncio.run(main(args.concurrency))
