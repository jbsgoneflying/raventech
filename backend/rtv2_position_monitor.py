"""RTv2.0 — Position Intelligence Monitor.

EOD evaluation of every active position: thesis tracking, 5-state
classification (ON_TRACK → INVALIDATED), suggested actions (HOLD → EXIT).

All evaluation is deterministic and rule-based.  No LLM, no ML.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

POSITION_STATES = ("ON_TRACK", "NEAR_TARGET", "RISK_INCREASING", "THESIS_WEAKENING", "INVALIDATED")
SUGGESTED_ACTIONS = ("HOLD", "TRIM", "TIGHTEN", "EXIT", "REVIEW")


# ---------------------------------------------------------------------------
# Evaluation helpers
# ---------------------------------------------------------------------------

def _trading_days_since(entry_date: str) -> int:
    """Approximate trading days (weekdays) between entry and today."""
    try:
        start = dt.date.fromisoformat(entry_date)
    except (ValueError, TypeError):
        return 0
    today = dt.date.today()
    if today <= start:
        return 0
    count = 0
    d = start
    while d < today:
        d += dt.timedelta(days=1)
        if d.weekday() < 5:
            count += 1
    return count


def _compute_pnl_pct(pos: dict, current_price: float) -> float:
    """P&L as a fraction of max risk (thesis_stop distance * units).

    Positive = profitable, negative = losing.
    Returns value in the range [-N, +N] where 1.0 = at target.
    """
    entry = float(pos.get("entry_price", 0))
    target = float(pos.get("thesis_target", 0))
    stop = float(pos.get("thesis_stop", 0))
    direction = str(pos.get("direction", "long")).lower()

    if entry == 0:
        return 0.0

    max_risk = abs(entry - stop) if stop else abs(entry * 0.05)
    if max_risk == 0:
        max_risk = abs(entry * 0.05) or 1.0

    if direction in ("long", "bullish", "bull"):
        raw_pnl = current_price - entry
    else:
        raw_pnl = entry - current_price

    target_dist = abs(target - entry) if target else max_risk
    if target_dist == 0:
        target_dist = max_risk

    return raw_pnl / target_dist


def _price_hit_stop(pos: dict, current_price: float) -> bool:
    """True if current price has crossed thesis_stop adversely."""
    stop = float(pos.get("thesis_stop", 0))
    if stop <= 0:
        return False
    direction = str(pos.get("direction", "long")).lower()
    if direction in ("long", "bullish", "bull"):
        return current_price <= stop
    else:
        return current_price >= stop


def _check_invalidation_conditions(pos: dict, dms: Optional[dict]) -> Optional[str]:
    """Check each invalidation condition against current state.

    Returns the first triggered condition string, or None.
    """
    conditions = pos.get("invalidation_conditions") or []
    if not conditions or dms is None:
        return None

    regime_state = ""
    if isinstance(dms, dict):
        regime = dms.get("regime")
        if isinstance(regime, dict):
            regime_state = str(regime.get("state", ""))
        elif isinstance(regime, str):
            regime_state = regime

    for cond in conditions:
        c = str(cond).lower()
        if "regime" in c and "stressed" in c and regime_state == "Stressed":
            return cond
        if "regime" in c and "risk-off" in c and regime_state == "Risk-Off":
            return cond
    return None


def _regime_turned_hostile(pos: dict, dms: Optional[dict]) -> bool:
    """Check if current regime is hostile to this trade type."""
    if dms is None:
        return False
    regime_state = ""
    if isinstance(dms, dict):
        regime = dms.get("regime")
        if isinstance(regime, dict):
            regime_state = str(regime.get("state", ""))
        elif isinstance(regime, str):
            regime_state = regime

    tt = str(pos.get("trade_type", ""))
    if tt == "mean_reversion" and regime_state == "Risk-On":
        return False
    if tt == "mean_reversion" and regime_state in ("Risk-Off", "Stressed"):
        return True
    if tt == "trend_continuation" and regime_state in ("Risk-Off", "Stressed"):
        return True
    if tt == "premium_decay" and regime_state == "Stressed":
        return True
    return False


def _vol_expanding_against(pos: dict, dms: Optional[dict]) -> bool:
    """True if vol is expanding against premium sellers."""
    if dms is None:
        return False
    vol_state = ""
    if isinstance(dms, dict):
        vol_state = str(dms.get("vol_state", dms.get("vol_direction", "")))

    bucket = str(pos.get("bucket", ""))
    tt = str(pos.get("trade_type", ""))
    if bucket == "income_core" or tt == "premium_decay":
        return vol_state.lower() in ("backwardation", "expanding")
    return False


# ---------------------------------------------------------------------------
# Core evaluation
# ---------------------------------------------------------------------------

def evaluate_position(
    pos: dict,
    current_price: float,
    dms: Optional[dict] = None,
) -> Tuple[str, str, str]:
    """Evaluate a single active position.

    Returns (position_state, suggested_action, reason).
    """
    pnl_pct = _compute_pnl_pct(pos, current_price)
    days = _trading_days_since(str(pos.get("entry_date", "")))
    max_days = int(pos.get("thesis_max_days", 0)) or 5
    time_ratio = days / max_days if max_days > 0 else 1.0

    # --- INVALIDATED ---
    if _price_hit_stop(pos, current_price):
        return ("INVALIDATED", "EXIT", "Price hit stop level")

    inv_cond = _check_invalidation_conditions(pos, dms)
    if inv_cond:
        return ("INVALIDATED", "EXIT", f"Invalidation condition triggered: {inv_cond}")

    # --- NEAR_TARGET ---
    if pnl_pct >= 0.80:
        return ("NEAR_TARGET", "REVIEW", f"P&L at {pnl_pct:.0%} of target")
    tt = str(pos.get("trade_type", ""))
    if tt == "premium_decay" and pnl_pct >= 0.50 and time_ratio >= 0.60:
        return ("NEAR_TARGET", "REVIEW", "Premium decay >50% with >60% time elapsed")

    # --- THESIS_WEAKENING ---
    if time_ratio >= 1.0 and pnl_pct < 0.30:
        return ("THESIS_WEAKENING", "REVIEW", "Max holding period reached, target not near")
    if pnl_pct <= -0.50:
        return ("THESIS_WEAKENING", "TIGHTEN", "P&L at -50% of max risk")
    if _regime_turned_hostile(pos, dms):
        return ("THESIS_WEAKENING", "TIGHTEN", "Regime now hostile to this trade type")

    # --- RISK_INCREASING ---
    if pnl_pct <= -0.30:
        return ("RISK_INCREASING", "TIGHTEN", "P&L at -30% of max risk")
    if time_ratio >= 0.80 and pnl_pct < 0.10:
        return ("RISK_INCREASING", "REVIEW", "80% of time elapsed, minimal progress")
    if _vol_expanding_against(pos, dms):
        return ("RISK_INCREASING", "REVIEW", "Vol expanding against position direction")

    # --- ON_TRACK ---
    return ("ON_TRACK", "HOLD", "Thesis intact, within expected parameters")


def evaluate_all_positions(
    positions: List[dict],
    prices: Dict[str, float],
    dms: Optional[dict] = None,
) -> List[dict]:
    """Evaluate every active position and return enriched records."""
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
    results = []

    for pos in positions:
        ticker = str(pos.get("ticker", ""))
        price = prices.get(ticker, float(pos.get("entry_price", 0)))

        state, action, reason = evaluate_position(pos, price, dms)
        pnl_pct = _compute_pnl_pct(pos, price)
        days = _trading_days_since(str(pos.get("entry_date", "")))

        enriched = dict(pos)
        enriched.update({
            "position_state": state,
            "suggested_action": action,
            "state_reason": reason,
            "current_pnl_pct": round(pnl_pct, 4),
            "days_in_trade": days,
            "last_evaluated": now_iso,
        })
        results.append(enriched)

    return results


def positions_summary(positions: List[dict]) -> Dict[str, int]:
    """Count positions by state."""
    counts: Dict[str, int] = {s: 0 for s in POSITION_STATES}
    for p in positions:
        state = str(p.get("position_state", "")).upper()
        if state in counts:
            counts[state] += 1
    counts["total"] = sum(counts.values())
    return counts
