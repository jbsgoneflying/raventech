"""Engine 14 — position sizing recommendations.

Given the distribution of replayed analogue outcomes, produce three
independent sizing recommendations so the operator can triangulate:

1. ``kelly_fraction`` — classic Kelly applied to the win/loss asymmetry
   of the P&L distribution (collapsed to a binary win/loss with mean
   magnitudes). We always return *half-Kelly* as the practical cap
   (full Kelly is notoriously aggressive).

2. ``fixed_fractional`` — a static fraction of equity per trade, risk-
   adjusted by the empirical loss. ``risk_per_trade_pct`` is the
   per-trade dollar risk the trader is willing to accept; we divide by
   the worst-case loss observed in the sample to get contract count
   per unit of account.

3. ``empirical_max_dd`` — cap position size so the *historical maximum
   drawdown* (consecutive loss chain) would not exceed
   ``max_drawdown_pct`` of account equity.

All three return a normalized ``sizeFraction`` in [0, 1] representing
the fraction of account equity to commit in *net credit* terms. The
router / UI is expected to translate that into contract counts using
its own margin model.

Inputs
------
``pnls`` — list of per-trade P&L as percent-of-credit from the
analogue replay. A positive number means a winning trade.

``credit_to_equity_pct`` — how much credit (in % of equity) the current
1-contract position would generate. Used to map P&L-of-credit into
P&L-of-equity for the Kelly / DD math.

``account_equity`` — optional, only used to produce a dollar
``recommendedRiskUsd`` field in the payload for display.
"""

from __future__ import annotations

import statistics
from typing import Any, Dict, Iterable, List, Optional, Sequence


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _safe_mean(xs: Sequence[float]) -> float:
    return float(statistics.mean(xs)) if xs else 0.0


def _max_consecutive_loss(pnls: Sequence[float]) -> float:
    """Return the largest cumulative-loss run (positive number).

    We walk the series in order, resetting the running loss whenever
    a winning trade arrives. The peak of the running loss across the
    series is the empirical max drawdown (in trade-return units).
    """
    peak = 0.0
    run = 0.0
    for p in pnls:
        if p >= 0:
            run = 0.0
            continue
        run += -float(p)
        if run > peak:
            peak = run
    return float(peak)


# ---------------------------------------------------------------------------
# Individual sizing methods
# ---------------------------------------------------------------------------

def kelly_fraction(
    pnls: Sequence[float],
    *,
    half_kelly: bool = True,
) -> Dict[str, Any]:
    """Classic Kelly, collapsed to binary win/loss means.

    f* = (p * (b+1) - 1) / b
    where p = win probability, b = (avg_win / avg_loss_magnitude).

    Returns a capped fraction (never > 0.25 even if the math says so —
    we assume option-selling tails).
    """
    if not pnls:
        return {"fraction": 0.0, "winProb": 0.0, "payoffRatio": 0.0,
                "halfKelly": bool(half_kelly), "clamp": True, "source": "kelly"}

    wins = [float(p) for p in pnls if p > 0]
    losses = [float(p) for p in pnls if p <= 0]
    n = len(pnls)
    p_win = len(wins) / n if n else 0.0

    avg_win = _safe_mean(wins)
    avg_loss_mag = abs(_safe_mean(losses)) if losses else 0.0
    if avg_loss_mag <= 1e-9:
        # All-winner sample: Kelly explodes — clamp at 0.25 and move on.
        return {"fraction": 0.25, "winProb": p_win, "payoffRatio": float("inf"),
                "halfKelly": bool(half_kelly), "clamp": True, "source": "kelly"}

    b = avg_win / avg_loss_mag if avg_win > 0 else 0.0
    if b <= 0:
        return {"fraction": 0.0, "winProb": p_win, "payoffRatio": 0.0,
                "halfKelly": bool(half_kelly), "clamp": False, "source": "kelly"}

    raw = (p_win * (b + 1.0) - 1.0) / b
    f = max(0.0, min(0.25, raw))
    if half_kelly:
        f = f * 0.5
    return {
        "fraction": round(float(f), 4),
        "winProb": round(float(p_win), 3),
        "payoffRatio": round(float(b), 3),
        "halfKelly": bool(half_kelly),
        "clamp": bool(raw > 0.25),
        "source": "kelly",
    }


def fixed_fractional(
    pnls: Sequence[float],
    *,
    risk_per_trade_pct: float = 2.0,
    credit_to_equity_pct: float = 5.0,
) -> Dict[str, Any]:
    """Cap position such that the worst-case replay would not lose more
    than ``risk_per_trade_pct`` of equity.

    size = risk_pct / (worst_loss_pct_of_credit * credit_to_equity_pct)
    """
    if not pnls or credit_to_equity_pct <= 0:
        return {"fraction": 0.0, "worstLossPctCredit": 0.0,
                "riskPerTradePct": float(risk_per_trade_pct),
                "source": "fixedFractional"}

    worst = abs(min(float(p) for p in pnls))  # worst % of credit
    if worst <= 0:
        return {"fraction": 1.0, "worstLossPctCredit": 0.0,
                "riskPerTradePct": float(risk_per_trade_pct),
                "source": "fixedFractional"}

    # worst_loss_pct_of_equity = worst_pct_credit * credit_pct_equity / 100
    worst_equity = (worst / 100.0) * (float(credit_to_equity_pct) / 100.0)
    if worst_equity <= 1e-9:
        return {"fraction": 1.0, "worstLossPctCredit": round(worst, 1),
                "riskPerTradePct": float(risk_per_trade_pct),
                "source": "fixedFractional"}
    f = (float(risk_per_trade_pct) / 100.0) / worst_equity
    f = max(0.0, min(1.0, f))
    return {
        "fraction": round(float(f), 4),
        "worstLossPctCredit": round(float(worst), 1),
        "riskPerTradePct": float(risk_per_trade_pct),
        "creditToEquityPct": float(credit_to_equity_pct),
        "source": "fixedFractional",
    }


def empirical_max_dd(
    pnls: Sequence[float],
    *,
    max_drawdown_pct: float = 10.0,
    credit_to_equity_pct: float = 5.0,
) -> Dict[str, Any]:
    """Cap size so that the empirical max drawdown in *account* terms is
    below ``max_drawdown_pct``.

    empirical_dd_equity = empirical_dd_credit * credit_to_equity_pct / 100
    size = max_drawdown_pct / empirical_dd_equity
    """
    if not pnls or credit_to_equity_pct <= 0:
        return {"fraction": 0.0, "empiricalDdPctCredit": 0.0,
                "maxDrawdownPct": float(max_drawdown_pct),
                "source": "empiricalMaxDd"}

    dd = _max_consecutive_loss(pnls)
    if dd <= 0:
        return {"fraction": 1.0, "empiricalDdPctCredit": 0.0,
                "maxDrawdownPct": float(max_drawdown_pct),
                "source": "empiricalMaxDd"}

    dd_equity = (dd / 100.0) * (float(credit_to_equity_pct) / 100.0)
    if dd_equity <= 1e-9:
        return {"fraction": 1.0, "empiricalDdPctCredit": round(dd, 1),
                "maxDrawdownPct": float(max_drawdown_pct),
                "source": "empiricalMaxDd"}
    f = (float(max_drawdown_pct) / 100.0) / dd_equity
    f = max(0.0, min(1.0, f))
    return {
        "fraction": round(float(f), 4),
        "empiricalDdPctCredit": round(float(dd), 1),
        "maxDrawdownPct": float(max_drawdown_pct),
        "creditToEquityPct": float(credit_to_equity_pct),
        "source": "empiricalMaxDd",
    }


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def compute_sizing(
    paths: Iterable[Any],
    *,
    credit_to_equity_pct: float = 5.0,
    risk_per_trade_pct: float = 2.0,
    max_drawdown_pct: float = 10.0,
    account_equity_usd: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute all three sizing recommendations + a consensus "min" cap.

    Return shape is consumed by the UI sizing card.
    """
    pnls = [float(p.exit_pnl_pct) for p in paths if getattr(p, "exit_pnl_pct", None) is not None]
    kelly = kelly_fraction(pnls)
    fixed = fixed_fractional(
        pnls,
        risk_per_trade_pct=risk_per_trade_pct,
        credit_to_equity_pct=credit_to_equity_pct,
    )
    empdd = empirical_max_dd(
        pnls,
        max_drawdown_pct=max_drawdown_pct,
        credit_to_equity_pct=credit_to_equity_pct,
    )

    # Consensus: take the *minimum* of the three (most conservative wins).
    candidates = [kelly["fraction"], fixed["fraction"], empdd["fraction"]]
    consensus = float(min(candidates))

    payload: Dict[str, Any] = {
        "n": int(len(pnls)),
        "kelly": kelly,
        "fixedFractional": fixed,
        "empiricalMaxDd": empdd,
        "consensusFraction": round(consensus, 4),
        "creditToEquityPct": float(credit_to_equity_pct),
    }
    if account_equity_usd is not None and account_equity_usd > 0:
        payload["recommendedAllocationUsd"] = round(float(account_equity_usd) * consensus, 2)
        payload["riskPerTradeUsd"] = round(float(account_equity_usd) * (risk_per_trade_pct / 100.0), 2)
        payload["accountEquityUsd"] = float(account_equity_usd)
    return payload
