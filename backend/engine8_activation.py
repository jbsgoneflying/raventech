"""Engine 8 – Activation Gate.

Validates all four non-negotiable preconditions before the Post-Event
Trade Extension engine runs:

  1. An Engine 1 trade exists with a valid tradeBuilder payload.
  2. The earnings event has occurred (earnings_date <= today).
  3. The system-derived trade_outcome is 'profitable' or 'controlled_loss'.
  4. At least one post-event price bar is available.

trade_outcome is NEVER accepted as user input.  It is derived
deterministically from the Engine 1 IC structure (short/long strikes,
totalCredit) and the current underlying price.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes (mirrors gating.py pattern)
# ---------------------------------------------------------------------------

@dataclass
class ActivationReason:
    code: str
    label: str
    severity: str          # HARD | SOFT
    detail: str
    source_value: Any = None
    threshold_value: Any = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ActivationResult:
    ticker: str
    activated: bool
    derived_trade_outcome: str   # profitable | controlled_loss | breakdown | unknown
    reasons: List[dict] = field(default_factory=list)
    trade_context: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Trade-outcome derivation
# ---------------------------------------------------------------------------

def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if not math.isfinite(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def derive_trade_outcome(
    trade_builder: dict,
    current_price: float,
    max_controlled_loss_pct: float = 50.0,
) -> str:
    """Derive trade outcome deterministically from IC structure + current price.

    Returns one of: 'profitable', 'controlled_loss', 'breakdown', 'unknown'.
    """
    put_leg = trade_builder.get("put") or {}
    call_leg = trade_builder.get("call") or {}

    short_put = _to_float(put_leg.get("shortStrike"))
    short_call = _to_float(call_leg.get("shortStrike"))
    long_put = _to_float(put_leg.get("longStrike"))
    long_call = _to_float(call_leg.get("longStrike"))
    total_credit = _to_float(trade_builder.get("totalCredit"))

    if short_put is None or short_call is None or total_credit is None:
        return "unknown"

    if short_put <= current_price <= short_call:
        return "profitable"

    # Outside short strikes – estimate intrinsic loss
    if current_price < short_put:
        intrinsic_loss = short_put - current_price
        if long_put is not None and current_price < long_put:
            return "breakdown"
    elif current_price > short_call:
        intrinsic_loss = current_price - short_call
        if long_call is not None and current_price > long_call:
            return "breakdown"
    else:
        intrinsic_loss = 0.0

    if total_credit > 0 and intrinsic_loss > 0:
        loss_pct = (intrinsic_loss / total_credit) * 100.0
        if loss_pct <= max_controlled_loss_pct:
            return "controlled_loss"
        return "breakdown"

    return "unknown"


# ---------------------------------------------------------------------------
# Activation checks
# ---------------------------------------------------------------------------

def _check_engine1_trade(engine1_trade: Optional[dict]) -> Optional[ActivationReason]:
    if not engine1_trade or not isinstance(engine1_trade, dict):
        return ActivationReason(
            code="NO_ENGINE1_TRADE",
            label="No Engine 1 trade available",
            severity="HARD",
            detail="Engine 8 requires an existing Engine 1 trade with tradeBuilder payload",
        )
    tb = engine1_trade.get("tradeBuilder") or engine1_trade.get("trade_builder")
    if not tb or not isinstance(tb, dict):
        return ActivationReason(
            code="NO_TRADE_BUILDER",
            label="Missing tradeBuilder payload",
            severity="HARD",
            detail="Engine 1 trade does not contain a tradeBuilder result",
        )
    put_leg = tb.get("put") or {}
    call_leg = tb.get("call") or {}
    if _to_float(put_leg.get("shortStrike")) is None or _to_float(call_leg.get("shortStrike")) is None:
        return ActivationReason(
            code="INCOMPLETE_TRADE_BUILDER",
            label="Incomplete tradeBuilder strikes",
            severity="HARD",
            detail="tradeBuilder missing shortStrike on put or call leg",
        )
    return None


def _check_event_occurred(earnings_date: Optional[dt.date], today: dt.date) -> Optional[ActivationReason]:
    if earnings_date is None:
        return ActivationReason(
            code="NO_EARNINGS_DATE",
            label="No earnings date available",
            severity="HARD",
            detail="Cannot determine if event has occurred without earnings_date",
        )
    if earnings_date > today:
        return ActivationReason(
            code="EVENT_NOT_OCCURRED",
            label="Event has not occurred yet",
            severity="HARD",
            detail=f"Earnings date {earnings_date} is in the future (today={today})",
            source_value=str(earnings_date),
            threshold_value=str(today),
        )
    return None


def _check_trade_outcome(outcome: str) -> Optional[ActivationReason]:
    if outcome in ("profitable", "controlled_loss"):
        return None
    return ActivationReason(
        code="TRADE_OUTCOME_BLOCKED",
        label=f"Trade outcome is '{outcome}'",
        severity="HARD",
        detail=f"Derived trade outcome '{outcome}' does not qualify (need profitable or controlled_loss)",
        source_value=outcome,
        threshold_value="profitable | controlled_loss",
    )


def _check_post_event_data(has_post_event_bar: bool) -> Optional[ActivationReason]:
    if has_post_event_bar:
        return None
    return ActivationReason(
        code="NO_POST_EVENT_DATA",
        label="No post-event price data",
        severity="HARD",
        detail="At least one daily bar after the event is required",
    )


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def check_activation(
    *,
    ticker: str,
    engine1_trade: Optional[dict],
    earnings_date: Optional[dt.date],
    current_price: Optional[float],
    has_post_event_bar: bool,
    max_controlled_loss_pct: float = 50.0,
    today: Optional[dt.date] = None,
) -> ActivationResult:
    """Run activation checks and return the result.

    HARD gates (block evaluation):
      - Earnings date must exist and be in the past
      - At least one post-event price bar must be available

    SOFT gates (informational only, do not block):
      - Engine 1 trade presence and trade outcome derivation
        Engine 8 works standalone for any post-earnings ticker.
    """
    if today is None:
        today = dt.date.today()

    hard_reasons: list[ActivationReason] = []
    soft_reasons: list[ActivationReason] = []

    # SOFT: Engine 1 trade context (informational)
    r = _check_engine1_trade(engine1_trade)
    if r:
        r.severity = "SOFT"
        soft_reasons.append(r)

    # HARD: event must have occurred
    r = _check_event_occurred(earnings_date, today)
    if r:
        hard_reasons.append(r)

    # Derive trade outcome (best-effort, does not block)
    outcome = "unknown"
    trade_builder: dict = {}
    if engine1_trade and isinstance(engine1_trade, dict):
        trade_builder = engine1_trade.get("tradeBuilder") or engine1_trade.get("trade_builder") or {}
    if trade_builder and current_price is not None:
        outcome = derive_trade_outcome(trade_builder, current_price, max_controlled_loss_pct)

    # SOFT: trade outcome (informational when Engine 1 context is missing)
    r = _check_trade_outcome(outcome)
    if r:
        r.severity = "SOFT"
        soft_reasons.append(r)

    # HARD: post-event price data
    r = _check_post_event_data(has_post_event_bar)
    if r:
        hard_reasons.append(r)

    activated = len(hard_reasons) == 0

    trade_context: Dict[str, Any] = {}
    if trade_builder:
        put_leg = trade_builder.get("put") or {}
        call_leg = trade_builder.get("call") or {}
        trade_context = {
            "shortPutStrike": _to_float(put_leg.get("shortStrike")),
            "shortCallStrike": _to_float(call_leg.get("shortStrike")),
            "longPutStrike": _to_float(put_leg.get("longStrike")),
            "longCallStrike": _to_float(call_leg.get("longStrike")),
            "totalCredit": _to_float(trade_builder.get("totalCredit")),
            "currentPrice": current_price,
        }

    return ActivationResult(
        ticker=ticker,
        activated=activated,
        derived_trade_outcome=outcome,
        reasons=[r.to_dict() for r in hard_reasons + soft_reasons],
        trade_context=trade_context,
    )
