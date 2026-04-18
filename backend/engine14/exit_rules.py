"""Engine 14 — exit-rule optimizer.

Given a set of analogue `AnaloguePath`s, find the rule configuration that
would have maximized expected P&L across the historical distribution.

Phase E1 expands the rule vocabulary beyond flat (pt, sl):

* ``per_dte_profit_target`` — scale the profit target by DTE remaining
  so early-week wins lock in sooner (credit decay is concentrated at
  the end of the cycle).
* ``trail_stop_pct`` — trailing stop expressed as percent of the
  path-maximum realized gain (giveback cap).
* ``time_stop_dte`` — hard time stop: close at the end of day when
  ``dte_remaining == time_stop_dte``.

To keep the grid tractable we cap the search at ~50 cells by sampling
small axes for the optional features and folding them in only when the
operator opts in (see ``extended_grid=True``).

Output shape::

    {
      "recommendedProfitTarget": 45,
      "recommendedStopLoss": 175,
      "recommendedPerDtePt": null | {"scale": 1.2, "base": 50, "slope": 5},
      "recommendedTrailStopPct": null | 40.0,
      "recommendedTimeStopDte": null | 1,
      "deltaFromDefault": {"winRatePct": +2.1, "avgPnlPct": +4.3},
      "grid": [...],
      "gridSize": 48,
      "extendedGrid": true,
    }

Philosophy
----------
Only recommend changes that materially improve *both* win-rate and
average P&L vs the user's defaults. If the grid's best cell doesn't
clear that bar, we return the default rule and a zero delta — no
spurious tuning noise.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple


def _per_dte_target(
    *,
    dte_remaining: int,
    base_pt: float,
    slope_per_dte: float,
) -> float:
    """Linearly scale the profit target by DTE remaining.

    Intuition: with 4 DTE left we want to bank ~``base_pt``; as DTE
    shrinks we accept a tighter target (``base_pt - slope*DTE_used``).
    We clamp to [10%, 95%] to avoid degenerate targets.

    ``dte_remaining`` is the option's own time-to-expiry at that day —
    so ``slope_per_dte > 0`` *raises* the target when lots of DTE
    remains (hold out for more) and lowers it into expiry. That matches
    how MM-style desks often think about IC theta realization.
    """
    target = float(base_pt) + float(slope_per_dte) * float(dte_remaining)
    return max(10.0, min(95.0, target))


def _replay_with_rule(
    path,
    *,
    profit_target_pct: float,
    stop_loss_pct: float,
    per_dte_slope: Optional[float] = None,
    trail_stop_pct: Optional[float] = None,
    time_stop_dte: Optional[int] = None,
) -> float:
    """Re-derive the exit P&L for a single path under a composite rule.

    Rule evaluation order on each day (first trigger wins):
      1. Per-DTE profit target (falls back to flat ``profit_target_pct``
         when ``per_dte_slope`` is None).
      2. Trailing stop: giveback from path-max exceeds trail_stop_pct
         (in PNL-points, same units as pnl_pct).
      3. Flat stop loss.
      4. Time stop: if today's DTE == time_stop_dte, close at close.
    """
    if not path.daily_pnl_pct:
        return 0.0

    max_seen = -1e18
    for dte_r, pnl in path.daily_pnl_pct:
        pnl = float(pnl)
        max_seen = max(max_seen, pnl)

        # 1) profit target (per-DTE-aware when slope provided)
        pt = float(profit_target_pct)
        if per_dte_slope is not None:
            pt = _per_dte_target(dte_remaining=int(dte_r),
                                 base_pt=float(profit_target_pct),
                                 slope_per_dte=float(per_dte_slope))
        if pnl >= pt:
            return pnl

        # 2) trailing stop (only after we've seen some run-up)
        if trail_stop_pct is not None and max_seen > 0.0:
            giveback = max_seen - pnl
            if giveback >= float(trail_stop_pct):
                return pnl

        # 3) flat stop loss
        if pnl <= -float(stop_loss_pct):
            return pnl

        # 4) time stop — close at today's close when DTE matches
        if time_stop_dte is not None and int(dte_r) <= int(time_stop_dte):
            return pnl

    return float(path.daily_pnl_pct[-1][1])


def _score_rule(
    paths: List,
    *,
    profit_target_pct: float,
    stop_loss_pct: float,
    per_dte_slope: Optional[float] = None,
    trail_stop_pct: Optional[float] = None,
    time_stop_dte: Optional[int] = None,
) -> Dict[str, float]:
    pnls = [
        _replay_with_rule(
            p,
            profit_target_pct=profit_target_pct,
            stop_loss_pct=stop_loss_pct,
            per_dte_slope=per_dte_slope,
            trail_stop_pct=trail_stop_pct,
            time_stop_dte=time_stop_dte,
        )
        for p in paths
    ]
    if not pnls:
        return {"avg": 0.0, "winRate": 0.0, "median": 0.0}
    wins = sum(1 for x in pnls if x > 0)
    return {
        "avg": float(statistics.mean(pnls)),
        "winRate": 100.0 * wins / len(pnls),
        "median": float(statistics.median(pnls)),
    }


def _build_grid(
    *,
    extended_grid: bool,
    max_cells: int = 50,
) -> List[Tuple[float, float, Optional[float], Optional[float], Optional[int]]]:
    """Construct the (pt, sl, slope, trail, timeStop) grid under a cell cap.

    Base grid = 3×3 = 9 cells. When ``extended_grid=True`` we multiplex
    by small axes for slope / trail / time-stop, then truncate the
    cartesian product to ``max_cells`` by thinning the optional axes
    (they are the least informative; we keep pt/sl resolution).
    """
    pt_grid: Sequence[float] = (35.0, 50.0, 65.0)
    sl_grid: Sequence[float] = (150.0, 200.0, 275.0)
    slope_grid: Sequence[Optional[float]] = (None,)
    trail_grid:  Sequence[Optional[float]] = (None,)
    time_grid:   Sequence[Optional[int]]   = (None,)

    if extended_grid:
        slope_grid = (None, 3.0, 6.0)           # +3%/DTE and +6%/DTE
        trail_grid = (None, 30.0, 60.0)         # 30% and 60% giveback
        time_grid  = (None, 1, 2)                # close at 2 or 1 DTE remaining

    cells: List[Tuple[float, float, Optional[float], Optional[float], Optional[int]]] = []
    for pt in pt_grid:
        for sl in sl_grid:
            for sp in slope_grid:
                for tr in trail_grid:
                    for ts in time_grid:
                        cells.append((float(pt), float(sl), sp, tr, ts))

    # Thin optional axes (keep all base rows, drop duplicates deterministically).
    if len(cells) > int(max_cells):
        # Preserve the "None × None × None" baseline for each (pt, sl) first.
        baseline = [c for c in cells if c[2] is None and c[3] is None and c[4] is None]
        enriched = [c for c in cells if c not in baseline]
        # Stride through enriched to hit the cap.
        budget = max(0, int(max_cells) - len(baseline))
        if budget <= 0:
            cells = baseline
        else:
            step = max(1, len(enriched) // budget)
            cells = baseline + enriched[::step][:budget]
    return cells


def optimize_exit_rules(
    *,
    paths: List,
    default_profit_target_pct: float,
    default_stop_loss_pct: float,
    min_improvement_pp: float = 1.0,
    extended_grid: bool = True,
    max_grid_cells: int = 50,
) -> Dict[str, Any]:
    """Search a capped rule grid; return the cell that beats the default."""
    if not paths:
        return {
            "recommendedProfitTarget": float(default_profit_target_pct),
            "recommendedStopLoss": float(default_stop_loss_pct),
            "recommendedPerDtePt": None,
            "recommendedTrailStopPct": None,
            "recommendedTimeStopDte": None,
            "deltaFromDefault": {"winRatePct": 0.0, "avgPnlPct": 0.0},
            "grid": [],
            "gridSize": 0,
            "extendedGrid": bool(extended_grid),
        }

    grid_cells = _build_grid(extended_grid=extended_grid, max_cells=int(max_grid_cells))
    base = _score_rule(paths,
                       profit_target_pct=default_profit_target_pct,
                       stop_loss_pct=default_stop_loss_pct)

    grid_out: List[Dict[str, Any]] = []
    best = None
    for (pt, sl, sp, tr, ts) in grid_cells:
        sc = _score_rule(
            paths,
            profit_target_pct=pt,
            stop_loss_pct=sl,
            per_dte_slope=sp,
            trail_stop_pct=tr,
            time_stop_dte=ts,
        )
        cell = {
            "profitTarget": float(pt),
            "stopLoss": float(sl),
            "perDteSlope": (None if sp is None else float(sp)),
            "trailStopPct": (None if tr is None else float(tr)),
            "timeStopDte": (None if ts is None else int(ts)),
            "avgPnlPct": round(sc["avg"], 1),
            "winRatePct": round(sc["winRate"], 1),
        }
        grid_out.append(cell)
        if best is None or sc["avg"] > best["score"]["avg"]:
            best = {"pt": pt, "sl": sl, "sp": sp, "tr": tr, "ts": ts, "score": sc}

    assert best is not None  # grid_out is non-empty because paths is non-empty.

    delta_avg = best["score"]["avg"] - base["avg"]
    delta_wr = best["score"]["winRate"] - base["winRate"]

    if delta_avg >= float(min_improvement_pp) and delta_wr >= float(min_improvement_pp):
        rec_pt = float(best["pt"])
        rec_sl = float(best["sl"])
        rec_slope = best["sp"]
        rec_trail = best["tr"]
        rec_time  = best["ts"]
    else:
        rec_pt = float(default_profit_target_pct)
        rec_sl = float(default_stop_loss_pct)
        rec_slope = rec_trail = rec_time = None
        delta_avg = 0.0
        delta_wr = 0.0

    per_dte_payload = None
    if rec_slope is not None:
        per_dte_payload = {
            "base": float(rec_pt),
            "slopePerDte": float(rec_slope),
            "example4dte": round(_per_dte_target(dte_remaining=4, base_pt=rec_pt, slope_per_dte=float(rec_slope)), 1),
            "example1dte": round(_per_dte_target(dte_remaining=1, base_pt=rec_pt, slope_per_dte=float(rec_slope)), 1),
        }

    return {
        "recommendedProfitTarget": rec_pt,
        "recommendedStopLoss": rec_sl,
        "recommendedPerDtePt": per_dte_payload,
        "recommendedTrailStopPct": (None if rec_trail is None else float(rec_trail)),
        "recommendedTimeStopDte": (None if rec_time is None else int(rec_time)),
        "deltaFromDefault": {
            "winRatePct": round(float(delta_wr), 1),
            "avgPnlPct": round(float(delta_avg), 1),
        },
        "grid": grid_out,
        "gridSize": int(len(grid_out)),
        "extendedGrid": bool(extended_grid),
    }
