#!/usr/bin/env python3
"""Engine 5 – Nightly Global EOD Refresh Script (cron wrapper).

Usage:
    python scripts/refresh_engine5_snapshot.py [--force]
"""

from __future__ import annotations

import logging
import os
import sys

try:
    from dotenv import load_dotenv  # type: ignore
    load_dotenv()
except Exception:
    pass

# Ensure repo root is on sys.path for cron-friendly execution.
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)

from backend.engine5_pipeline import run_pipeline


def main() -> int:
    force = "--force" in sys.argv
    exit_code, snapshot_id = run_pipeline(force=force, source="cron")
    if snapshot_id:
        logging.getLogger(__name__).info("Snapshot created: %s", snapshot_id)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
