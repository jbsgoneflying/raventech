"""
Engine 9 — Credit Stress Drift: Redis Data Store

Persistent storage for transcript analyses, insider baselines, news scans,
thesis discoveries, and scan results.

Key patterns:
  e9:transcript:{TICKER}:{YEAR}Q{QUARTER}   LLM transcript analysis (90d TTL)
  e9:insider:{TICKER}:latest                 Latest insider scan (1d TTL)
  e9:insider:{TICKER}:baseline               Rolling 90d baseline (7d TTL)
  e9:news:daily:{DATE}                       Daily credit news scan (30d TTL)
  e9:thesis:latest                           Latest thesis discovery (7d TTL)
  e9:scan:latest                             Full scan result cache (5m TTL)
"""
from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any, Dict, List, Optional

from backend.redis_store import get_store_optional, RedisStore

LOG = logging.getLogger(__name__)

TTL_TRANSCRIPT = 90 * 86400    # 90 days
TTL_INSIDER_LATEST = 86400     # 1 day
TTL_INSIDER_BASELINE = 7 * 86400  # 7 days
TTL_NEWS = 30 * 86400          # 30 days
TTL_THESIS = 7 * 86400         # 7 days
TTL_SCAN = 5 * 60              # 5 minutes


def _store() -> Optional[RedisStore]:
    return get_store_optional()


# ---------------------------------------------------------------------------
# Transcript Analysis
# ---------------------------------------------------------------------------

def store_transcript_analysis(ticker: str, year: int, quarter: int, analysis: dict) -> bool:
    s = _store()
    if not s:
        return False
    key = f"e9:transcript:{ticker.upper()}:{year}Q{quarter}"
    return s.set_json(key, analysis, ttl_s=TTL_TRANSCRIPT)


def load_transcript_analysis(ticker: str, year: int, quarter: int) -> Optional[dict]:
    s = _store()
    if not s:
        return None
    key = f"e9:transcript:{ticker.upper()}:{year}Q{quarter}"
    return s.get_json(key)


def load_transcript_history(ticker: str, quarters: int = 4) -> List[dict]:
    """Load cached LLM analyses for the last N quarters, newest first."""
    s = _store()
    if not s:
        return []

    results = []
    today = date.today()
    y, q = today.year, (today.month - 1) // 3 + 1

    for _ in range(quarters + 2):
        data = load_transcript_analysis(ticker, y, q)
        if data:
            data["_year"] = y
            data["_quarter"] = q
            results.append(data)
        q -= 1
        if q < 1:
            q = 4
            y -= 1
        if len(results) >= quarters:
            break

    return results


# ---------------------------------------------------------------------------
# Insider Baselines
# ---------------------------------------------------------------------------

def store_insider_latest(ticker: str, data: dict) -> bool:
    s = _store()
    if not s:
        return False
    key = f"e9:insider:{ticker.upper()}:latest"
    return s.set_json(key, data, ttl_s=TTL_INSIDER_LATEST)


def load_insider_latest(ticker: str) -> Optional[dict]:
    s = _store()
    if not s:
        return None
    key = f"e9:insider:{ticker.upper()}:latest"
    return s.get_json(key)


def store_insider_baseline(ticker: str, baseline: dict) -> bool:
    s = _store()
    if not s:
        return False
    key = f"e9:insider:{ticker.upper()}:baseline"
    return s.set_json(key, baseline, ttl_s=TTL_INSIDER_BASELINE)


def load_insider_baseline(ticker: str) -> Optional[dict]:
    s = _store()
    if not s:
        return None
    key = f"e9:insider:{ticker.upper()}:baseline"
    return s.get_json(key)


def update_insider_baseline(ticker: str, current_monthly_net: float) -> dict:
    """
    Update rolling baseline with current month's net selling.
    Stores a list of monthly values (up to 6 months) and computes average.
    """
    existing = load_insider_baseline(ticker) or {"monthly_values": [], "avg": 0}
    values = existing.get("monthly_values", [])
    values.append(current_monthly_net)
    values = values[-6:]
    avg = sum(values) / len(values) if values else 0
    baseline = {"monthly_values": values, "avg": round(avg, 2), "months": len(values)}
    store_insider_baseline(ticker, baseline)
    return baseline


# ---------------------------------------------------------------------------
# News Scans
# ---------------------------------------------------------------------------

def store_news_scan(scan_date: str, data: dict) -> bool:
    s = _store()
    if not s:
        return False
    key = f"e9:news:daily:{scan_date}"
    return s.set_json(key, data, ttl_s=TTL_NEWS)


def load_news_scan(scan_date: Optional[str] = None) -> Optional[dict]:
    s = _store()
    if not s:
        return None
    if not scan_date:
        scan_date = date.today().isoformat()
    key = f"e9:news:daily:{scan_date}"
    return s.get_json(key)


# ---------------------------------------------------------------------------
# Thesis Discovery
# ---------------------------------------------------------------------------

def store_thesis(thesis: dict) -> bool:
    s = _store()
    if not s:
        return False
    return s.set_json("e9:thesis:latest", thesis, ttl_s=TTL_THESIS)


def load_thesis() -> Optional[dict]:
    s = _store()
    if not s:
        return None
    return s.get_json("e9:thesis:latest")


# ---------------------------------------------------------------------------
# Full Scan Cache
# ---------------------------------------------------------------------------

def store_scan_result(result: dict) -> bool:
    s = _store()
    if not s:
        return False
    return s.set_json("e9:scan:latest", result, ttl_s=TTL_SCAN)


def load_scan_result() -> Optional[dict]:
    s = _store()
    if not s:
        return None
    return s.get_json("e9:scan:latest")
