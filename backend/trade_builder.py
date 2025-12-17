from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from backend.config import get_flags
from backend.orats_client import OratsClient


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _imp_to_pct(imp_ern_mv: Any) -> Optional[float]:
    v = _to_float(imp_ern_mv)
    if v is None:
        return None
    v = abs(v)
    if v <= 1.0:
        return v * 100.0
    return v


def _pick_expiration_from_monies(
    client: OratsClient,
    *,
    ticker: str,
    trade_date: str,
    dte_target: int,
) -> Tuple[Optional[str], Optional[int], Optional[float], Optional[float], Optional[dict]]:
    """
    Use /hist/monies/implied to choose the expiration nearest dte_target.
    Returns (expirDate, dte, stockPrice, atm_iv, raw_row).
    """
    lo = max(1, int(dte_target) - 2)
    hi = int(dte_target) + 10
    fields = "ticker,tradeDate,expirDate,dte,stockPrice,vol50,atmiv"
    rows = client.hist_monies_implied(ticker=ticker, trade_date=trade_date, fields=fields, dte=f"{lo},{hi}").rows or []
    if not rows:
        return None, None, None, None, None
    best = None
    best_dist = None
    for r in rows:
        dte_val = _to_float(r.get("dte"))
        if dte_val is None:
            continue
        dist = abs(dte_val - float(dte_target))
        if best is None or best_dist is None or dist < best_dist:
            best = r
            best_dist = dist
    if best is None:
        best = rows[0]
    exp = str(best.get("expirDate") or "")[:10] if best.get("expirDate") else None
    dte_val = _to_float(best.get("dte"))
    stock = _to_float(best.get("stockPrice"))
    atm_iv = _to_float(best.get("vol50") or best.get("atmiv"))
    return exp, (int(dte_val) if dte_val is not None else None), stock, atm_iv, best


def _nearest_by(items: List[dict], key_fn, target: float) -> Optional[dict]:
    best = None
    best_dist = None
    for it in items:
        v = key_fn(it)
        if v is None:
            continue
        dist = abs(float(v) - float(target))
        if best is None or best_dist is None or dist < best_dist:
            best = it
            best_dist = dist
    return best


def _nearest_strike(strikes: List[float], target: float, *, side: str) -> Optional[float]:
    """Pick nearest available strike at or beyond target in the correct direction."""
    xs = sorted({float(s) for s in strikes if s is not None})
    if not xs:
        return None
    if side == "put_long":
        # long put is lower strike than short put: choose <= target
        cands = [s for s in xs if s <= target + 1e-9]
        return cands[-1] if cands else xs[0]
    if side == "call_long":
        # long call is higher strike than short call: choose >= target
        cands = [s for s in xs if s >= target - 1e-9]
        return cands[0] if cands else xs[-1]
    # generic nearest
    return min(xs, key=lambda s: abs(s - target))


def compute_trade_builder(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: str,
    inputs: Dict[str, Any],
    wing_recommendation: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Build strike-based suggestions for an earnings IC using ORATS /hist/strikes chain.

    If chain is unavailable/empty, returns a safe stub with notes.
    """
    mode = str(inputs.get("mode") or "auto")
    symmetry = str(inputs.get("symmetry") or "auto")
    dte_target = int(inputs.get("dte_target") or 2)
    wing_width = float(inputs.get("wing_width") or 5.0)
    exp_override = inputs.get("exp")
    flags = get_flags()

    # Determine expiration (current week) via monies implied unless overridden
    expir, expir_dte, spot_from_monies, _, _ = _pick_expiration_from_monies(
        client, ticker=ticker, trade_date=str(as_of_date)[:10], dte_target=dte_target
    )
    if exp_override:
        expir = str(exp_override)[:10]

    # Pull strikes chain around the target window
    if not expir:
        return {
            "underlyingPrice": spot_from_monies,
            "expiration": None,
            "modeUsed": mode,
            "symmetryUsed": symmetry,
            "put": {},
            "call": {},
            "totalCredit": None,
            "notes": ["Options chain unavailable: could not determine expiration."],
        }

    fields = ",".join(
        [
            "ticker",
            "tradeDate",
            "expirDate",
            "dte",
            "strike",
            "stockPrice",
            "callBidPrice",
            "callAskPrice",
            "putBidPrice",
            "putAskPrice",
            "callDelta",
            "putDelta",
            "delta",
            "callMidIv",
            "putMidIv",
        ]
    )

    rows = client.hist_strikes(ticker=ticker, trade_date=str(as_of_date)[:10], fields=fields, dte=f"{max(1,dte_target-2)},{dte_target+10}").rows or []
    rows = [r for r in rows if str(r.get("expirDate") or "")[:10] == expir]
    if not rows:
        return {
            "underlyingPrice": spot_from_monies,
            "expiration": expir,
            "modeUsed": mode,
            "symmetryUsed": symmetry,
            "put": {},
            "call": {},
            "totalCredit": None,
            "notes": ["Options chain unavailable for selected expiration (empty strikes response)."],
        }

    # Underlying price: use chain stockPrice if present
    underlying = _to_float(rows[0].get("stockPrice")) or spot_from_monies

    # Resolve auto mode using wingRecommendation if available
    mode_used = mode
    if mode == "auto" and wing_recommendation:
        sm = str(wing_recommendation.get("structureMode") or "")
        if sm == "AUTO_EQUAL_PREMIUM":
            mode_used = "equal_premium"
        else:
            mode_used = "equal_delta"

    # Choose short strikes
    target_delta = float(inputs.get("target_delta") or 0.10)
    target_premium = float(inputs.get("target_premium") or 0.50)

    def call_delta(r: dict) -> Optional[float]:
        v = _to_float(r.get("callDelta"))
        if v is not None:
            return v
        v = _to_float(r.get("delta"))
        return v if v is not None and v > 0 else None

    def put_delta(r: dict) -> Optional[float]:
        v = _to_float(r.get("putDelta"))
        if v is not None:
            return v
        v = _to_float(r.get("delta"))
        return v if v is not None and v < 0 else None

    def call_mid(r: dict) -> Optional[float]:
        b = _to_float(r.get("callBidPrice"))
        a = _to_float(r.get("callAskPrice"))
        if b is None or a is None:
            return None
        return 0.5 * (b + a)

    def put_mid(r: dict) -> Optional[float]:
        b = _to_float(r.get("putBidPrice"))
        a = _to_float(r.get("putAskPrice"))
        if b is None or a is None:
            return None
        return 0.5 * (b + a)

    notes: List[str] = []
    # Optional OTM constraint: keep shorts OTM (reduces accidental risk-profile shifts).
    call_rows = rows
    put_rows = rows
    if flags.TRADEBUILDER_ENFORCE_OTM and underlying is not None and underlying > 0:
        eps = 1e-9

        def _strike(r: dict) -> Optional[float]:
            return _to_float(r.get("strike"))

        call_rows = [r for r in rows if _strike(r) is not None and _strike(r) > float(underlying) + eps]
        put_rows = [r for r in rows if _strike(r) is not None and _strike(r) < float(underlying) - eps]
        if not call_rows:
            call_rows = rows
            notes.append("OTM enforcement: no OTM call strikes available; falling back to unconstrained selection.")
        if not put_rows:
            put_rows = rows
            notes.append("OTM enforcement: no OTM put strikes available; falling back to unconstrained selection.")

    if mode_used == "equal_premium":
        short_call = _nearest_by(call_rows, call_mid, target_premium)
        short_put = _nearest_by(put_rows, put_mid, target_premium)
    else:
        # equal_delta
        short_call = _nearest_by(call_rows, call_delta, target_delta)
        # putDelta is negative; target magnitude
        short_put = _nearest_by(put_rows, lambda r: (abs(put_delta(r)) if put_delta(r) is not None else None), target_delta)

    if not short_call or not short_put:
        return {
            "underlyingPrice": underlying,
            "expiration": expir,
            "modeUsed": mode_used,
            "symmetryUsed": symmetry,
            "put": {},
            "call": {},
            "totalCredit": None,
            "notes": ["Unable to select short strikes (missing delta/premium fields)."],
        }

    short_call_strike = _to_float(short_call.get("strike"))
    short_put_strike = _to_float(short_put.get("strike"))
    strikes = [_to_float(r.get("strike")) for r in rows]

    long_put_strike = None
    long_call_strike = None
    if short_put_strike is not None:
        long_put_strike = _nearest_strike([s for s in strikes if s is not None], short_put_strike - wing_width, side="put_long")
    if short_call_strike is not None:
        long_call_strike = _nearest_strike([s for s in strikes if s is not None], short_call_strike + wing_width, side="call_long")

    # Lookup long legs for pricing
    by_strike = {float(_to_float(r.get("strike"))): r for r in rows if _to_float(r.get("strike")) is not None}
    long_put = by_strike.get(float(long_put_strike)) if long_put_strike is not None else None
    long_call = by_strike.get(float(long_call_strike)) if long_call_strike is not None else None

    sp_mid = put_mid(short_put)
    lp_mid = put_mid(long_put) if long_put else None
    sc_mid = call_mid(short_call)
    lc_mid = call_mid(long_call) if long_call else None

    put_credit = (sp_mid - lp_mid) if (sp_mid is not None and lp_mid is not None) else None
    call_credit = (sc_mid - lc_mid) if (sc_mid is not None and lc_mid is not None) else None
    total_credit = (put_credit + call_credit) if (put_credit is not None and call_credit is not None) else None

    out = {
        "underlyingPrice": underlying,
        "expiration": expir,
        "modeUsed": mode_used,
        "symmetryUsed": symmetry,
        "put": {
            "shortStrike": short_put_strike,
            "longStrike": long_put_strike,
            "shortDelta": put_delta(short_put),
            "shortMid": sp_mid,
            "longMid": lp_mid,
            "credit": put_credit,
        },
        "call": {
            "shortStrike": short_call_strike,
            "longStrike": long_call_strike,
            "shortDelta": call_delta(short_call),
            "shortMid": sc_mid,
            "longMid": lc_mid,
            "credit": call_credit,
        },
        "totalCredit": total_credit,
        "notes": [
            "Chain-based strike selection enabled via ORATS /datav2/hist/strikes.",
            *notes,
        ],
    }
    return out

