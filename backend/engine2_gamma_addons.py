from __future__ import annotations

import math
import statistics
from typing import Any, Dict, List, Optional, Tuple


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if not math.isfinite(f):
            return None
        return f
    except Exception:
        return None


def _clamp(lo: float, hi: float, x: float) -> float:
    return max(float(lo), min(float(hi), float(x)))


def _pick_spot(rows: List[dict]) -> Optional[float]:
    for key in ("spotPrice", "spot_price", "spot"):
        for r in rows:
            v = _to_float(r.get(key))
            if v and v > 0:
                return float(v)
    for key in ("stockPrice", "stock_price", "underlyingPrice"):
        for r in rows:
            v = _to_float(r.get(key))
            if v and v > 0:
                return float(v)
    return None


def _infer_weighting_mode(rows: List[dict]) -> str:
    has_oi = any((_to_float(r.get("callOpenInterest")) is not None) or (_to_float(r.get("putOpenInterest")) is not None) for r in rows)
    if has_oi:
        return "oi"
    has_vol = any((_to_float(r.get("callVolume")) is not None) or (_to_float(r.get("putVolume")) is not None) for r in rows)
    if has_vol:
        return "volume"
    return "gamma_only"


def _row_weight(row: dict, mode: str) -> float:
    if mode == "oi":
        w = (_to_float(row.get("callOpenInterest")) or 0.0) + (_to_float(row.get("putOpenInterest")) or 0.0)
    elif mode == "volume":
        w = (_to_float(row.get("callVolume")) or 0.0) + (_to_float(row.get("putVolume")) or 0.0)
    else:
        w = 1.0
    return max(0.0, float(w))


def compute_hedging_pressure(
    strikes_rows: List[dict],
    *,
    spot: Optional[float] = None,
    band_pct: float = 0.05,
    contract_multiplier: int = 100,
    adv_shares_20d: Optional[float] = None,
    weighting_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Dealer Hedging Pressure Index (HPI / Gamma Elasticity) — best-effort proxy.

    Uses a near-spot band to avoid far OI dominating. This is informational, not a positioning truth.
    """
    rows = [r for r in (strikes_rows or []) if isinstance(r, dict)]
    s0 = float(spot) if (spot is not None and float(spot) > 0) else (_pick_spot(rows) or None)
    if s0 is None or s0 <= 0:
        return {"enabled": False, "reason": "missing_spot"}

    mode = str(weighting_mode or _infer_weighting_mode(rows))
    lo = float(s0) * (1.0 - float(band_pct))
    hi = float(s0) * (1.0 + float(band_pct))

    gamma_total = 0.0
    n_used = 0
    for r in rows:
        strike = _to_float(r.get("strike"))
        if strike is None or not (lo <= float(strike) <= hi):
            continue
        g = _to_float(r.get("gamma"))
        if g is None:
            continue
        w = _row_weight(r, mode)
        # Gamma_total: sum gamma * weight * multiplier (per $1 move proxy)
        gamma_total += float(g) * float(w) * float(contract_multiplier)
        n_used += 1

    # Scenario flows (ΔS from % move)
    def _scenario(pct: float) -> Dict[str, Any]:
        ds = float(s0) * float(pct)
        hedge_shares = float(gamma_total) * float(ds)
        hedge_notional = float(hedge_shares) * float(s0)
        return {
            "movePct": float(pct) * 100.0,
            "deltaS": round(float(ds), 4),
            "hedgeShares": round(float(hedge_shares), 4),
            "hedgeNotional": round(float(hedge_notional), 2),
        }

    scenarios = [_scenario(0.0025), _scenario(0.0050), _scenario(0.0100)]
    notional_50bp = next((s.get("hedgeNotional") for s in scenarios if abs(float(s.get("movePct") or 0.0) - 0.5) < 1e-9), None)

    adv_notional = None
    elasticity = None
    if adv_shares_20d is not None and float(adv_shares_20d) > 0 and notional_50bp is not None:
        adv_notional = float(adv_shares_20d) * float(s0)
        if adv_notional > 0:
            elasticity = float(notional_50bp) / float(adv_notional)

    # Simple bucket for UI framing
    bucket = None
    if elasticity is not None:
        if elasticity < 0.10:
            bucket = "LOW"
        elif elasticity < 0.25:
            bucket = "MED"
        else:
            bucket = "HIGH"

    return {
        "enabled": True,
        "spot": round(float(s0), 6),
        "bandPct": float(band_pct),
        "weightingMode": mode,
        "gammaTotal": round(float(gamma_total), 6),
        "strikesUsed": int(n_used),
        "scenarios": scenarios,
        "advShares20d": (None if adv_shares_20d is None else round(float(adv_shares_20d), 2)),
        "advNotional20d": (None if adv_notional is None else round(float(adv_notional), 2)),
        "elasticity50bp": (None if elasticity is None else round(float(elasticity), 6)),
        "elasticityBucket": bucket,
    }


def _sum_oi_in_range(rows: List[dict], *, side: str, lo: float, hi: float, weighting_mode: str) -> float:
    tot = 0.0
    key_oi = "putOpenInterest" if side.upper().startswith("P") else "callOpenInterest"
    key_vol = "putVolume" if side.upper().startswith("P") else "callVolume"
    for r in rows:
        strike = _to_float(r.get("strike"))
        if strike is None or not (float(lo) <= float(strike) <= float(hi)):
            continue
        if weighting_mode == "oi":
            w = _to_float(r.get(key_oi)) or 0.0
        elif weighting_mode == "volume":
            w = _to_float(r.get(key_vol)) or 0.0
        else:
            w = 0.0
        if w and float(w) > 0:
            tot += float(w)
    return float(tot)


def _gex_slope_near_spot(
    rows: List[dict],
    *,
    spot: float,
    band_pct: float,
    contract_multiplier: int,
    weighting_mode: str,
) -> Optional[float]:
    """
    Best-effort slope of net GEX proxy vs strike around spot.
    netGexStrike ~ gamma * (callOI - putOI) * multiplier * spot^2
    """
    lo = float(spot) * (1.0 - float(band_pct))
    hi = float(spot) * (1.0 + float(band_pct))
    xs: List[float] = []
    ys: List[float] = []
    for r in rows:
        k = _to_float(r.get("strike"))
        if k is None or not (lo <= float(k) <= hi):
            continue
        g = _to_float(r.get("gamma"))
        if g is None:
            continue
        if weighting_mode == "oi":
            c = _to_float(r.get("callOpenInterest")) or 0.0
            p = _to_float(r.get("putOpenInterest")) or 0.0
        elif weighting_mode == "volume":
            c = _to_float(r.get("callVolume")) or 0.0
            p = _to_float(r.get("putVolume")) or 0.0
        else:
            c = 0.0
            p = 0.0
        net_w = float(c) - float(p)
        y = float(g) * float(net_w) * float(contract_multiplier) * float(spot) * float(spot)
        if not math.isfinite(y):
            continue
        xs.append(float(k))
        ys.append(float(y))
    if len(xs) < 4:
        return None
    mx = sum(xs) / len(xs)
    my = sum(ys) / len(ys)
    varx = sum((x - mx) ** 2 for x in xs)
    if varx <= 1e-12:
        return None
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return float(cov / varx)


def compute_tail_ignition(
    strikes_rows: List[dict],
    *,
    spot: Optional[float] = None,
    put_wall_strike: Optional[float] = None,
    call_wall_strike: Optional[float] = None,
    gamma_flip_strike: Optional[float] = None,
    band_pct_for_slope: float = 0.02,
    air_near_pct: float = 0.01,
    air_far_lo_pct: float = 0.015,
    air_far_hi_pct: float = 0.03,
    contract_multiplier: int = 100,
    weighting_mode: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Tail ignition risk (conditional severity) — best-effort proxy.
    Produces downside/upside scores in 0..100 with supporting fields.
    """
    rows = [r for r in (strikes_rows or []) if isinstance(r, dict)]
    s0 = float(spot) if (spot is not None and float(spot) > 0) else (_pick_spot(rows) or None)
    if s0 is None or s0 <= 0:
        return {"enabled": False, "reason": "missing_spot"}

    mode = str(weighting_mode or _infer_weighting_mode(rows))

    pw = _to_float(put_wall_strike) if put_wall_strike is not None else None
    cw = _to_float(call_wall_strike) if call_wall_strike is not None else None
    gf = _to_float(gamma_flip_strike) if gamma_flip_strike is not None else None

    # Distances
    dist_put = None if pw is None else (float(s0) - float(pw)) / float(s0)
    dist_call = None if cw is None else (float(cw) - float(s0)) / float(s0)

    # Air pocket density ratio beyond wall
    def _air(side: str, wall: Optional[float]) -> Tuple[Optional[float], Dict[str, Any]]:
        if wall is None or wall <= 0:
            return None, {"near": None, "far": None, "nearSum": None, "farSum": None}
        near_lo = float(wall) * (1.0 - float(air_near_pct))
        near_hi = float(wall) * (1.0 + float(air_near_pct))
        if side.upper().startswith("P"):
            far_lo = float(wall) * (1.0 - float(air_far_hi_pct))
            far_hi = float(wall) * (1.0 - float(air_far_lo_pct))
        else:
            far_lo = float(wall) * (1.0 + float(air_far_lo_pct))
            far_hi = float(wall) * (1.0 + float(air_far_hi_pct))

        near_sum = _sum_oi_in_range(rows, side=side, lo=min(near_lo, near_hi), hi=max(near_lo, near_hi), weighting_mode=mode)
        far_sum = _sum_oi_in_range(rows, side=side, lo=min(far_lo, far_hi), hi=max(far_lo, far_hi), weighting_mode=mode)
        near_w = max(1e-9, abs(float(near_hi) - float(near_lo)))
        far_w = max(1e-9, abs(float(far_hi) - float(far_lo)))
        near_den = float(near_sum) / float(near_w)
        far_den = float(far_sum) / float(far_w)
        ratio = None if near_den <= 1e-12 else (float(far_den) / float(near_den))
        air_score = None
        if ratio is not None:
            air_score = _clamp(0.0, 1.0, 1.0 - float(ratio))
        return air_score, {
            "near": {"lo": near_lo, "hi": near_hi, "width": near_w},
            "far": {"lo": far_lo, "hi": far_hi, "width": far_w},
            "nearSum": round(float(near_sum), 4),
            "farSum": round(float(far_sum), 4),
            "densityRatioFarOverNear": (None if ratio is None else round(float(ratio), 6)),
        }

    air_down, air_down_dbg = _air("P", pw)
    air_up, air_up_dbg = _air("C", cw)

    flip_dist = None if gf is None else abs(float(s0) - float(gf)) / float(s0)

    slope = _gex_slope_near_spot(rows, spot=float(s0), band_pct=float(band_pct_for_slope), contract_multiplier=int(contract_multiplier), weighting_mode=mode)
    # Normalize slope to a unitless-ish measure for UI (avoid huge values)
    slope_norm = None
    if slope is not None:
        slope_norm = abs(float(slope)) / max(1e-9, float(s0) * float(s0))

    # Map to risks 0..1
    def _dist_risk(d: Optional[float]) -> float:
        if d is None:
            return 0.5
        # If wall is within 2% => elevated; farther => low; if already breached => max.
        return _clamp(0.0, 1.0, (0.02 - float(d)) / 0.02)

    def _flip_risk(d: Optional[float]) -> float:
        if d is None:
            return 0.5
        return _clamp(0.0, 1.0, (0.02 - float(d)) / 0.02)

    def _slope_risk(x: Optional[float]) -> float:
        if x is None:
            return 0.5
        # heuristic scaling: 0.0..~1.0
        return _clamp(0.0, 1.0, float(x) / 0.0005)

    down01 = 0.35 * _dist_risk(dist_put) + 0.30 * (air_down if air_down is not None else 0.5) + 0.20 * _flip_risk(flip_dist) + 0.15 * _slope_risk(slope_norm)
    up01 = 0.35 * _dist_risk(dist_call) + 0.30 * (air_up if air_up is not None else 0.5) + 0.20 * _flip_risk(flip_dist) + 0.15 * _slope_risk(slope_norm)

    def _label(x01: float) -> str:
        if x01 < 0.33:
            return "LOW"
        if x01 < 0.66:
            return "MED"
        return "HIGH"

    return {
        "enabled": True,
        "spot": round(float(s0), 6),
        "weightingMode": mode,
        "putWallStrike": (None if pw is None else round(float(pw), 2)),
        "callWallStrike": (None if cw is None else round(float(cw), 2)),
        "gammaFlipStrike": (None if gf is None else round(float(gf), 2)),
        "distToPutWallPct": (None if dist_put is None else round(100.0 * float(dist_put), 4)),
        "distToCallWallPct": (None if dist_call is None else round(100.0 * float(dist_call), 4)),
        "flipDistancePct": (None if flip_dist is None else round(100.0 * float(flip_dist), 4)),
        "gexSlopeNorm": (None if slope_norm is None else round(float(slope_norm), 8)),
        "down": {"score": int(round(100.0 * down01)), "label": _label(down01), "airPocket01": (None if air_down is None else round(float(air_down), 6)), "air": air_down_dbg},
        "up": {"score": int(round(100.0 * up01)), "label": _label(up01), "airPocket01": (None if air_up is None else round(float(air_up), 6)), "air": air_up_dbg},
        "notes": [
            "Best-effort proxy: uses OI/volume density near walls, gamma-flip proximity, and a simple near-spot GEX slope proxy.",
        ],
    }


def _zscore(x: float, hist: List[float]) -> float:
    vals = [float(v) for v in hist if v is not None and math.isfinite(float(v))]
    if len(vals) < 8:
        return 0.0
    m = sum(vals) / len(vals)
    try:
        s = statistics.pstdev(vals)
    except Exception:
        s = 0.0
    if not math.isfinite(s) or s <= 1e-9:
        return 0.0
    return float((float(x) - float(m)) / float(s))


def compute_vol_pressure(
    *,
    asof: str,
    dates_sorted: List[str],
    iv7_by_date: Dict[str, float],
    iv30_by_date: Dict[str, float],
    rv10_by_date: Dict[str, float],
    slope_by_date: Dict[str, float],
    window: int = 60,
) -> Dict[str, Any]:
    """
    Volatility supply/demand imbalance proxy.
    Produces a z-score style composite (negative ~ offered, positive ~ bid).
    """
    d0 = str(asof)[:10]
    if d0 not in dates_sorted:
        return {"enabled": False, "reason": "asof_not_in_series"}

    def prev_date(d: str) -> Optional[str]:
        try:
            idx = dates_sorted.index(d)
        except ValueError:
            return None
        return dates_sorted[idx - 1] if idx > 0 else None

    pv = prev_date(d0)

    iv7 = iv7_by_date.get(d0)
    iv30 = iv30_by_date.get(d0)
    rv10 = rv10_by_date.get(d0)
    slope = slope_by_date.get(d0)

    iv7_prev = iv7_by_date.get(pv) if pv else None
    slope_prev = slope_by_date.get(pv) if pv else None

    d_iv = None if (iv7 is None or iv7_prev is None) else float(iv7) - float(iv7_prev)
    d_skew = None if (slope is None or slope_prev is None) else float(slope) - float(slope_prev)
    ivrv = None if (iv7 is None or rv10 is None) else float(iv7) - float(rv10)
    term = None if (iv7 is None or iv30 is None) else float(iv7) - float(iv30)

    # Rolling histories (exclude current day to avoid look-ahead bias)
    idx = dates_sorted.index(d0)
    hist_dates = dates_sorted[max(0, idx - int(window)) : idx]

    def hist_series(fn) -> List[float]:
        out = []
        for d in hist_dates:
            v = fn(d)
            if v is not None and math.isfinite(float(v)):
                out.append(float(v))
        return out

    hist_d_iv = hist_series(lambda d: (None if prev_date(d) is None else (iv7_by_date.get(d) - iv7_by_date.get(prev_date(d))) if (iv7_by_date.get(d) is not None and iv7_by_date.get(prev_date(d)) is not None) else None))
    hist_d_skew = hist_series(lambda d: (None if prev_date(d) is None else (slope_by_date.get(d) - slope_by_date.get(prev_date(d))) if (slope_by_date.get(d) is not None and slope_by_date.get(prev_date(d)) is not None) else None))
    hist_ivrv = hist_series(lambda d: (iv7_by_date.get(d) - rv10_by_date.get(d)) if (iv7_by_date.get(d) is not None and rv10_by_date.get(d) is not None) else None)
    hist_term = hist_series(lambda d: (iv7_by_date.get(d) - iv30_by_date.get(d)) if (iv7_by_date.get(d) is not None and iv30_by_date.get(d) is not None) else None)

    z_di = 0.0 if d_iv is None else _zscore(float(d_iv), hist_d_iv)
    z_ds = 0.0 if d_skew is None else _zscore(float(d_skew), hist_d_skew)
    z_ivrv = 0.0 if ivrv is None else _zscore(float(ivrv), hist_ivrv)
    z_term = 0.0 if term is None else _zscore(float(term), hist_term)

    score = 0.25 * z_di + 0.25 * z_ds + 0.25 * z_ivrv + 0.25 * z_term

    state = "NEUTRAL"
    if score >= 0.5:
        state = "BID"
    elif score <= -0.5:
        state = "OFFERED"

    return {
        "enabled": True,
        "asOfDate": d0,
        "state": state,
        "scoreZ": round(float(score), 4),
        "inputs": {
            "iv7": (None if iv7 is None else round(float(iv7), 4)),
            "iv30": (None if iv30 is None else round(float(iv30), 4)),
            "rv10": (None if rv10 is None else round(float(rv10), 4)),
            "slope": (None if slope is None else round(float(slope), 6)),
            "dIv": (None if d_iv is None else round(float(d_iv), 4)),
            "dSkew": (None if d_skew is None else round(float(d_skew), 6)),
            "ivRv": (None if ivrv is None else round(float(ivrv), 4)),
            "termSlope": (None if term is None else round(float(term), 4)),
        },
        "z": {
            "dIv": round(float(z_di), 4),
            "dSkew": round(float(z_ds), 4),
            "ivRv": round(float(z_ivrv), 4),
            "term": round(float(z_term), 4),
        },
        "notes": [
            "Composite uses rolling z-scores (60d lookback) of ΔIV, Δskew proxy (cores.slope), IV−RV, and term slope (IV7−IV30).",
        ],
    }


