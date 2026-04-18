#!/usr/bin/env python3
"""Backfill the Engine 14 multi-factor regime features table.

Phase C1 of the Engine 14 fine-tuning plan. Populates the SQLite table at
`FeatureFlags.ENGINE14_REGIME_FEATURES_PATH` with a per-trading-day row
capturing VIX / VIX9D / VVIX / term slope / RV20 / credit stress.

Dealer-gamma fields cannot be reconstructed historically and are left NULL
here — they'll be captured by a separate live-snapshot job once we start
persisting `compute_spx_live_levels` results.

Usage:
    python scripts/engine14_backfill_regime_features.py                 # 2y default
    python scripts/engine14_backfill_regime_features.py --years 3
    python scripts/engine14_backfill_regime_features.py --resume        # skip cached dates
    python scripts/engine14_backfill_regime_features.py --with-dms      # enrich w/ DMS stress

Idempotent: `upsert_features` uses ON CONFLICT DO UPDATE, so re-running is
safe and `--resume` is only an optimization (skips rows that already exist).
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
from typing import Optional

_HERE = os.path.abspath(os.path.dirname(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.engine14 import regime_features as rf  # noqa: E402

LOG = logging.getLogger("engine14.regime_features.backfill")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _build_price_service():
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    try:
        from backend.eodhd_client import EodhdClient
        from backend.price_service import PriceService
    except Exception as e:
        LOG.error("Could not import EODHD / PriceService: %s", e)
        return None
    try:
        client = EodhdClient.from_env()
        return PriceService(client)
    except Exception as e:
        LOG.error("Could not instantiate PriceService: %s", e)
        return None


def _build_store(enable: bool):
    if not enable:
        return None
    try:
        from backend.redis_store import get_store_optional
        return get_store_optional()
    except Exception as e:
        LOG.warning("Could not initialize Redis store: %s", e)
        return None


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--years", type=float, default=2.0,
                    help="Lookback window in years (default 2).")
    ap.add_argument("--start", type=str, default="",
                    help="Explicit start date (YYYY-MM-DD). Overrides --years.")
    ap.add_argument("--end", type=str, default="",
                    help="Explicit end date (YYYY-MM-DD). Defaults to today.")
    ap.add_argument("--resume", action="store_true",
                    help="Skip dates already present in the features DB.")
    ap.add_argument("--with-dms", action="store_true",
                    help="Enrich rows with DMS cross-asset-stress (requires Redis).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Fetch and print a summary without writing the DB.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    _configure_logging(args.verbose)

    today = dt.date.today()
    end = dt.date.fromisoformat(args.end) if args.end else today
    if args.start:
        start = dt.date.fromisoformat(args.start)
    else:
        start = end - dt.timedelta(days=int(float(args.years) * 370))

    LOG.info("Backfilling regime features %s..%s", start, end)
    ps = _build_price_service()
    if ps is None:
        LOG.error("No PriceService available — aborting.")
        return 2

    store = _build_store(bool(args.with_dms))
    if args.with_dms and store is None:
        LOG.warning("--with-dms requested but Redis unavailable; continuing without it.")

    rows = rf.compute_features_for_range(
        price_service=ps, start=start, end=end, store=store,
    )
    LOG.info("Fetched %d feature rows.", len(rows))

    if args.resume:
        existing = set(rf.cached_trade_dates())
        before = len(rows)
        rows = [r for r in rows if r.trade_date not in existing]
        LOG.info("Resume mode: %d new rows after filtering %d existing.", len(rows), before - len(rows))

    if args.dry_run:
        for r in rows[:10]:
            LOG.info("sample %s: vix=%s vix9d=%s vvix=%s rv20=%s stress=%s",
                     r.trade_date, r.vix, r.vix9d, r.vvix, r.rv20, r.credit_stress_label)
        LOG.info("Dry run — wrote nothing.")
        return 0

    n = rf.upsert_features_many(rows)
    LOG.info("Upserted %d rows into regime features DB.", n)
    cov = rf.coverage()
    LOG.info(
        "Coverage now: days=%d first=%s last=%s fieldCoverage=%s",
        cov.get("daysCovered"), cov.get("firstDate"), cov.get("lastDate"),
        cov.get("fieldCoverage"),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
