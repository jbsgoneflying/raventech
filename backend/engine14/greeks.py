"""Engine 14 — Black-Scholes greeks + per-path P&L attribution.

Scope / simplifying assumptions
-------------------------------
ORATS cached chains only expose mid/ask/bid and IV, not greeks.  Rather
than hit the greek columns (which are often missing on older vintages)
we compute analytic BS greeks from the cached ATM IV and the mapped
strike set.  That keeps attribution consistent with the same inputs the
replay uses.

The greeks we compute are *position greeks* for the short iron
condor — i.e. signed so that a positive number for a given Greek means
"P&L goes up when that factor moves up".

For an IC short: short 1 put (K_sp), long 1 put (K_lp),
                 short 1 call (K_sc), long 1 call (K_lc).

Position greeks are therefore:
    greek_pos = -greek(K_sp, P) + greek(K_lp, P) - greek(K_sc, C) + greek(K_lc, C)

Each leg's greek uses Black-Scholes with zero-rate, zero-dividend
defaults — fine for a 3-to-7 DTE attribution step.

Attribution
-----------
For each path we approximate the P&L as a Taylor expansion of the
position's value around entry:

    dPnL ≈ Δ * dS  +  ½·Γ·dS²  +  Θ·dt  +  V·dIV   +   residual

All figures are expressed in % of entry credit so they sum (with a
residual) to the observed per-path P&L.  The residual captures
higher-order moves, path-dependent effects (early stops), and fill-
model slippage.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Black-Scholes helpers (r=0, q=0)
# ---------------------------------------------------------------------------

_SQRT_2PI = math.sqrt(2.0 * math.pi)


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / _SQRT_2PI


def _cdf(x: float) -> float:
    # Abramowitz-Stegun style approximation via erf.
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


@dataclass(frozen=True)
class LegGreeks:
    delta: float
    gamma: float
    theta: float
    vega: float


def bs_greeks(
    *,
    spot: float,
    strike: float,
    years_to_expiry: float,
    iv: float,
    is_call: bool,
) -> LegGreeks:
    """Return (delta, gamma, theta, vega) per 1-contract for a European
    option, scaled such that:
      - delta is in dollars-of-underlying per dollar move (the usual 0..1)
      - gamma per unit of spot
      - theta in dollars per *day* (positive means long-theta)
      - vega in dollars per 1-vol-point (i.e. per 0.01 of IV)
    """
    S = float(spot); K = float(strike); T = max(1e-6, float(years_to_expiry))
    sig = max(1e-4, float(iv))
    sqrtT = math.sqrt(T)
    d1 = (math.log(S / K) + 0.5 * sig * sig * T) / (sig * sqrtT)
    d2 = d1 - sig * sqrtT
    nd1 = _pdf(d1)
    if is_call:
        delta = _cdf(d1)
        # Theta (per year) for call with r=q=0: -(S * n(d1) * sig) / (2*sqrtT)
        theta_yr = -(S * nd1 * sig) / (2.0 * sqrtT)
    else:
        delta = _cdf(d1) - 1.0
        theta_yr = -(S * nd1 * sig) / (2.0 * sqrtT)
    gamma = nd1 / (S * sig * sqrtT)
    # Theta per calendar day (365).
    theta_day = theta_yr / 365.0
    # Vega per 1 vol point (i.e. per 0.01 of IV).
    vega_01 = (S * nd1 * sqrtT) * 0.01
    return LegGreeks(delta=float(delta), gamma=float(gamma),
                     theta=float(theta_day), vega=float(vega_01))


@dataclass(frozen=True)
class ICGreeks:
    delta: float
    gamma: float
    theta: float
    vega: float


def ic_net_greeks(
    *,
    spot: float,
    iv: float,
    years_to_expiry: float,
    short_put_strike: float,
    long_put_strike: float,
    short_call_strike: float,
    long_call_strike: float,
) -> ICGreeks:
    """Sum-of-legs greeks for a SHORT iron condor.

    Signs: short legs contribute -1×leg_greek, long legs contribute +1×leg_greek.
    """
    sp = bs_greeks(spot=spot, strike=short_put_strike, years_to_expiry=years_to_expiry,
                   iv=iv, is_call=False)
    lp = bs_greeks(spot=spot, strike=long_put_strike,  years_to_expiry=years_to_expiry,
                   iv=iv, is_call=False)
    sc = bs_greeks(spot=spot, strike=short_call_strike, years_to_expiry=years_to_expiry,
                   iv=iv, is_call=True)
    lc = bs_greeks(spot=spot, strike=long_call_strike,  years_to_expiry=years_to_expiry,
                   iv=iv, is_call=True)
    return ICGreeks(
        delta=-sp.delta + lp.delta - sc.delta + lc.delta,
        gamma=-sp.gamma + lp.gamma - sc.gamma + lc.gamma,
        theta=-sp.theta + lp.theta - sc.theta + lc.theta,
        vega=-sp.vega + lp.vega - sc.vega + lc.vega,
    )


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------

@dataclass
class PathAttribution:
    entry_date: str
    delta_pct: float
    gamma_pct: float
    theta_pct: float
    vega_pct: float
    residual_pct: float
    total_pct: float


def attribute_path(
    *,
    entry_date: str,
    entry_credit: float,
    entry_spot: float,
    exit_spot: float,
    entry_iv: float,
    exit_iv: Optional[float],
    days_held: int,
    years_to_expiry: float,
    mapped_strikes: Tuple[float, float, float, float],
    realized_pnl_pct: float,
) -> PathAttribution:
    """Taylor-expand the position value at entry and decompose the
    realized PnL (in % of credit) into the standard greeks.

    The IV path is rarely known; callers that lack exit IV can pass
    ``None`` and vega will be attributed to the residual.
    """
    sp_k, lp_k, sc_k, lc_k = mapped_strikes
    g = ic_net_greeks(
        spot=entry_spot, iv=entry_iv, years_to_expiry=years_to_expiry,
        short_put_strike=sp_k, long_put_strike=lp_k,
        short_call_strike=sc_k, long_call_strike=lc_k,
    )

    dS = float(exit_spot) - float(entry_spot)
    d_delta_usd = g.delta * dS
    d_gamma_usd = 0.5 * g.gamma * dS * dS
    d_theta_usd = g.theta * float(days_held)
    if exit_iv is not None:
        d_vol_points = (float(exit_iv) - float(entry_iv)) * 100.0
        d_vega_usd = g.vega * d_vol_points
    else:
        d_vega_usd = 0.0

    credit_usd = max(1e-9, float(entry_credit))
    to_pct = lambda x: 100.0 * float(x) / credit_usd  # noqa: E731

    delta_pct = to_pct(d_delta_usd)
    gamma_pct = to_pct(d_gamma_usd)
    theta_pct = to_pct(d_theta_usd)
    vega_pct  = to_pct(d_vega_usd)
    total_attributed = delta_pct + gamma_pct + theta_pct + vega_pct
    residual = float(realized_pnl_pct) - total_attributed

    return PathAttribution(
        entry_date=str(entry_date),
        delta_pct=round(delta_pct, 2),
        gamma_pct=round(gamma_pct, 2),
        theta_pct=round(theta_pct, 2),
        vega_pct=round(vega_pct, 2),
        residual_pct=round(residual, 2),
        total_pct=round(float(realized_pnl_pct), 2),
    )


def aggregate_attribution(parts: Iterable[PathAttribution]) -> Dict[str, Any]:
    """Mean each component across paths, plus share of absolute PnL."""
    items = list(parts)
    if not items:
        return {
            "n": 0,
            "deltaPct": 0.0, "gammaPct": 0.0, "thetaPct": 0.0,
            "vegaPct": 0.0,  "residualPct": 0.0, "totalPct": 0.0,
            "shareOfAbsPnl": {},
        }
    n = len(items)
    avg_delta = sum(p.delta_pct for p in items) / n
    avg_gamma = sum(p.gamma_pct for p in items) / n
    avg_theta = sum(p.theta_pct for p in items) / n
    avg_vega  = sum(p.vega_pct  for p in items) / n
    avg_res   = sum(p.residual_pct for p in items) / n
    avg_tot   = sum(p.total_pct  for p in items) / n

    # Share of *absolute* attributed magnitude for a stacked-bar style card.
    abs_sum = sum(abs(x) for x in (avg_delta, avg_gamma, avg_theta, avg_vega, avg_res))
    if abs_sum < 1e-9:
        shares = {k: 0.0 for k in ("delta", "gamma", "theta", "vega", "residual")}
    else:
        shares = {
            "delta":    round(100.0 * abs(avg_delta) / abs_sum, 1),
            "gamma":    round(100.0 * abs(avg_gamma) / abs_sum, 1),
            "theta":    round(100.0 * abs(avg_theta) / abs_sum, 1),
            "vega":     round(100.0 * abs(avg_vega)  / abs_sum, 1),
            "residual": round(100.0 * abs(avg_res)   / abs_sum, 1),
        }

    return {
        "n": int(n),
        "deltaPct":    round(avg_delta, 2),
        "gammaPct":    round(avg_gamma, 2),
        "thetaPct":    round(avg_theta, 2),
        "vegaPct":     round(avg_vega,  2),
        "residualPct": round(avg_res,   2),
        "totalPct":    round(avg_tot,   2),
        "shareOfAbsPnl": shares,
    }
