"""Shared Macro Event Calendar Engine.

Provides system-wide macro proximity signals that any engine can consume.
Consolidates the macro_multiplier logic (previously Engine 2-only) into a
reusable service with Redis caching.

Usage:
    from backend.macro_calendar_engine import get_macro_proximity
    ctx = get_macro_proximity()  # Returns MacroProximity for current week
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

HIGH_IMPACT_EVENTS = ("CPI", "FOMC_RATE_DECISION", "NFP", "FOMC_MINUTES",
                       "PPI", "RETAIL_SALES", "PMI_ISM")

BASE_WEIGHTS: Dict[str, float] = {
    "CPI": 0.50,
    "FOMC_RATE_DECISION": 0.60,
    "FOMC_MINUTES": 0.25,
    "NFP": 0.40,
    "PPI": 0.20,
    "RETAIL_SALES": 0.15,
    "PMI_ISM": 0.20,
    "TREASURY_REFUNDING": 0.15,
    "TREASURY_AUCTION": 0.05,
}

DECAY_LAMBDA = 0.25


@dataclass
class MacroEvent:
    """Single upcoming macro event."""
    date: str
    name: str
    key: str
    importance: int = 0
    days_away: int = 0
    weight: float = 0.0


@dataclass
class MacroProximity:
    """System-wide macro proximity signal for the current period."""
    as_of: str = ""
    window_start: str = ""
    window_end: str = ""
    multiplier: float = 1.0
    risk_level: str = "LOW"
    total_risk_score: float = 0.0
    events: List[MacroEvent] = field(default_factory=list)
    flags: Dict[str, bool] = field(default_factory=dict)
    sizing_guidance: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["events"] = [asdict(e) for e in self.events]
        return d


def _macro_key(name: str) -> Optional[str]:
    """Map event name to canonical key."""
    n = str(name or "").strip().lower()
    if not n:
        return None
    if "cpi" in n and "ppi" not in n:
        return "CPI"
    if "ppi" in n:
        return "PPI"
    if "retail" in n and "sales" in n:
        return "RETAIL_SALES"
    if "nonfarm" in n or "payroll" in n or "nfp" in n:
        return "NFP"
    if "pmi" in n or "ism" in n:
        return "PMI_ISM"
    if "fomc" in n and "minutes" in n:
        return "FOMC_MINUTES"
    if "fomc" in n or "interest rate decision" in n or "rate decision" in n:
        return "FOMC_RATE_DECISION"
    if "refunding" in n:
        return "TREASURY_REFUNDING"
    if "auction" in n or "treasury" in n:
        return "TREASURY_AUCTION"
    return None


def compute_macro_proximity(
    economics_rows: List[dict],
    *,
    as_of: Optional[dt.date] = None,
    window_days: int = 5,
    decay_lambda: float = DECAY_LAMBDA,
    multiplier_cap: float = 3.0,
) -> MacroProximity:
    """Compute macro proximity signal from Benzinga economics calendar rows.

    Args:
        economics_rows: Raw Benzinga economics calendar entries.
        as_of: Reference date (defaults to today).
        window_days: Forward window for proximity calculation.
        decay_lambda: Exponential decay rate for distance weighting.
        multiplier_cap: Maximum multiplier value.

    Returns MacroProximity with sizing guidance per engine type.
    """
    today = as_of or dt.date.today()
    window_end = today + dt.timedelta(days=window_days)

    events: List[MacroEvent] = []
    flags: Dict[str, bool] = {}
    risk_components: Dict[str, float] = {}

    for row in economics_rows:
        try:
            imp = int(float(row.get("importance") or 0))
        except (TypeError, ValueError):
            imp = 0
        ctry = str(row.get("country") or "").upper()
        if ctry and ctry not in ("US", "UNITED STATES", "USA"):
            continue
        if imp < 3:
            continue

        name = str(row.get("event_name") or "").strip()
        date_str = str(row.get("date") or "")[:10]
        try:
            event_date = dt.date.fromisoformat(date_str)
        except (ValueError, TypeError):
            continue

        days_away = (event_date - today).days
        if days_away < -1 or days_away > window_days:
            continue

        key = _macro_key(name)
        base_w = BASE_WEIGHTS.get(key, 0.10) if key else 0.10
        decay = math.exp(-decay_lambda * max(0, abs(days_away)))
        weight = base_w * decay

        events.append(MacroEvent(
            date=date_str, name=name, key=key or "OTHER",
            importance=imp, days_away=days_away, weight=round(weight, 4),
        ))

        if key:
            flags[key] = True
            risk_components[key] = risk_components.get(key, 0.0) + weight

    total_risk = sum(risk_components.values())
    multiplier = min(multiplier_cap, 1.0 + total_risk)

    if multiplier >= 2.0:
        risk_level = "EXTREME"
    elif multiplier >= 1.5:
        risk_level = "HIGH"
    elif multiplier >= 1.2:
        risk_level = "MODERATE"
    else:
        risk_level = "LOW"

    events.sort(key=lambda e: (e.days_away, -e.weight))

    # Per-engine sizing guidance (multiplier -> position size scale)
    sizing_guidance = {
        "iron_condor_width": max(0.5, 1.0 + (multiplier - 1.0) * 0.3),
        "credit_spread_sizing": max(0.3, 1.0 - (multiplier - 1.0) * 0.25),
        "pairs_sizing": max(0.5, 1.0 - (multiplier - 1.0) * 0.15),
        "vix_fade_sizing": max(0.4, 1.0 - (multiplier - 1.0) * 0.20),
    }

    return MacroProximity(
        as_of=today.isoformat(),
        window_start=today.isoformat(),
        window_end=window_end.isoformat(),
        multiplier=round(multiplier, 3),
        risk_level=risk_level,
        total_risk_score=round(total_risk, 4),
        events=events,
        flags=flags,
        sizing_guidance={k: round(v, 3) for k, v in sizing_guidance.items()},
    )


def get_macro_proximity(
    *,
    benzinga_client: Any = None,
    store: Any = None,
    as_of: Optional[dt.date] = None,
    window_days: int = 5,
    cache_ttl_s: int = 3600,
) -> MacroProximity:
    """High-level entry: fetch calendar data and compute proximity.

    Tries Redis cache first, then Benzinga API.
    Falls back to neutral signal on any failure.
    """
    today = as_of or dt.date.today()
    cache_key = f"macro_calendar:proximity:{today.isoformat()}"

    if store is not None:
        try:
            cached = store.get_json(cache_key)
            if cached is not None:
                _LOG.debug("Macro proximity cache hit: %s", cache_key)
                return MacroProximity(**{k: v for k, v in cached.items()
                                        if k in MacroProximity.__dataclass_fields__})
        except Exception:
            pass

    rows: List[dict] = []
    if benzinga_client is not None:
        try:
            window_end = today + dt.timedelta(days=window_days)
            resp = benzinga_client.calendar_economics(
                date_from=today.isoformat(),
                date_to=window_end.isoformat(),
                pagesize=500,
                page=0,
            )
            rows = resp.rows or []
        except Exception as exc:
            _LOG.warning("Macro calendar fetch failed: %s", exc)

    result = compute_macro_proximity(rows, as_of=today, window_days=window_days)

    if store is not None:
        try:
            store.set_json(cache_key, result.to_dict(), ttl_s=cache_ttl_s)
        except Exception:
            pass

    return result
