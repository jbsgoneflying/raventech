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


def _score_to_grade(score100: float) -> str:
    s = float(score100)
    if s >= 80:
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

    br15 = _breach_rate_pct(realized_abs=realized_abs, em_pct=em, k=1.5)
    br20 = _breach_rate_pct(realized_abs=realized_abs, em_pct=em, k=2.0)

    richness = (em / med_move) if (em is not None and med_move is not None and med_move > 0) else None
    tail_buffer_15 = ((1.5 * em) / p90) if (em is not None and p90 is not None and p90 > 0) else None

    # Simple, explainable score (0..100). Can be tuned without breaking schema.
    score = 50.0
    if br15 is not None:
        score += _clamp((15.0 - float(br15)) * 1.4, -30.0, 30.0)
    if br20 is not None:
        score += _clamp((8.0 - float(br20)) * 1.6, -20.0, 20.0)
    if richness is not None:
        score += _clamp((float(richness) - 1.0) * 12.0, -15.0, 15.0)
    if tail_buffer_15 is not None:
        score += _clamp((float(tail_buffer_15) - 1.0) * 10.0, -10.0, 10.0)
    score = _clamp(score, 0.0, 100.0)

    return {
        "ticker": t,
        "asOfDate": str(((base.get("current") or {}) if isinstance(base.get("current"), dict) else {}).get("asOfDate") or today)[:10],
        "n": int(n),
        "years": int(years),
        "frontWeekEmPct": em,
        "medianGapPct": med_move,
        "p90GapPct": p90,
        "breachRatePct": {"k1_5": br15, "k2_0": br20},
        "richness": richness,
        "tailBuffer": {"k1_5": tail_buffer_15},
        "score100": round(float(score), 1),
        "grade": _score_to_grade(score),
        "notes": [
            "Rank is a lightweight pre-earnings screen for same-day entry and next-session exit.",
            "Uses Engine-1 earnings gap history (close→open) and current implied move when available.",
        ],
    }


