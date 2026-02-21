"""Engine 8 – Engine 1 Bridge.

Runs Engine 1 (breach analysis) internally for Engine 8's lifecycle:

  Phase A (pre-earnings): Runs ``compute_breach_stats()`` with trade builder
  enabled, persists the result to Redis for post-event use.

  Phase B (post-earnings): Loads the persisted Engine 1 result from Redis
  and derives the trade outcome from the IC structure + current price.

No retroactive Engine 1 execution — if Phase A was not run before earnings,
Phase B returns None and the frontend shows a "set up before earnings" message.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Dict, Optional

LOG = logging.getLogger(__name__)

_REDIS_PREFIX = "engine8:e1"
_REDIS_TTL_S = 30 * 86400  # 30 days


def _redis_key(ticker: str, earnings_date: str) -> str:
    return f"{_REDIS_PREFIX}:{ticker.upper()}:{earnings_date}"


def run_engine1_for_phase_a(
    *,
    ticker: str,
    orats_client: Any,
    store: Any = None,
    earnings_date: Optional[dt.date] = None,
    today: Optional[dt.date] = None,
    benzinga_client: Any = None,
) -> Dict[str, Any]:
    """Run Engine 1 breach analysis and persist to Redis for Phase B.

    Returns the full Engine 1 response dict with trade builder included.
    """
    from backend.earnings_logic import compute_breach_stats

    if today is None:
        today = dt.date.today()

    tb_inputs: Dict[str, Any] = {
        "mode": "auto",
        "symmetry": "auto",
        "wing_width": 5.0,
    }

    result = compute_breach_stats(
        client=orats_client,
        ticker=ticker.upper(),
        n=20,
        years=5,
        k=1.0,
        trade_builder_inputs=tb_inputs,
        today=today,
        benzinga_client=benzinga_client,
    )

    if store is not None and earnings_date is not None:
        key = _redis_key(ticker, earnings_date.isoformat())
        try:
            store.set_json(key, result, ttl_s=_REDIS_TTL_S)
            LOG.info("Engine 8 bridge: persisted E1 result for %s/%s", ticker, earnings_date)
        except Exception as e:
            LOG.warning("Engine 8 bridge: failed to persist E1 result: %s", e)

    return result


def load_engine1_for_phase_b(
    *,
    ticker: str,
    earnings_date: str,
    store: Any,
) -> Optional[Dict[str, Any]]:
    """Load persisted Engine 1 result from Redis for Phase B.

    Returns None if no Phase A data exists (desk skipped pre-earnings setup).
    """
    if store is None:
        return None
    key = _redis_key(ticker, earnings_date)
    try:
        data = store.get_json(key)
        if data and isinstance(data, dict):
            return data
    except Exception as e:
        LOG.warning("Engine 8 bridge: failed to load E1 result: %s", e)
    return None


def derive_trade_outcome_from_e1(
    engine1_result: Dict[str, Any],
    current_price: Optional[float],
    max_controlled_loss_pct: float = 50.0,
) -> str:
    """Derive trade outcome from Engine 1 tradeBuilder + current price.

    Returns: 'profitable', 'controlled_loss', 'breakdown', or 'unknown'.
    """
    from backend.engine8_activation import derive_trade_outcome

    tb = engine1_result.get("tradeBuilder")
    if not tb or not isinstance(tb, dict) or current_price is None:
        return "unknown"

    return derive_trade_outcome(tb, current_price, max_controlled_loss_pct)


def resolve_next_earnings(
    orats_client: Any,
    ticker: str,
    today: Optional[dt.date] = None,
) -> Optional[Dict[str, Any]]:
    """Resolve the next upcoming earnings date and timing from ORATS.

    Tries /cores snapshot first (has nextErn/nextErnTod), then falls back
    to /hist/earnings scanning for future dates. The fallback is needed
    because /cores may not return data on weekends/holidays.

    Returns dict with 'earnings_date', 'timing', 'expected_move_pct' or None.
    """
    from backend.earnings_logic import classify_timing

    if today is None:
        today = dt.date.today()

    # Strategy 1: ORATS /cores snapshot (best — has nextErn, nextErnTod, impErnMv)
    try:
        fields = "ticker,tradeDate,stockPrice,impErnMv,nextErn,nextErnTod,daysToNextErn"
        resp = orats_client.cores(ticker=ticker.upper(), fields=fields)
        if resp and getattr(resp, "rows", None):
            row = resp.rows[0] if resp.rows else None
            if row:
                next_ern = row.get("nextErn")
                if next_ern:
                    earn_date = dt.date.fromisoformat(str(next_ern)[:10])
                    if earn_date >= today:
                        timing = classify_timing(row.get("nextErnTod"))
                        days_to = (earn_date - today).days
                        imp = row.get("impErnMv")
                        em_pct = None
                        if imp is not None:
                            em_val = float(imp)
                            em_pct = abs(em_val) * 100.0 if abs(em_val) <= 1.0 else abs(em_val)
                        return {
                            "earnings_date": earn_date.isoformat(),
                            "timing": timing,
                            "days_to_earnings": days_to,
                            "expected_move_pct": round(em_pct, 2) if em_pct else None,
                            "stock_price": row.get("stockPrice"),
                        }
    except Exception as e:
        LOG.debug("Engine 8 bridge: /cores lookup failed for %s: %s", ticker, e)

    # Strategy 2: /hist/earnings — scan for future dates (works on weekends)
    try:
        resp = orats_client.hist_earnings(ticker.upper())
        if resp and getattr(resp, "rows", None):
            future = []
            for r in resp.rows:
                ed_str = r.get("earnDate")
                if not ed_str:
                    continue
                ed = dt.date.fromisoformat(str(ed_str)[:10])
                if ed >= today:
                    future.append((ed, r))
            if future:
                future.sort(key=lambda x: x[0])
                earn_date, row = future[0]
                annc = row.get("anncTod") or row.get("annc_tod") or row.get("anncTOD")
                timing = classify_timing(annc)
                days_to = (earn_date - today).days
                imp = row.get("impErnMv")
                em_pct = None
                if imp is not None:
                    em_val = float(imp)
                    em_pct = abs(em_val) * 100.0 if abs(em_val) <= 1.0 else abs(em_val)
                return {
                    "earnings_date": earn_date.isoformat(),
                    "timing": timing,
                    "days_to_earnings": days_to,
                    "expected_move_pct": round(em_pct, 2) if em_pct else None,
                    "stock_price": None,
                }
    except Exception as e:
        LOG.debug("Engine 8 bridge: /hist/earnings lookup failed for %s: %s", ticker, e)

    return None
