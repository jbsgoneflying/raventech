"""Market Intelligence v2 calibration CLI.

Runs the HMM calibration pipeline and persists the model to Redis + disk.
Intended to run weekly via cron / manual invocation.

Usage:
    python -m scripts.mi_calibrate                 # full calibration
    python -m scripts.mi_calibrate --lookback 504  # custom lookback (2y)
    python -m scripts.mi_calibrate --no-persist    # dry run, no writes
"""
from __future__ import annotations

import argparse
import json
import logging
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description="Market Intelligence HMM calibration")
    parser.add_argument("--lookback", type=int, default=1260,
                        help="Days of history to fit on (default 1260 = 5y)")
    parser.add_argument("--no-persist", action="store_true",
                        help="Do everything except write to Redis / disk")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )
    log = logging.getLogger("mi_calibrate")

    try:
        from backend.market_intel.calibration import run_calibration
    except Exception as e:
        log.error("cannot import calibration: %s", e)
        return 2

    log.info("Starting calibration: lookback=%dd persist=%s", args.lookback, not args.no_persist)
    report = run_calibration(
        lookback_days=args.lookback,
        persist=not args.no_persist,
    )
    print(json.dumps(report.to_dict(), indent=2, default=str))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
