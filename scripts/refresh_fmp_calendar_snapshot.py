#!/usr/bin/env python3

from __future__ import annotations

import os
import sys

try:
    from dotenv import load_dotenv  # type: ignore

    load_dotenv()
except Exception:
    pass

# Ensure repo root is on sys.path when running as a script (cron-friendly).
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from backend.fmp_client import FmpClient
from backend.fmp_snapshot import refresh_fmp_earnings_snapshot_if_needed
from backend.redis_store import get_store_optional


def main() -> int:
    force = "--force" in sys.argv
    try:
        horizon_days = int(float(os.getenv("FMP_EARNINGS_HORIZON_DAYS") or 180))
    except Exception:
        horizon_days = 180
    store = get_store_optional()
    if store is None:
        print("Missing REDIS_URL; cannot refresh FMP earnings snapshot.", file=sys.stderr)
        return 2
    if not store.ping():
        print("Redis ping failed; cannot refresh FMP earnings snapshot.", file=sys.stderr)
        return 3

    client = FmpClient.from_env()
    res = refresh_fmp_earnings_snapshot_if_needed(client, store, force=force, horizon_days=int(horizon_days))
    print(
        f"ok={res.ok} etDate={res.etDate} horizonDays={int(horizon_days)} rowsUsed={res.rowsUsed} byDate={res.byDateSize} errors={res.errors} "
        f"notes={' '.join(res.notes)}"
    )
    return 0 if res.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())


