#!/usr/bin/env python3
"""Backfill SPX historical option chains into the Engine 14 cache.

Fetches EOD `/hist/strikes` snapshots from ORATS for every trading day in
the requested lookback window and stores them via
`backend.engine14.chain_cache.upsert_chain`.

Usage:
    python scripts/engine14_backfill_chains.py               # 2y default
    python scripts/engine14_backfill_chains.py --years 3
    python scripts/engine14_backfill_chains.py --resume      # skip cached days
    python scripts/engine14_backfill_chains.py --ticker SPX --max-dte 60

Idempotent: re-running with --resume skips dates already in the manifest.

Rate-limiting note
------------------
ORATS historical endpoints are typically 10-60 req/s depending on plan;
we throttle conservatively at 1 req / 0.25s by default. Use --delay-ms to
tune. The script logs progress and resumes cleanly on Ctrl-C.
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import os
import sys
import time
from typing import List

# Allow `python scripts/engine14_backfill_chains.py` to find `backend.*`
_HERE = os.path.abspath(os.path.dirname(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.engine14 import chain_cache  # noqa: E402
from backend.orats_client import OratsClient, OratsError  # noqa: E402
from backend.spx_ic.ohlc import fetch_dailies_ohlc_range  # noqa: E402

LOG = logging.getLogger("engine14.backfill")


def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def _get_client() -> OratsClient:
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except Exception:
        pass
    return OratsClient.from_env()


def _enumerate_trade_dates(client: OratsClient, ticker: str, start: dt.date, end: dt.date) -> List[str]:
    bars = fetch_dailies_ohlc_range(client, ticker=ticker, start=start, end=end)
    return [b.trade_date for b in bars if b.close is not None]


def main() -> int:
    parser = argparse.ArgumentParser(description="Backfill Engine 14 SPX chain cache.")
    parser.add_argument("--ticker", default="SPX", help="Underlying ticker (default: SPX)")
    parser.add_argument("--years", type=float, default=2.0, help="Lookback window in years (default: 2.0)")
    parser.add_argument("--max-dte", type=int, default=45, help="Max DTE to request per trade_date (default: 45)")
    parser.add_argument("--delay-ms", type=int, default=250, help="Delay between requests in ms (default: 250)")
    parser.add_argument("--resume", action="store_true", help="Skip trade_dates already cached in the manifest")
    parser.add_argument("--start", type=str, default=None, help="Override start date (YYYY-MM-DD)")
    parser.add_argument("--end", type=str, default=None, help="Override end date (YYYY-MM-DD)")
    parser.add_argument("--limit", type=int, default=0, help="Cap rows processed (0=all; useful for smoke tests)")
    parser.add_argument("--verbose", action="store_true", help="Debug logging")
    args = parser.parse_args()

    _configure_logging(args.verbose)

    today = dt.date.today()
    start = dt.date.fromisoformat(args.start) if args.start else (today - dt.timedelta(days=int(args.years * 370)))
    end = dt.date.fromisoformat(args.end) if args.end else today

    LOG.info("Engine 14 backfill: ticker=%s start=%s end=%s max_dte=%s resume=%s",
             args.ticker, start.isoformat(), end.isoformat(), args.max_dte, args.resume)

    client = _get_client()

    LOG.info("Enumerating trading days from daily bars…")
    trade_dates = _enumerate_trade_dates(client, args.ticker, start, end)
    LOG.info("Found %d candidate trade dates.", len(trade_dates))

    if args.resume:
        cached = set(chain_cache.fetch_cached_trade_dates(ticker=args.ticker))
        trade_dates = [d for d in trade_dates if d not in cached]
        LOG.info("Resume mode: %d dates remaining after filtering cached.", len(trade_dates))

    if args.limit and args.limit > 0:
        trade_dates = trade_dates[: int(args.limit)]
        LOG.info("--limit %d applied: processing %d dates.", args.limit, len(trade_dates))

    if not trade_dates:
        LOG.info("Nothing to backfill.")
        return 0

    delay = max(0.0, float(args.delay_ms) / 1000.0)
    ok = 0
    failed: List[str] = []
    empty: List[str] = []
    t0 = time.time()

    try:
        for i, td in enumerate(trade_dates, start=1):
            try:
                n = chain_cache.fetch_and_cache_day(
                    client, ticker=args.ticker, trade_date=td, max_dte=int(args.max_dte),
                )
                if n == 0:
                    empty.append(td)
                else:
                    ok += 1
                if i % 25 == 0 or i == len(trade_dates):
                    elapsed = time.time() - t0
                    rate = i / max(1e-9, elapsed)
                    eta = (len(trade_dates) - i) / max(1e-9, rate)
                    LOG.info("[%d/%d] %s rows=%d · %.1f/s · ETA %ds",
                             i, len(trade_dates), td, n, rate, int(eta))
            except OratsError as e:
                LOG.warning("[%d/%d] ORATS error at %s: %s", i, len(trade_dates), td, e)
                failed.append(td)
            except Exception as e:
                LOG.exception("[%d/%d] unexpected error at %s: %s", i, len(trade_dates), td, e)
                failed.append(td)

            if delay:
                time.sleep(delay)
    except KeyboardInterrupt:
        LOG.warning("Interrupted by user. Progress saved to cache; re-run with --resume to continue.")

    cov = chain_cache.cache_coverage(ticker=args.ticker)
    LOG.info("Backfill complete: ok=%d empty=%d failed=%d", ok, len(empty), len(failed))
    LOG.info("Cache coverage: %s", cov)
    if failed:
        LOG.warning("%d failed dates (first 10): %s", len(failed), failed[:10])
    return 0 if not failed else 1


if __name__ == "__main__":
    raise SystemExit(main())
