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


def _infer_strike_step(strikes: List[float]) -> float:
    """
    Infer strike increment from sorted strikes.
    Returns a best-effort positive step.
    """
    xs = sorted(float(x) for x in strikes if x is not None and math.isfinite(float(x)))
    if len(xs) < 2:
        return 1.0
    diffs = []
    prev = xs[0]
    for x in xs[1:]:
        d = float(x) - float(prev)
        if d > 0 and math.isfinite(d):
            diffs.append(d)
        prev = x
    if not diffs:
        return 1.0
    try:
        step = float(statistics.median(diffs))
    except Exception:
        step = diffs[0]
    # snap to a sane value
    if not math.isfinite(step) or step <= 0:
        return 1.0
    return step


def _weighting_mode(rows: List[dict]) -> Tuple[str, List[str]]:
    warnings: List[str] = []
    has_call_oi = any(_to_float(r.get("callOpenInterest")) is not None for r in rows)
    has_put_oi = any(_to_float(r.get("putOpenInterest")) is not None for r in rows)
    has_call_vol = any(_to_float(r.get("callVolume")) is not None for r in rows)
    has_put_vol = any(_to_float(r.get("putVolume")) is not None for r in rows)

    if has_call_oi or has_put_oi:
        mode = "oi"
        if not (has_call_oi and has_put_oi):
            warnings.append("Open interest missing on one side; using 0 for missing side.")
        return mode, warnings

    if has_call_vol or has_put_vol:
        return "volume", ["Open interest unavailable; using volume proxy (noisy)."]

    return "unknown", ["Open interest and volume unavailable; OI clusters cannot be computed."]


def _cluster_for_side(
    rows: List[dict],
    *,
    side: str,
    strike_step: float,
    cluster_steps: int,
    top_n: int,
    weighting_mode: str,
) -> List[Dict[str, Any]]:
    """
    side: 'C' or 'P'
    Greedy pick peaks, merge nearby strikes into a cluster window.
    """
    # Build (strike, w) points
    pts: List[Tuple[float, float]] = []
    for r in rows:
        strike = _to_float(r.get("strike"))
        if strike is None:
            continue
        if weighting_mode == "oi":
            w = _to_float(r.get("callOpenInterest" if side == "C" else "putOpenInterest")) or 0.0
        elif weighting_mode == "volume":
            w = _to_float(r.get("callVolume" if side == "C" else "putVolume")) or 0.0
        else:
            w = 0.0
        w = max(0.0, float(w))
        pts.append((float(strike), float(w)))

    if not pts:
        return []

    # Sort by descending weight (peaks)
    pts_sorted = sorted(pts, key=lambda x: x[1], reverse=True)
    selected_ranges: List[Tuple[float, float]] = []
    clusters: List[Dict[str, Any]] = []

    window = float(max(1, int(cluster_steps))) * float(strike_step)

    def overlaps(a_lo: float, a_hi: float, b_lo: float, b_hi: float) -> bool:
        return not (a_hi < b_lo or b_hi < a_lo)

    for strike_peak, w_peak in pts_sorted:
        if w_peak <= 0:
            break
        lo = float(strike_peak) - window
        hi = float(strike_peak) + window
        if any(overlaps(lo, hi, rlo, rhi) for (rlo, rhi) in selected_ranges):
            continue

        # Include all points in the window
        in_win = [(s, w) for (s, w) in pts if lo <= s <= hi and w > 0]
        if not in_win:
            continue
        in_win.sort(key=lambda x: x[0])

        strikes = [s for (s, _) in in_win]
        weights = [w for (_, w) in in_win]
        total = float(sum(weights))
        if total <= 0:
            continue
        center = float(sum(s * w for (s, w) in in_win) / total)

        # max strike within cluster
        ms, mw = max(in_win, key=lambda x: x[1])
        clusters.append(
            {
                "side": side,
                "minStrike": float(min(strikes)),
                "maxStrike": float(max(strikes)),
                "centerStrike": float(center),
                "totalOI": float(total),
                "maxStrike": float(ms),
                "maxOI": float(mw),
                "nStrikes": int(len(in_win)),
            }
        )
        selected_ranges.append((float(lo), float(hi)))
        if len(clusters) >= max(0, int(top_n)):
            break

    # Sort clusters by total desc
    clusters.sort(key=lambda c: float(c.get("totalOI") or 0.0), reverse=True)
    return clusters


def compute_open_interest_clusters(
    strikes_rows: List[dict],
    *,
    expiry: str | None = None,
    band_pct: float = 0.05,
    top_n: int = 5,
    cluster_steps: int = 2,
) -> Dict[str, Any]:
    """
    Dominant OI clusters (walls) from strike-level chain rows (live, informational).

    - Filters strikes to ±band_pct around spot
    - Determines weightingMode: OI preferred, else volume proxy
    - Greedy picks top peaks and groups nearby strikes into clusters
    """
    rows = [r for r in (strikes_rows or []) if isinstance(r, dict)]
    warnings: List[str] = []

    spot = _pick_spot(rows)
    if spot is None or spot <= 0:
        return {
            "spot": None,
            "expiry": str(expiry)[:10] if expiry else None,
            "bandPct": float(band_pct),
            "weightingMode": "unknown",
            "warnings": ["Missing spot/stock price in live strikes payload."],
            "callClusters": [],
            "putClusters": [],
            "callWall": None,
            "putWall": None,
        }

    lo = float(spot) * (1.0 - float(band_pct))
    hi = float(spot) * (1.0 + float(band_pct))
    in_band = []
    strikes = []
    for r in rows:
        s = _to_float(r.get("strike"))
        if s is None:
            continue
        if lo <= float(s) <= hi:
            in_band.append(r)
            strikes.append(float(s))

    mode, mode_warn = _weighting_mode(in_band)
    warnings.extend(mode_warn)
    if mode not in ("oi", "volume"):
        return {
            "spot": float(spot),
            "expiry": str(expiry)[:10] if expiry else None,
            "bandPct": float(band_pct),
            "weightingMode": str(mode),
            "warnings": warnings,
            "callClusters": [],
            "putClusters": [],
            "callWall": None,
            "putWall": None,
        }

    if not in_band:
        return {
            "spot": float(spot),
            "expiry": str(expiry)[:10] if expiry else None,
            "bandPct": float(band_pct),
            "weightingMode": str(mode),
            "warnings": [*warnings, "No strikes in spot band; OI clusters empty."],
            "callClusters": [],
            "putClusters": [],
            "callWall": None,
            "putWall": None,
        }

    step = _infer_strike_step(strikes)
    if step <= 0:
        step = 1.0

    call_clusters = _cluster_for_side(
        in_band,
        side="C",
        strike_step=step,
        cluster_steps=int(cluster_steps),
        top_n=int(top_n),
        weighting_mode=mode,
    )
    put_clusters = _cluster_for_side(
        in_band,
        side="P",
        strike_step=step,
        cluster_steps=int(cluster_steps),
        top_n=int(top_n),
        weighting_mode=mode,
    )

    call_wall = call_clusters[0] if call_clusters else None
    put_wall = put_clusters[0] if put_clusters else None

    return {
        "spot": float(spot),
        "expiry": str(expiry)[:10] if expiry else None,
        "bandPct": float(band_pct),
        "weightingMode": str(mode),
        "strikeStep": float(step),
        "clusterSteps": int(cluster_steps),
        "warnings": warnings,
        "callClusters": call_clusters,
        "putClusters": put_clusters,
        "callWall": call_wall,
        "putWall": put_wall,
    }


