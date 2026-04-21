"""MAE (Max Adverse Excursion) proxy for Engine 1 Wing Console.

The desk's "White Knuckle" signal: given a wing placement, how close did
the underlying come during the hold window to blowing through a short
strike? We don't have intrabar data for all historical earnings events,
so we compute a **daily-OHLC proxy** — the worst ``max(|high - entry|,
|entry - low|)`` across the 1-2 trading days of the hold.

Per-event MAE is expressed as a % move, which the wing console then
scales relative to each candidate placement's short-strike distance to
produce an "MAE vs wing" ratio. Aggregated across the event pool we
report percentiles (p50 / p90 / p95) — the p95 is the White-Knuckle
anchor: if p95 of MAE-vs-short-strike exceeds 1.0, the desk historically
would have been forced into emergency de-risking.

IMPORTANT:

- This is a **proxy** — daily OHLC underestimates intraday excursions
  on reversal days (big swing down and back). Documented limitation.
- Hold window is configurable. Defaults to the earnings-day bar plus
  the next trading day (covers AMC cases where most premium decays
  on the gap-open + next session).
- When the OHLC series is missing ``high``/``low`` (the legacy
  ``DailyBar`` in ``earnings_logic.py`` carries only close + open),
  MAE falls back to the daily range estimated from
  ``|open - close|``, which is conservative (under-estimate).
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger("engine1.mae_proxy")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class EventOHLC:
    """Minimal OHLC snapshot for one hold-window day."""

    date:  str = ""
    open:  Optional[float] = None
    high:  Optional[float] = None
    low:   Optional[float] = None
    close: Optional[float] = None


@dataclass
class EventMAE:
    """Per-event MAE reading."""

    earn_date:         str = ""
    timing:            str = ""
    entry_close:       Optional[float] = None   # "PC" in hold-risk vocabulary
    hold_excursion:    Optional[float] = None   # abs worst %-move from entry_close
    hold_direction:    str = ""                  # "up" | "down" | "flat"
    source:            str = "daily_ohlc_proxy"  # or "open_close_fallback"
    note:              str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class MAEDistribution:
    """Aggregated MAE distribution across the event pool."""

    n:          int = 0
    p50:        float = 0.0        # median MAE as absolute pct move
    p75:        float = 0.0
    p90:        float = 0.0
    p95:        float = 0.0
    max:        float = 0.0
    events:     List[EventMAE] = field(default_factory=list)
    source:     str = "daily_ohlc_proxy"
    notes:      List[str] = field(default_factory=list)
    hold_days:  int = 2

    def to_dict(self) -> dict:
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
    """Linear-interpolation percentile (pct in [0, 100])."""
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


def _compute_single_event_mae(
    entry_close: float,
    bars: List[EventOHLC],
) -> Tuple[Optional[float], str, str]:
    """Worst drawdown or rally in % vs entry_close across the hold-day bars.

    Returns ``(excursion_pct_abs, direction, source)``. ``excursion_pct_abs``
    is always >= 0; ``direction`` is ``up``/``down``/``flat``; ``source``
    is ``daily_ohlc_proxy`` when we had true high/low, ``open_close_fallback``
    when we had to estimate from open/close.
    """
    if not bars or not (entry_close and math.isfinite(entry_close) and entry_close > 0):
        return None, "", "none"

    worst_up = 0.0   # largest upward move above entry
    worst_dn = 0.0   # largest downward move below entry
    source = "daily_ohlc_proxy"

    for bar in bars:
        highs: List[float] = []
        lows:  List[float] = []
        if bar.high is not None and bar.low is not None:
            highs.append(float(bar.high))
            lows.append(float(bar.low))
        else:
            # Fallback: use the bar's open + close as a conservative proxy.
            source = "open_close_fallback"
            if bar.open is not None:
                highs.append(float(bar.open))
                lows.append(float(bar.open))
            if bar.close is not None:
                highs.append(float(bar.close))
                lows.append(float(bar.close))
        for h in highs:
            if h > entry_close:
                worst_up = max(worst_up, (h - entry_close) / entry_close)
        for lo in lows:
            if lo < entry_close:
                worst_dn = max(worst_dn, (entry_close - lo) / entry_close)

    if worst_up == 0.0 and worst_dn == 0.0:
        return 0.0, "flat", source

    if worst_up >= worst_dn:
        return worst_up * 100.0, "up", source
    return worst_dn * 100.0, "down", source


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def compute_mae_distribution(
    *,
    hold_risk_events: List[Any],
    dailies_cache: Dict[str, Any],
    hold_days: int = 2,
) -> MAEDistribution:
    """Compute the MAE distribution across a pool of historical earnings events.

    ``hold_risk_events`` is the list of :class:`HoldRiskEvent` produced in
    ``earnings_logic._build_hold_risk_events``. We use:

    - ``entry_close`` = ``prior_close`` (PC anchor; close before earnings
      were known)
    - hold window = up to ``hold_days`` trading days starting with the
      earnings-day bar

    ``dailies_cache`` is the bulk-fetched ``Dict[date_str, DailyBar]``
    the scanner already built. We **also** accept
    ``Dict[date_str, price_service.DailyBar]`` (which carries ``high``/
    ``low``); the helper sniffs the attribute set and builds
    :class:`EventOHLC` accordingly.
    """
    if not hold_risk_events:
        return MAEDistribution(n=0, hold_days=hold_days, notes=["no_events"])

    events: List[EventMAE] = []
    per_event_pcts: List[float] = []
    fallback_count = 0

    for hre in hold_risk_events:
        earn_date = getattr(hre, "earn_date", "")
        timing = getattr(hre, "timing", "")
        entry_close = _as_float(getattr(hre, "prior_close", None))
        if entry_close is None or entry_close <= 0:
            continue

        try:
            earn_dt = dt.date.fromisoformat(earn_date[:10])
        except Exception:
            continue

        # Choose the starting date for the hold window based on timing.
        # AMC → hold starts on the next trading day (earn_date + 1).
        # BMO → hold starts on the earnings day itself.
        if timing == "AMC":
            window_start = earn_dt + dt.timedelta(days=1)
        else:
            window_start = earn_dt

        bars = _collect_hold_bars(
            dailies_cache=dailies_cache,
            start=window_start,
            max_bars=hold_days,
        )
        if not bars:
            continue

        excursion, direction, source = _compute_single_event_mae(entry_close, bars)
        if excursion is None:
            continue

        if source == "open_close_fallback":
            fallback_count += 1

        events.append(EventMAE(
            earn_date=earn_date,
            timing=timing,
            entry_close=entry_close,
            hold_excursion=round(excursion, 3),
            hold_direction=direction,
            source=source,
        ))
        per_event_pcts.append(excursion)

    n = len(per_event_pcts)
    if n == 0:
        return MAEDistribution(
            n=0, hold_days=hold_days,
            notes=["mae_pool_empty: no events resolved OHLC data"],
        )

    dist = MAEDistribution(
        n=n,
        p50=round(_percentile(per_event_pcts, 50), 3),
        p75=round(_percentile(per_event_pcts, 75), 3),
        p90=round(_percentile(per_event_pcts, 90), 3),
        p95=round(_percentile(per_event_pcts, 95), 3),
        max=round(max(per_event_pcts), 3),
        events=events,
        hold_days=hold_days,
    )

    # Source chip — true MAE when >=80% of events had real high/low.
    if fallback_count == 0:
        dist.source = "daily_ohlc_proxy"
    elif fallback_count >= n * 0.5:
        dist.source = "open_close_fallback"
        dist.notes.append(
            f"{fallback_count}/{n} events used open/close fallback "
            "(underestimates true intraday excursion)"
        )
    else:
        dist.source = "mixed_proxy"
        dist.notes.append(f"{fallback_count}/{n} events fell back to open/close")

    return dist


def _collect_hold_bars(
    *,
    dailies_cache: Dict[str, Any],
    start: dt.date,
    max_bars: int,
) -> List[EventOHLC]:
    """Walk forward from ``start`` through the cache collecting up to
    ``max_bars`` trading-day bars. Skips weekends/holidays silently.
    """
    out: List[EventOHLC] = []
    cursor = start
    for _ in range(max_bars * 5):  # max 10 calendar-day lookahead = 2 biz weeks
        if len(out) >= max_bars:
            break
        key = cursor.isoformat()
        bar = dailies_cache.get(key)
        if bar is not None:
            out.append(_to_event_ohlc(key, bar))
        cursor += dt.timedelta(days=1)
    return out


def _to_event_ohlc(date_str: str, bar: Any) -> EventOHLC:
    """Convert a ``DailyBar`` from either ``earnings_logic`` (open/clsPx only)
    or ``price_service`` (open/high/low/close) into an :class:`EventOHLC`.
    """
    # price_service.DailyBar has high/low as direct attributes
    high = _as_float(getattr(bar, "high", None))
    low  = _as_float(getattr(bar, "low",  None))
    # earnings_logic.DailyBar uses clsPx not close
    close = _as_float(getattr(bar, "close", None)) or _as_float(getattr(bar, "clsPx", None))
    open_ = _as_float(getattr(bar, "open", None))

    return EventOHLC(date=date_str, open=open_, high=high, low=low, close=close)


# ---------------------------------------------------------------------------
# Wing-placement adapter
# ---------------------------------------------------------------------------


def mae_percentile_to_credit_pct(
    *,
    mae_pct_move:       float,
    em_multiple:        float,
    implied_move_pct:   float,
    wing_width_pts:     float,
    underlying_spot:    float,
) -> float:
    """Convert an MAE percentile (in % price move) into a % of entry credit
    at a given wing placement.

    Model: a short-IC's position value at an intraday print equal to
    ``entry_close * (1 + mae/100)`` is approximately:

        loss(mae) =
            max(0, (mae_pct_move - em_multiple * implied_move_pct) *
                   underlying_spot * 0.01)

    In dollar terms, this is the distance past the short strike in the
    MAE direction (assumed put OR call — we use whichever is closer for
    a symmetric structure). Wing-width caps this at the wing's max-loss
    so we divide by the credit for a % ratio.

    Credit itself is not known at inference time; the scoring engine
    estimates it from historical credit richness elsewhere. For ratio
    math, we treat ``wing_width_pts`` as the loss cap; if MAE exceeds
    ``(em_multiple * implied_move)``, the loss scales linearly until it
    saturates at the wing width. Output is ``loss_pts / wing_width_pts``
    clamped to [0, 1.5] — above 1.0 means "historically hit max loss
    at this placement".
    """
    if (
        underlying_spot <= 0 or wing_width_pts <= 0 or
        implied_move_pct <= 0 or em_multiple <= 0 or
        not math.isfinite(mae_pct_move)
    ):
        return 0.0

    # Distance past the short strike in points.
    intrinsic_pct_past_short = max(0.0, mae_pct_move - em_multiple * implied_move_pct)
    pts_past_short = intrinsic_pct_past_short * 0.01 * underlying_spot

    # Clip at wing width + small tolerance (1.5x so the desk can see "way past").
    ratio = pts_past_short / wing_width_pts
    return float(max(0.0, min(1.5, ratio)))
