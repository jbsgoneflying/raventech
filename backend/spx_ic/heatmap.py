from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple
import datetime as dt
import logging

from backend.spx_ic.utils import (
    _to_float,
    _parse_date,
    _normalize_expiry_dates,
)

LOG = logging.getLogger("spx_ic_engine")


def _finite(v: Any) -> Optional[float]:
    x = _to_float(v)
    return None if x is None else float(x)


def compute_spx_net_gex_heatmap(
    strikes_rows: List[dict],
    *,
    expiries: List[str],
    spot: float,
    band_pct: float = 0.05,
    contract_multiplier: int = 100,
    strike_cap: int = 180,
) -> Dict[str, Any]:
    """
    Build an expiry × strike matrix of net dollar gamma exposure (best-effort proxy).

      net$GEX(strike,exp) ≈ gamma * (callOI - putOI) * spot^2 * contract_multiplier

    Notes:
    - Uses ORATS live strikes payload for gamma + OI.
    - If OI is missing, falls back to volume (noisy); otherwise gamma-only (very noisy).
    - Filters to strikes within ±band_pct of spot to keep the matrix compact.
    """
    rows = [r for r in (strikes_rows or []) if isinstance(r, dict)]
    out_exp = [str(e)[:10] for e in (expiries or []) if e]

    s0 = float(spot)
    lo = s0 * (1.0 - float(band_pct))
    hi = s0 * (1.0 + float(band_pct))

    has_call_oi = any(_to_float(r.get("callOpenInterest")) is not None for r in rows)
    has_put_oi = any(_to_float(r.get("putOpenInterest")) is not None for r in rows)
    has_call_vol = any(_to_float(r.get("callVolume")) is not None for r in rows)
    has_put_vol = any(_to_float(r.get("putVolume")) is not None for r in rows)

    warnings: List[str] = []
    if has_call_oi or has_put_oi:
        weighting_mode = "oi"
        if not (has_call_oi and has_put_oi):
            warnings.append("Open interest missing on one side; using 0 for missing side.")
    elif has_call_vol or has_put_vol:
        weighting_mode = "volume"
        warnings.append("Open interest unavailable; using volume-weighted net $GEX proxy (noisy).")
    else:
        weighting_mode = "gamma_only"
        warnings.append("Open interest and volume unavailable; using gamma-only net $GEX proxy (very noisy).")

    rows_by_exp: Dict[str, List[dict]] = {e: [] for e in out_exp}
    for r in rows:
        ex = str(r.get("expirDate") or r.get("expiry") or r.get("expDate") or r.get("exp_date") or "")[:10]
        if ex in rows_by_exp:
            rows_by_exp[ex].append(r)

    strike_set = set()
    for ex in out_exp:
        for r in rows_by_exp.get(ex) or []:
            k = _to_float(r.get("strike"))
            if k is None:
                continue
            kk = float(k)
            if lo <= kk <= hi:
                strike_set.add(kk)
    strikes = sorted(strike_set)

    cap = max(40, int(strike_cap))
    if len(strikes) > cap:
        stride = int(math.ceil(len(strikes) / float(cap)))
        strikes = strikes[:: max(1, stride)]

    mat: List[List[Optional[float]]] = []
    for ex in out_exp:
        by_strike: Dict[float, float] = {}
        for r in rows_by_exp.get(ex) or []:
            k = _to_float(r.get("strike"))
            gamma = _to_float(r.get("gamma"))
            if k is None or gamma is None:
                continue
            kk = float(k)
            if kk not in strike_set:
                continue

            if weighting_mode == "oi":
                c = _to_float(r.get("callOpenInterest")) or 0.0
                p = _to_float(r.get("putOpenInterest")) or 0.0
            elif weighting_mode == "volume":
                c = _to_float(r.get("callVolume")) or 0.0
                p = _to_float(r.get("putVolume")) or 0.0
            else:
                c = 1.0
                p = 1.0
            c = max(0.0, float(c))
            p = max(0.0, float(p))

            val = float(gamma) * (c - p) * (s0**2) * float(contract_multiplier)
            if math.isfinite(val):
                by_strike[kk] = by_strike.get(kk, 0.0) + float(val)

        row_vals: List[Optional[float]] = []
        for kk in strikes:
            v = by_strike.get(float(kk))
            row_vals.append(None if v is None else round(float(v), 6))
        mat.append(row_vals)

    return {
        "enabled": True,
        "spot": round(float(s0), 6),
        "bandPct": float(band_pct),
        "weightingMode": weighting_mode,
        "contractMultiplier": int(contract_multiplier),
        "expiries": out_exp,
        "strikes": [round(float(k), 6) for k in strikes],
        "netDollarGex": mat,
        "warnings": warnings,
        "notes": [
            "Net $GEX is a best-effort proxy computed from ORATS strike gamma and OI; not a full dealer positioning model.",
            "Missing strikes are returned as null (not zero).",
        ],
    }


# ---------------------------------------------------------------------------
# Levels computation helpers
# ---------------------------------------------------------------------------

def _row_slope_first_diff(vals: List[Optional[float]]) -> List[Optional[float]]:
    """
    First difference across strikes: slope[i] = vals[i] - vals[i-1].
    Keeps the same length; slope[0] = None.

    IMPORTANT: this is computed on RAW Net $GEX (not normalized).
    """
    out: List[Optional[float]] = [None] * len(vals)
    if not vals:
        return out
    prev = vals[0]
    for i in range(1, len(vals)):
        cur = vals[i]
        if cur is None or prev is None:
            out[i] = None
        else:
            out[i] = float(cur) - float(prev)
        prev = cur
    return out


def _rolling_mean(vals: List[Optional[float]], *, window: int) -> List[Optional[float]]:
    """
    Centered rolling mean ignoring None; returns None where insufficient data.
    """
    w = int(window)
    if w <= 1:
        return list(vals)
    n = len(vals)
    out: List[Optional[float]] = [None] * n
    half = w // 2
    for i in range(n):
        lo = max(0, i - half)
        hi = min(n, i + half + 1)
        xs = [float(x) for x in vals[lo:hi] if x is not None and math.isfinite(float(x))]
        if len(xs) >= max(2, min(w, hi - lo) // 2):
            out[i] = float(sum(xs) / float(len(xs)))
        else:
            out[i] = None
    return out


def _apply_slope(vals: List[Optional[float]], *, window: int) -> List[Optional[float]]:
    """
    Slope = first difference across strikes, then rolling-mean smoothing.
    Computed on RAW Net $GEX; normalization is applied only at render time.
    """
    return _rolling_mean(_row_slope_first_diff(vals), window=int(window))


def _parse_expiry_safe(s: str) -> Optional[dt.date]:
    try:
        return _parse_date(str(s)[:10])
    except Exception:
        return None


def _select_expiries_by_dte(
    exp_dates: List[str],
    *,
    today: dt.date,
    dte_max: int,
    cap: int,
) -> List[Tuple[str, int]]:
    """
    Return (expiry_iso, dte_days) sorted by expiry date, filtered to 0..dte_max.
    """
    out: List[Tuple[str, int]] = []
    for e in _normalize_expiry_dates(exp_dates):
        ed = _parse_expiry_safe(e)
        if ed is None:
            continue
        dte = int((ed - today).days)
        if dte < 0 or dte > int(dte_max):
            continue
        out.append((str(e)[:10], dte))
    out.sort(key=lambda x: x[0])
    if cap and len(out) > int(cap):
        out = out[: int(cap)]
    return out


def _bucket_for_dte(dte: int) -> Optional[str]:
    dd = int(dte)
    if 0 <= dd <= 5:
        return "0_5"
    if 6 <= dd <= 10:
        return "6_10"
    if 20 <= dd <= 40:
        return "20_40"
    return None


def _bucket_label(k: str) -> str:
    return {"0_5": "0–5 DTE", "6_10": "6–10 DTE", "20_40": "20–40 DTE"}.get(k, k)


def _exp_decay_weight(*, dte: int, half_life_dte: float) -> float:
    hl = float(half_life_dte)
    if hl <= 1e-9:
        return 1.0
    lam = math.log(2.0) / hl
    return math.exp(-lam * float(max(0, int(dte))))


def _weighted_sum_rows(
    *,
    rows: List[List[Optional[float]]],
    weights: List[float],
) -> List[Optional[float]]:
    """
    Weighted sum across expiry rows, skipping None cells.
    """
    if not rows:
        return []
    m = len(rows[0])
    out: List[Optional[float]] = [None] * m
    for j in range(m):
        num = 0.0
        den = 0.0
        for i in range(len(rows)):
            w = float(weights[i])
            if w <= 0:
                continue
            v = rows[i][j] if j < len(rows[i]) else None
            if v is None:
                continue
            fv = float(v)
            if not math.isfinite(fv):
                continue
            num += w * fv
            den += w
        out[j] = (num / den) if den > 1e-12 else None
    return out


def _find_accel_boundary_from_spot(
    *,
    strikes: List[float],
    vals: List[Optional[float]],
    spot: float,
    side: str,
    adjacent_n: int,
) -> Optional[float]:
    """
    Find the first boundary from spot outward where sign flips from positive->negative
    and remains negative for >= adjacent_n strikes beyond the boundary.

    side: "down" (scan decreasing strikes) or "up" (scan increasing strikes)
    Returns an approximate boundary strike (midpoint between the two strikes around the flip).
    """
    if not strikes or not vals or len(strikes) != len(vals):
        return None
    s0 = float(spot)
    n = max(1, int(adjacent_n))

    best_i = None
    best_d = None
    for i, k in enumerate(strikes):
        v = vals[i]
        if v is None:
            continue
        if not math.isfinite(float(v)):
            continue
        d = abs(float(k) - s0)
        if best_i is None or best_d is None or d < best_d:
            best_i = i
            best_d = d
    if best_i is None:
        return None

    def _sign(v: float) -> int:
        return 1 if v >= 0 else -1

    v0 = vals[best_i]
    if v0 is None:
        return None

    if side == "down":
        for i in range(best_i - 1, -1, -1):
            vi = vals[i]
            vup = vals[i + 1]
            if vi is None or vup is None:
                continue
            if not (math.isfinite(float(vi)) and math.isfinite(float(vup))):
                continue
            if _sign(float(vup)) == 1 and _sign(float(vi)) == -1:
                ok = True
                for j in range(i, max(-1, i - n), -1):
                    vj = vals[j]
                    if vj is None or (not math.isfinite(float(vj))) or _sign(float(vj)) != -1:
                        ok = False
                        break
                if ok:
                    return 0.5 * (float(strikes[i]) + float(strikes[i + 1]))
        return None

    if side == "up":
        for i in range(best_i, len(strikes) - 1):
            vi = vals[i]
            vdn = vals[i + 1]
            if vi is None or vdn is None:
                continue
            if not (math.isfinite(float(vi)) and math.isfinite(float(vdn))):
                continue
            if _sign(float(vi)) == 1 and _sign(float(vdn)) == -1:
                ok = True
                for j in range(i + 1, min(len(strikes), i + 1 + n)):
                    vj = vals[j]
                    if vj is None or (not math.isfinite(float(vj))) or _sign(float(vj)) != -1:
                        ok = False
                        break
                if ok:
                    return 0.5 * (float(strikes[i]) + float(strikes[i + 1]))
        return None

    return None


def _classify_stability(
    *,
    strikes: List[float],
    vals0_5: List[Optional[float]],
    spot: float,
    em_pts: Optional[float],
    downside_em: Optional[float],
    upside_em: Optional[float],
    fragile_band_em: float = 0.75,
    asym_diff_em: float = 0.5,
) -> Dict[str, Any]:
    """
    Stability label priority (explicit):
    1) Fragile if negative gamma exists within ±0.75 EM of spot
    2) Asymmetric if |distance_up_EM - distance_down_EM| > 0.5 EM
    3) Stable otherwise
    """
    reasons: List[str] = []
    s0 = float(spot)

    if em_pts is not None and float(em_pts) > 1e-9:
        band = float(fragile_band_em) * float(em_pts)
        lo = s0 - band
        hi = s0 + band
        neg_found = False
        for k, v in zip(strikes, vals0_5):
            if v is None:
                continue
            if float(k) < lo or float(k) > hi:
                continue
            if float(v) < 0:
                neg_found = True
                break
        if neg_found:
            reasons.append(f"Fragile: negative gamma detected within ±{fragile_band_em:.2f} EM of spot")
            return {"label": "Fragile", "reasons": reasons}

    if downside_em is None or upside_em is None:
        reasons.append("Asymmetric: one-sided boundary missing (insufficient data for both sides)")
        return {"label": "Asymmetric", "reasons": reasons}
    if abs(float(upside_em) - float(downside_em)) > float(asym_diff_em):
        reasons.append(f"Asymmetric: upside vs downside boundary distance differs by > {asym_diff_em:.2f} EM")
        return {"label": "Asymmetric", "reasons": reasons}

    reasons.append("Stable: no negative gamma near spot and boundaries are balanced")
    return {"label": "Stable", "reasons": reasons}
