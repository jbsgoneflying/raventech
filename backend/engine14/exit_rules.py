"""Engine 14 — exit-rule optimizer.

Given a set of analogue `AnaloguePath`s, find the (profit-target,
stop-loss) pair that would have maximized expected P&L across the
historical distribution. The search is a small 2-D grid that re-classifies
each path's exit day & final P&L under alternative rules.

Output shape:
    {
      "recommendedProfitTarget": 45,
      "recommendedStopLoss": 175,
      "deltaFromDefault": {"winRatePct": +2.1, "avgPnlPct": +4.3}
    }

Philosophy
----------
We only recommend changes that materially improve *both* win-rate and
average P&L vs the user's defaults. If the grid's best cell doesn't
clear that bar, we return the default rule and a zero delta — no
spurious tuning noise.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, List


def _replay_with_rule(
    path,
    *,
    profit_target_pct: float,
    stop_loss_pct: float,
) -> float:
    """Re-derive the exit P&L for a single path under a (pt, sl) rule."""
    if not path.daily_pnl_pct:
        return 0.0
    for _, pnl in path.daily_pnl_pct:
        if pnl >= float(profit_target_pct):
            return float(pnl)
        if pnl <= -float(stop_loss_pct):
            return float(pnl)
    return float(path.daily_pnl_pct[-1][1])


def _score_rule(paths: List, profit_target_pct: float, stop_loss_pct: float) -> Dict[str, float]:
    pnls = [_replay_with_rule(p, profit_target_pct=profit_target_pct, stop_loss_pct=stop_loss_pct)
            for p in paths]
    if not pnls:
        return {"avg": 0.0, "winRate": 0.0, "median": 0.0}
    wins = sum(1 for x in pnls if x > 0)
    return {
        "avg": float(statistics.mean(pnls)),
        "winRate": 100.0 * wins / len(pnls),
        "median": float(statistics.median(pnls)),
    }


def optimize_exit_rules(
    *,
    paths: List,
    default_profit_target_pct: float,
    default_stop_loss_pct: float,
    min_improvement_pp: float = 1.0,
) -> Dict[str, Any]:
    """Search a small (pt, sl) grid; return the cell that beats the default."""
    if not paths:
        return {
            "recommendedProfitTarget": float(default_profit_target_pct),
            "recommendedStopLoss": float(default_stop_loss_pct),
            "deltaFromDefault": {"winRatePct": 0.0, "avgPnlPct": 0.0},
            "grid": [],
        }

    pt_grid = [25.0, 35.0, 45.0, 50.0, 60.0, 75.0]
    sl_grid = [100.0, 150.0, 175.0, 200.0, 250.0, 300.0]

    base = _score_rule(paths, default_profit_target_pct, default_stop_loss_pct)

    grid: List[Dict[str, Any]] = []
    best = None
    for pt in pt_grid:
        for sl in sl_grid:
            sc = _score_rule(paths, pt, sl)
            cell = {
                "profitTarget": float(pt),
                "stopLoss": float(sl),
                "avgPnlPct": round(sc["avg"], 1),
                "winRatePct": round(sc["winRate"], 1),
            }
            grid.append(cell)
            if best is None or sc["avg"] > best["score"]["avg"]:
                best = {"pt": pt, "sl": sl, "score": sc}

    if best is None:
        return {
            "recommendedProfitTarget": float(default_profit_target_pct),
            "recommendedStopLoss": float(default_stop_loss_pct),
            "deltaFromDefault": {"winRatePct": 0.0, "avgPnlPct": 0.0},
            "grid": grid,
        }

    delta_avg = best["score"]["avg"] - base["avg"]
    delta_wr = best["score"]["winRate"] - base["winRate"]

    # Only recommend a change if both metrics improve by min_improvement_pp.
    if delta_avg >= float(min_improvement_pp) and delta_wr >= float(min_improvement_pp):
        rec_pt = float(best["pt"])
        rec_sl = float(best["sl"])
    else:
        rec_pt = float(default_profit_target_pct)
        rec_sl = float(default_stop_loss_pct)
        delta_avg = 0.0
        delta_wr = 0.0

    return {
        "recommendedProfitTarget": rec_pt,
        "recommendedStopLoss": rec_sl,
        "deltaFromDefault": {
            "winRatePct": round(float(delta_wr), 1),
            "avgPnlPct": round(float(delta_avg), 1),
        },
        "grid": grid,
    }
