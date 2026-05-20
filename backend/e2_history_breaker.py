"""Engine 2 warn-only history-breaker scoring."""

from __future__ import annotations

from typing import Any, Dict, List


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def compute_e2_history_breaker_risk(payload: Dict[str, Any]) -> Dict[str, Any]:
    weeks = payload.get("weeks") if isinstance(payload.get("weeks"), list) else []
    current = payload.get("current") if isinstance(payload.get("current"), dict) else {}
    recommendation = payload.get("recommendation") if isinstance(payload.get("recommendation"), dict) else {}
    odds_like = payload.get("oddsLikeNow") if isinstance(payload.get("oddsLikeNow"), dict) else {}

    signals: Dict[str, Any] = {}
    drivers: List[str] = []
    score = 0.0

    # 1) Recency divergence: recent absolute move vs trailing baseline.
    abs_moves = []
    for row in weeks:
        mv = row.get("signedMovePct")
        if mv is None:
            continue
        abs_moves.append(abs(_to_float(mv)))
    recent = abs_moves[-12:] if abs_moves else []
    base = abs_moves[-52:] if abs_moves else []
    recent_avg = (sum(recent) / len(recent)) if recent else 0.0
    base_avg = (sum(base) / len(base)) if base else 0.0
    recency_ratio = (recent_avg / base_avg) if base_avg > 0 else 1.0
    signals["recencyMoveRatio"] = round(recency_ratio, 3)
    if recency_ratio >= 1.35:
        score += 24
        drivers.append("Recent realized moves are materially larger than baseline.")
    elif recency_ratio >= 1.15:
        score += 12
        drivers.append("Recent move intensity is running hotter than baseline.")

    # 2) Regime caution.
    regime_bucket = str((current.get("regime") or {}).get("bucket") or "").upper()
    signals["regimeBucket"] = regime_bucket or None
    if regime_bucket == "NO_TRADE":
        score += 28
        drivers.append("Current regime is NO_TRADE.")
    elif regime_bucket == "ELEVATED":
        score += 14
        drivers.append("Current regime is ELEVATED.")

    # 3) Recommendation caution.
    rec_label = str(recommendation.get("label") or recommendation.get("verdict") or "").lower()
    signals["recommendationLabel"] = rec_label or None
    if rec_label.startswith("avoid"):
        score += 22
        drivers.append("Recommendation currently flags Avoid.")

    # 4) Tail pressure from conditioned odds by width.
    by_width = odds_like.get("byWidth") if isinstance(odds_like.get("byWidth"), list) else []
    min_breach = None
    for row in by_width:
        bp = row.get("breachPct")
        if bp is None:
            continue
        bp_f = _to_float(bp)
        min_breach = bp_f if min_breach is None else min(min_breach, bp_f)
    signals["bestBreachPct"] = None if min_breach is None else round(min_breach, 2)
    if min_breach is not None and min_breach >= 25:
        score += 20
        drivers.append("Even best width shows elevated conditioned breach risk.")
    elif min_breach is not None and min_breach >= 18:
        score += 10
        drivers.append("Conditioned breach risk remains above comfort range.")

    score = max(0.0, min(100.0, score))
    if score >= 65:
        level = "high"
        gate = "NO_TRADE"
    elif score >= 35:
        level = "elevated"
        gate = "CAUTION"
    else:
        level = "low"
        gate = "OK"

    return {
        "score": round(score, 1),
        "level": level,
        "gate": gate,
        "confidence": round(min(0.95, 0.55 + (len(drivers) * 0.08)), 2),
        "overrideFavorableStats": bool(score >= 55),
        "drivers": drivers[:4],
        "signals": signals,
        "policy": "warn_only",
    }

