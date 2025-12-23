from __future__ import annotations

import math
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


def _pick_spot(rows: List[dict]) -> Optional[float]:
    # Prefer spotPrice if present, else stockPrice
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


def _bucket_ratio(r: float) -> str:
    # Deterministic thresholds for UI framing.
    # Low: mostly balanced; High: strongly one-sided.
    if r < 0.20:
        return "low"
    if r < 0.50:
        return "medium"
    return "high"


def compute_dealer_gamma_context(
    strikes_rows: List[dict],
    *,
    expiry: str | None = None,
    contract_multiplier: int = 100,
    band_pct: float = 0.05,
    top_n: int = 5,
) -> Dict[str, Any]:
    """
    Lightweight dealer gamma proxy (current-only; informational).

    We approximate dealer positioning via gamma concentration near spot:
      callsGex = Σ gamma * callOI * multiplier
      putsGex  = Σ gamma * putOI  * multiplier
      netGex   = callsGex - putsGex

    Notes:
    - Uses strike-level gamma from ORATS live strikes payload.
    - Prefers open interest. If missing, falls back to volume. If missing, gamma-only.
    - Filters strikes to a band around spot (±band_pct).
    """
    rows = [r for r in (strikes_rows or []) if isinstance(r, dict)]
    warnings: List[str] = []

    spot = _pick_spot(rows)
    if spot is None or spot <= 0:
        return {
            "spot": None,
            "expiry": expiry,
            "bandPct": float(band_pct),
            "weightingMode": "unknown",
            "warnings": ["Missing spot/stock price in live strikes payload."],
            "callsGex": None,
            "putsGex": None,
            "netGex": None,
            "netGammaSign": None,
            "magnitudeRatio": None,
            "magnitudeBucket": None,
            "callPutImbalance": None,
            "topGammaStrikes": [],
        }

    lo = float(spot) * (1.0 - float(band_pct))
    hi = float(spot) * (1.0 + float(band_pct))

    # Detect which weight fields exist.
    has_call_oi = any(_to_float(r.get("callOpenInterest")) is not None for r in rows)
    has_put_oi = any(_to_float(r.get("putOpenInterest")) is not None for r in rows)
    has_call_vol = any(_to_float(r.get("callVolume")) is not None for r in rows)
    has_put_vol = any(_to_float(r.get("putVolume")) is not None for r in rows)

    if has_call_oi or has_put_oi:
        weighting_mode = "oi"
        if not (has_call_oi and has_put_oi):
            warnings.append("Open interest missing on one side; using 0 for missing side.")
    elif has_call_vol or has_put_vol:
        weighting_mode = "volume"
        warnings.append("Open interest unavailable; using volume-weighted gamma proxy (noisy).")
    else:
        weighting_mode = "gamma_only"
        warnings.append("Open interest and volume unavailable; using gamma-only proxy (very noisy).")

    calls_gex = 0.0
    puts_gex = 0.0
    top_candidates: List[Tuple[float, Dict[str, Any]]] = []

    for r in rows:
        strike = _to_float(r.get("strike"))
        gamma = _to_float(r.get("gamma"))
        if strike is None or gamma is None:
            continue
        if not (lo <= float(strike) <= hi):
            continue

        if weighting_mode == "oi":
            call_w = _to_float(r.get("callOpenInterest")) or 0.0
            put_w = _to_float(r.get("putOpenInterest")) or 0.0
        elif weighting_mode == "volume":
            call_w = _to_float(r.get("callVolume")) or 0.0
            put_w = _to_float(r.get("putVolume")) or 0.0
        else:
            call_w = 1.0
            put_w = 1.0

        # Ensure non-negative weights
        call_w = max(0.0, float(call_w))
        put_w = max(0.0, float(put_w))

        c_gex = float(gamma) * call_w * float(contract_multiplier)
        p_gex = float(gamma) * put_w * float(contract_multiplier)
        calls_gex += c_gex
        puts_gex += p_gex

        # Pick dominant side for top strike reporting
        if abs(c_gex) >= abs(p_gex):
            side = "C"
            gex = c_gex
            weight = call_w
        else:
            side = "P"
            gex = p_gex
            weight = put_w
        top_candidates.append(
            (
                abs(float(gex)),
                {
                    "strike": float(strike),
                    "side": side,
                    "gex": float(gex),
                    "gamma": float(gamma),
                    "weight": float(weight),
                },
            )
        )

    net_gex = float(calls_gex) - float(puts_gex)
    gross = abs(float(calls_gex)) + abs(float(puts_gex))
    ratio = abs(float(net_gex)) / max(1e-9, gross)
    mag_bucket = _bucket_ratio(float(ratio))
    sign = "positive" if net_gex >= 0 else "negative"
    imbalance = (float(calls_gex) - float(puts_gex)) / max(1e-9, gross)

    # Top strikes by absolute gex density
    top_candidates.sort(key=lambda x: x[0], reverse=True)
    top_strikes = [d for _, d in top_candidates[: max(0, int(top_n))]]

    return {
        "spot": float(spot),
        "expiry": str(expiry)[:10] if expiry else None,
        "bandPct": float(band_pct),
        "weightingMode": weighting_mode,
        "warnings": warnings,
        "callsGex": round(float(calls_gex), 6),
        "putsGex": round(float(puts_gex), 6),
        "netGex": round(float(net_gex), 6),
        "netGammaSign": sign,
        "magnitudeRatio": round(float(ratio), 4),
        "magnitudeBucket": mag_bucket,
        "callPutImbalance": round(float(imbalance), 4),
        "topGammaStrikes": top_strikes,
    }


