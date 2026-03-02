#!/usr/bin/env python3
"""Engine 12 — VIX Spike Monitor (cron job).

Runs every 15 minutes during market hours. Fetches live VIX,
runs spike detection, and stores alert state in Redis.

Usage:
    python scripts/engine12_spike_monitor.py
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time

try:
    from dotenv import load_dotenv
    load_dotenv()
except Exception:
    pass

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(levelname)s %(message)s",
)
LOG = logging.getLogger("engine12_spike_monitor")

ALERT_KEY = "e12:alert:latest"
ALERT_TTL_S = 4 * 3600  # 4 hours


def main():
    from backend.eodhd_client import EodhdClient
    from backend.engine12_spike_detector import detect_vix_spike
    from backend.redis_store import get_store_optional

    try:
        eodhd = EodhdClient.from_env()
    except Exception as e:
        LOG.error("EODHD not configured: %s", e)
        return

    store = get_store_optional()
    if not store:
        LOG.error("Redis not available. Cannot store alert state.")
        return

    # Fetch EOD VIX history (last 30 days)
    import datetime as dt
    start = (dt.date.today() - dt.timedelta(days=40)).isoformat()
    try:
        resp = eodhd.get_eod("VIX.INDX", from_date=start)
        eod_closes = [
            float(r.get("adjusted_close") or r.get("close", 0))
            for r in (resp.rows or [])
            if r.get("adjusted_close") or r.get("close")
        ]
    except Exception as e:
        LOG.error("EOD VIX fetch failed: %s", e)
        return

    if len(eod_closes) < 21:
        LOG.warning("Insufficient EOD VIX data (%d bars)", len(eod_closes))
        return

    # Fetch live VIX quote
    live_vix = None
    try:
        resp = eodhd.get_live_quote("VIX.INDX")
        for row in resp.rows or ([resp.raw] if isinstance(resp.raw, dict) else []):
            for key in ("close", "previousClose", "last", "price"):
                v = row.get(key)
                if v is not None and float(v) > 5:
                    live_vix = float(v)
                    break
            if live_vix:
                break
    except Exception as e:
        LOG.warning("Live VIX quote failed: %s", e)

    if live_vix is not None:
        eod_closes.append(live_vix)
        LOG.info("Live VIX: %.2f (appended to %d EOD bars)", live_vix, len(eod_closes) - 1)
    else:
        LOG.info("No live VIX available; using EOD only (%d bars)", len(eod_closes))

    # Run spike detection
    spike = detect_vix_spike(eod_closes)

    alert_payload = {
        "detected": spike.detected,
        "vixCurrent": round(spike.vix_current, 2),
        "vix20dMA": round(spike.vix_20d_ma, 2),
        "spikePctAboveMA": round(spike.spike_pct_above_ma, 1),
        "zScore": round(spike.z_score, 2),
        "preEventRegime": spike.pre_event_regime,
        "timestamp": int(time.time()),
        "source": "live" if live_vix else "eod",
    }

    store.set_json(ALERT_KEY, alert_payload, ttl_s=ALERT_TTL_S)

    if spike.detected:
        LOG.warning(
            "SPIKE DETECTED: VIX %.2f (+%.1f%% above MA, z=%.2f)",
            spike.vix_current, spike.spike_pct_above_ma, spike.z_score,
        )
    else:
        LOG.info(
            "No spike: VIX %.2f (+%.1f%% above MA, z=%.2f)",
            spike.vix_current, spike.spike_pct_above_ma, spike.z_score,
        )


if __name__ == "__main__":
    main()
