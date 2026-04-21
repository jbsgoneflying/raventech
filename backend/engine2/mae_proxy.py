"""Intraweek MAE proxy for the SPX IC Command Deck.

A weekly iron condor's main failure mode is **not** "close at expiry
outside the shorts" — it's "spot touched a short strike midweek, the
desk panicked, and closed at the worst possible mark." The legacy E2
historical grid only counts breach at expiry close; this module gives
the Command Deck a per-week distribution of the worst intraweek
excursion so the composite score can penalise placements whose wings
historically got blown through on a Wednesday afternoon.

Math (deterministic, no assumptions about IV crush):

- For each historical weekly window ``(entry_date, expiry_date)``:
  ``mae_pct = max(|high - entry_close|, |entry_close - low|) / entry_close * 100``
  where ``high`` / ``low`` are the extreme prints across the hold-
  window daily bars.
- Aggregate per-event MAE across the pool into p50 / p75 / p90 / p95
  plus a ``source`` tag (``daily_ohlc`` / ``open_close_fallback`` /
  ``mixed``).

The wing-console scorer consumes :func:`mae_p95_vs_wing_ratio` to
convert the realised % move into a "fraction of wing width" that
penalty term expects.
"""
from __future__ import annotations

import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence

LOG = logging.getLogger("engine2.mae_proxy")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class WeeklyMAE:
    """Per-week MAE reading."""

    entry_date:  str = ""
    expiry_date: str = ""
    entry_close: Optional[float] = None
    mae_pct:     Optional[float] = None          # abs worst %-move vs entry close
    direction:   str = ""                         # "up" | "down" | "flat"
    source:      str = "daily_ohlc"               # or "open_close_fallback"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MAEDistribution:
    """Aggregated MAE distribution across the weekly pool."""

    n:       int = 0
    p50:     float = 0.0
    p75:     float = 0.0
    p90:     float = 0.0
    p95:     float = 0.0
    max:     float = 0.0
    source:  str = "daily_ohlc"
    notes:   List[str] = field(default_factory=list)
    events:  List[WeeklyMAE] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["events"] = [e.to_dict() for e in self.events]
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _percentile(values: List[float], pct: float) -> float:
    """Linear-interpolation percentile. ``pct`` in [0, 100]."""
    if not values:
        return 0.0
    if len(values) == 1:
        return float(values[0])
    xs = sorted(values)
    k = (pct / 100.0) * (len(xs) - 1)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(xs[lo])
    frac = k - lo
    return float(xs[lo] + (xs[hi] - xs[lo]) * frac)


# ---------------------------------------------------------------------------
# Per-week MAE compute
# ---------------------------------------------------------------------------


def _compute_week_mae(
    *,
    entry_close: float,
    bars: Sequence[Any],
) -> Optional[WeeklyMAE]:
    """Compute the worst intraweek excursion vs ``entry_close``.

    ``bars`` is an iterable of anything with ``.high`` / ``.low`` / ``.open``
    / ``.close`` attributes (the SPX IC engine's :class:`DailyOHLC` or the
    shared price-service ``DailyBar`` both qualify). When high/low are
    missing we fall back to the bar's own open/close as a conservative
    proxy and flag the source as ``open_close_fallback``.
    """
    if not (entry_close and math.isfinite(entry_close) and entry_close > 0):
        return None
    if not bars:
        return None

    worst_up = 0.0
    worst_dn = 0.0
    source = "daily_ohlc"
    had_any = False
    for b in bars:
        hi = _as_float(getattr(b, "high", None))
        lo = _as_float(getattr(b, "low",  None))
        o  = _as_float(getattr(b, "open", None))
        c  = _as_float(getattr(b, "close", None)) or _as_float(getattr(b, "clsPx", None))
        if hi is None or lo is None:
            # Fallback: use open + close extremes.
            candidates = [x for x in (o, c) if x is not None]
            if not candidates:
                continue
            hi = max(candidates)
            lo = min(candidates)
            source = "open_close_fallback"
        had_any = True
        if hi > entry_close:
            worst_up = max(worst_up, (hi - entry_close) / entry_close)
        if lo < entry_close:
            worst_dn = max(worst_dn, (entry_close - lo) / entry_close)

    if not had_any:
        return None

    if worst_up == 0.0 and worst_dn == 0.0:
        return WeeklyMAE(entry_close=entry_close, mae_pct=0.0, direction="flat", source=source)
    if worst_up >= worst_dn:
        return WeeklyMAE(entry_close=entry_close, mae_pct=worst_up * 100.0, direction="up", source=source)
    return WeeklyMAE(entry_close=entry_close, mae_pct=worst_dn * 100.0, direction="down", source=source)


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_mae_distribution(
    *,
    windows: Iterable[Dict[str, Any]],
    bars_by_date: Dict[str, Any],
) -> MAEDistribution:
    """Compute the intraweek MAE distribution across the weekly pool.

    ``windows`` is an iterable of dicts each carrying at minimum:

    - ``entry_date``   (YYYY-MM-DD)
    - ``expiry_date``  (YYYY-MM-DD)
    - ``entry_close``  (float)

    ``bars_by_date`` maps trade-date strings to OHLC bar objects (same
    shape the SPX IC engine already carries: ``DailyOHLC`` with
    ``trade_date`` / ``open`` / ``high`` / ``low`` / ``close``).

    For each window we collect all bars between ``entry_date`` (exclusive)
    and ``expiry_date`` (inclusive) and compute the worst excursion from
    ``entry_close``. The aggregate p50/p75/p90/p95 + max gives the desk
    the realised "white-knuckle" distribution for this entry-day /
    ticker combination.
    """
    per_week: List[WeeklyMAE] = []
    per_week_values: List[float] = []
    fallback_count = 0

    # Sort bar keys once so we can slice linearly per window.
    sorted_dates = sorted(bars_by_date.keys())

    for win in (windows or []):
        ed = str(win.get("entry_date") or win.get("entryDate") or "")[:10]
        xp = str(win.get("expiry_date") or win.get("expiryDate") or "")[:10]
        entry_close = _as_float(win.get("entry_close") or win.get("entryClose") or win.get("entryPx"))
        if not ed or not xp or entry_close is None:
            continue

        # Collect in-window bars: strictly after entry_date, up to + including expiry_date.
        hold_bars: List[Any] = []
        for d in sorted_dates:
            if d <= ed:
                continue
            if d > xp:
                break
            b = bars_by_date.get(d)
            if b is not None:
                hold_bars.append(b)
        if not hold_bars:
            continue

        reading = _compute_week_mae(entry_close=entry_close, bars=hold_bars)
        if reading is None or reading.mae_pct is None:
            continue

        reading.entry_date = ed
        reading.expiry_date = xp
        per_week.append(reading)
        per_week_values.append(float(reading.mae_pct))
        if reading.source == "open_close_fallback":
            fallback_count += 1

    n = len(per_week_values)
    if n == 0:
        return MAEDistribution(n=0, notes=["mae_pool_empty: no weekly windows resolved OHLC data"])

    dist = MAEDistribution(
        n=n,
        p50=round(_percentile(per_week_values, 50), 3),
        p75=round(_percentile(per_week_values, 75), 3),
        p90=round(_percentile(per_week_values, 90), 3),
        p95=round(_percentile(per_week_values, 95), 3),
        max=round(max(per_week_values), 3),
        events=per_week,
    )

    if fallback_count == 0:
        dist.source = "daily_ohlc"
    elif fallback_count >= n * 0.5:
        dist.source = "open_close_fallback"
        dist.notes.append(
            f"{fallback_count}/{n} weeks used open/close fallback; p95 under-estimates true intraweek MAE."
        )
    else:
        dist.source = "mixed"
        dist.notes.append(f"{fallback_count}/{n} weeks fell back to open/close for MAE.")
    return dist


# ---------------------------------------------------------------------------
# Placement-time adapter
# ---------------------------------------------------------------------------


def mae_p95_vs_wing_ratio(
    *,
    mae_p95_pct:       float,
    em_multiple:       float,
    implied_move_pct:  float,
    wing_width_pts:    float,
    spot:              float,
) -> float:
    """Convert the historical p95 MAE into a "fraction of wing width"
    the scorer's penalty term uses.

    Same math as E1 v2's ``mae_percentile_to_credit_pct`` but adapted
    for SPX where the wing width is in points (not as a fraction of EM).

    Model: at the p95 intraweek print the IC's spot is
    ``entry_close ± mae_p95_pct%``. Loss vs the short strike is
    ``max(0, mae_p95_pct - em_multiple * implied_move_pct) * spot * 0.01``
    in points; fraction of wing width = loss_pts / wing_width_pts,
    clamped to [0, 1.5] so the desk can see "way past max" as a
    saturating penalty rather than a runaway score.
    """
    if (
        spot <= 0 or wing_width_pts <= 0 or
        implied_move_pct <= 0 or em_multiple <= 0 or
        not math.isfinite(mae_p95_pct)
    ):
        return 0.0

    pct_past_short = max(0.0, mae_p95_pct - em_multiple * implied_move_pct)
    pts_past_short = pct_past_short * 0.01 * spot
    ratio = pts_past_short / wing_width_pts
    return float(max(0.0, min(1.5, ratio)))
