from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional

from backend.earnings_logic import compute_breach_stats
from backend.orats_client import OratsClient


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except Exception:
        return None


def _median(xs: List[float]) -> Optional[float]:
    ys = [float(x) for x in xs if x is not None]
    if not ys:
        return None
    ys.sort()
    m = len(ys) // 2
    return ys[m] if (len(ys) % 2 == 1) else (ys[m - 1] + ys[m]) / 2.0


def _pctl(xs: List[float], q: float) -> Optional[float]:
    ys = [float(x) for x in xs if x is not None]
    if not ys:
        return None
    ys.sort()
    qf = max(0.0, min(1.0, float(q)))
    idx = int(qf * (len(ys) - 1))
    return ys[idx]


def _breach_rate_pct(*, realized_abs: List[float], em_pct: Optional[float], k: float) -> Optional[float]:
    if em_pct is None or em_pct <= 0:
        return None
    if not realized_abs:
        return None
    thr = float(k) * float(em_pct)
    b = sum(1 for x in realized_abs if float(x) > thr)
    return (b / len(realized_abs)) * 100.0


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(float(lo), min(float(hi), float(x)))

def _clamp01(x: float) -> float:
    return _clamp(float(x), 0.0, 1.0)

def _safe01(x: Optional[float], lo: float, hi: float) -> Optional[float]:
    """
    Map x in [lo..hi] to [0..1] (clamped). Returns None if x is None.
    """
    if x is None:
        return None
    lo0 = float(lo)
    hi0 = float(hi)
    if hi0 <= lo0:
        return None
    return _clamp01((float(x) - lo0) / (hi0 - lo0))


def _score_to_grade(score100: float) -> str:
    s = float(score100)
    if s >= 85:
        return "A"
    if s >= 70:
        return "B"
    if s >= 55:
        return "C"
    if s >= 40:
        return "D"
    return "F"


def compute_condor_rank(
    client: OratsClient,
    *,
    ticker: str,
    n: int = 20,
    years: int = 5,
) -> Dict[str, Any]:
    """
    Lightweight Iron Condor Rank for same-day entry into earnings and next-session exit.

    Uses Engine-1 earnings gap history (close->open) already implemented in compute_breach_stats.
    This keeps methodology consistent with the rest of the app and leverages existing caching.
    """
    t = str(ticker or "").strip().upper()
    if not t:
        raise ValueError("Missing ticker")

    today = dt.date.today()
    base = compute_breach_stats(client=client, ticker=t, n=int(n), years=int(years), k=1.0, today=today)

    events = base.get("events") if isinstance(base.get("events"), list) else []
    usable = [e for e in events if isinstance(e, dict) and _to_float(e.get("realizedMovePct")) is not None]
    realized_abs = [abs(float(_to_float(e.get("realizedMovePct")) or 0.0)) for e in usable]

    # Prefer current front-week EM if available; else fallback to the most recent implied in history.
    em = _to_float(((base.get("current") or {}) if isinstance(base.get("current"), dict) else {}).get("impliedMovePct"))
    if em is None:
        for e in reversed(usable):
            em2 = _to_float(e.get("impliedMovePct"))
            if em2 is not None and em2 > 0:
                em = em2
                break

    med_move = _median(realized_abs)
    p90 = _pctl(realized_abs, 0.90)

    # Breach rates should be Engine-1 consistent: compare realized vs each event's implied move.
    usable_for_breach: list[tuple[float, float]] = []
    for e in usable:
        imp = _to_float(e.get("impliedMovePct"))
        rea = _to_float(e.get("realizedMovePct"))
        if imp is None or imp <= 0 or rea is None:
            continue
        usable_for_breach.append((abs(float(rea)), float(imp)))
    if usable_for_breach:
        br10 = (sum(1 for r, imp in usable_for_breach if r > 1.0 * imp) / len(usable_for_breach)) * 100.0
        br15 = (sum(1 for r, imp in usable_for_breach if r > 1.5 * imp) / len(usable_for_breach)) * 100.0
        br20 = (sum(1 for r, imp in usable_for_breach if r > 2.0 * imp) / len(usable_for_breach)) * 100.0
    else:
        br10 = None
        br15 = None
        br20 = None

    richness = (em / med_move) if (em is not None and med_move is not None and med_move > 0) else None
    tail_buffer_15 = ((1.5 * em) / p90) if (em is not None and p90 is not None and p90 > 0) else None

    # Score is a weighted blend of 0..1 components, then mapped to 0..100.
    # This avoids saturating at 100 whenever breach rates are near zero.
    #
    # Interpretation: higher is "more favorable for short IC around earnings" (risk-only screen).
    br15_01 = (None if br15 is None else _clamp01(1.0 - (float(br15) / 30.0)))  # 30%+ breaches => ~0
    br20_01 = (None if br20 is None else _clamp01(1.0 - (float(br20) / 20.0)))  # 20%+ breaches => ~0
    richness_01 = _safe01(richness, 0.70, 1.60)  # 0.7 (bad) .. 1.6 (good)
    tailbuf_01 = _safe01(tail_buffer_15, 0.80, 1.60)  # 0.8 (bad) .. 1.6 (good)

    comps: list[tuple[str, float, Optional[float]]] = [
        ("br15", 0.40, br15_01),
        ("br20", 0.25, br20_01),
        ("richness", 0.20, richness_01),
        ("tailBuffer15", 0.15, tailbuf_01),
    ]
    numer = 0.0
    denom = 0.0
    missing: list[str] = []
    for name, w, v in comps:
        if v is None:
            missing.append(name)
            continue
        numer += float(w) * float(v)
        denom += float(w)
    # Neutral fallback if too many components are missing.
    score01 = (numer / denom) if denom > 0 else 0.50

    # Sample-size dampener: fewer usable implied events => reduce confidence and shrink toward neutral.
    n_used = int(len(usable_for_breach))
    damp = _clamp01(n_used / 12.0)  # full strength by ~12 events
    score01 = 0.50 + (score01 - 0.50) * damp
    score = _clamp(100.0 * score01, 0.0, 100.0)

    return {
        "ticker": t,
        "asOfDate": str(((base.get("current") or {}) if isinstance(base.get("current"), dict) else {}).get("asOfDate") or today)[:10],
        "n": int(n),
        "years": int(years),
        "eventsUsed": int(len(usable_for_breach)),
        "frontWeekEmPct": em,
        "medianGapPct": med_move,
        "p90GapPct": p90,
        "breachRatePct": {"k1_0": br10, "k1_5": br15, "k2_0": br20},
        "richness": richness,
        "tailBuffer": {"k1_5": tail_buffer_15},
        "score100": round(float(score), 1),
        "grade": _score_to_grade(score),
        "notes": [
            "Rank is a lightweight pre-earnings screen for same-day entry and next-session exit.",
            "Uses Engine-1 earnings gap history (close→open) and current implied move when available.",
            ("Missing components: " + ", ".join(missing)) if missing else "All score components available.",
            "Scores are dampened toward neutral when few usable implied events are available.",
        ],
    }


