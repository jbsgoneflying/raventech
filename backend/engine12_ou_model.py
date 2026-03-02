"""Engine 12 — Ornstein-Uhlenbeck Mean-Reversion Model for VIX.

Calibrates OU parameters from historical VIX spot data via discrete MLE,
simulates forward VIX paths, and computes modeled vs implied half-life
for the persistence mispricing metric.

Model: dV = kappa * (theta - V) * dt + sigma * dW
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


@dataclass(frozen=True)
class OUParams:
    """Calibrated Ornstein-Uhlenbeck parameters."""

    kappa: float       # mean-reversion speed (annualized)
    theta: float       # long-run mean level
    sigma: float       # vol-of-vol (annualized)
    n_obs: int         # number of observations used
    r_squared: float   # goodness of fit

    def to_dict(self) -> Dict[str, Any]:
        return {
            "kappa": round(self.kappa, 6),
            "theta": round(self.theta, 4),
            "sigma": round(self.sigma, 6),
            "nObs": self.n_obs,
            "rSquared": round(self.r_squared, 4),
            "modeledHalfLifeDays": round(modeled_half_life_days(self.kappa), 1),
        }


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def calibrate_ou(closes: List[float], dt_years: float = 1.0 / 252.0) -> Optional[OUParams]:
    """Calibrate OU parameters from a series of VIX close prices.

    Uses discrete MLE: regress V(t+1) - V(t) on V(t).

    dV = kappa*(theta - V)*dt + sigma*dW
    => V(t+1) - V(t) = a + b*V(t) + eps

    where b = -kappa*dt, a = kappa*theta*dt, sigma = std(eps)/sqrt(dt)
    """
    vals = [v for v in closes if v is not None and math.isfinite(v) and v > 0]
    if len(vals) < 60:
        LOG.warning("OU calibration: insufficient data (%d points)", len(vals))
        return None

    n = len(vals) - 1
    x = vals[:-1]
    dv = [vals[i + 1] - vals[i] for i in range(n)]

    sum_x = sum(x)
    sum_dv = sum(dv)
    sum_x2 = sum(xi * xi for xi in x)
    sum_xdv = sum(x[i] * dv[i] for i in range(n))

    denom = n * sum_x2 - sum_x * sum_x
    if abs(denom) < 1e-15:
        return None

    b = (n * sum_xdv - sum_x * sum_dv) / denom
    a = (sum_dv - b * sum_x) / n

    kappa_dt = -b
    if kappa_dt <= 0:
        kappa_dt = 1e-6

    kappa = kappa_dt / dt_years
    theta = a / kappa_dt if kappa_dt > 1e-12 else sum(vals) / len(vals)

    residuals = [dv[i] - a - b * x[i] for i in range(n)]
    ss_res = sum(r * r for r in residuals)
    sigma_dt = math.sqrt(ss_res / max(1, n - 2))
    sigma = sigma_dt / math.sqrt(dt_years)

    mean_dv = sum_dv / n
    ss_tot = sum((dv[i] - mean_dv) ** 2 for i in range(n))
    r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 1e-12 else 0.0

    if theta < 5 or theta > 60:
        theta = sum(vals) / len(vals)
    if kappa < 0.01:
        kappa = 0.01
    if sigma < 0.01:
        sigma = 0.01

    return OUParams(
        kappa=kappa,
        theta=theta,
        sigma=sigma,
        n_obs=n,
        r_squared=max(0.0, min(1.0, r_squared)),
    )


def modeled_half_life_days(kappa: float) -> float:
    """Half-life of mean reversion in trading days."""
    if kappa <= 0:
        return 999.0
    kappa_daily = kappa / 252.0
    if kappa_daily <= 0:
        return 999.0
    return math.log(2.0) / kappa_daily


def implied_half_life_from_term_structure(iv_30d: float, iv_60d: float) -> Optional[float]:
    """Extract market-implied persistence from IV term structure.

    If IV at 30d DTE and 60d DTE are available, the rate at which IV
    declines across the curve implies how long the market expects
    elevated vol to persist.

    implied_hl = -30 / ln(iv_60d / iv_30d)

    Returns implied half-life in trading days, or None if inputs invalid.
    """
    if iv_30d is None or iv_60d is None:
        return None
    if iv_30d <= 0 or iv_60d <= 0:
        return None
    ratio = iv_60d / iv_30d
    if ratio <= 0 or ratio >= 1.0:
        # Curve is flat or upward-sloping — no decay implied
        return None
    ln_ratio = math.log(ratio)
    if abs(ln_ratio) < 1e-12:
        return None
    hl = -30.0 / ln_ratio
    if hl < 1 or hl > 500:
        return None
    return hl


def persistence_mispricing(implied_hl: Optional[float], modeled_hl: float) -> Optional[float]:
    """Compute persistence mispricing in trading days.

    Positive = market overprices persistence = short vol edge.
    Negative = market expects faster decay than model = caution.
    """
    if implied_hl is None:
        return None
    return implied_hl - modeled_hl


def implied_forward_curve(
    params: OUParams,
    vix_current: float,
    horizons_days: List[int],
) -> List[Dict[str, Any]]:
    """Expected VIX at future horizons under the OU model.

    E[V(t)] = theta + (V0 - theta) * exp(-kappa * t)
    """
    out = []
    kappa_daily = params.kappa / 252.0
    for d in horizons_days:
        t = float(d)
        expected = params.theta + (vix_current - params.theta) * math.exp(-kappa_daily * t)
        out.append({
            "horizon_days": d,
            "expected_vix": round(expected, 2),
            "decay_pct": round((1.0 - math.exp(-kappa_daily * t)) * 100, 1),
        })
    return out


def simulate_paths(
    params: OUParams,
    vix_current: float,
    n_days: int,
    n_paths: int,
    seed: int,
) -> List[List[float]]:
    """Simulate forward VIX paths using exact OU transition density.

    V(t+dt) | V(t) ~ N(mu, var)
    where mu = theta + (V(t) - theta)*exp(-kappa*dt)
          var = (sigma^2 / (2*kappa)) * (1 - exp(-2*kappa*dt))
    """
    rng = random.Random(seed)
    kappa_daily = params.kappa / 252.0
    sigma_daily = params.sigma / math.sqrt(252.0)

    exp_neg_k = math.exp(-kappa_daily)
    if params.kappa > 1e-6:
        var = (sigma_daily ** 2 / (2.0 * kappa_daily)) * (1.0 - math.exp(-2.0 * kappa_daily))
    else:
        var = sigma_daily ** 2
    std = math.sqrt(max(0.0, var))

    paths = []
    for _ in range(n_paths):
        path = [vix_current]
        v = vix_current
        for _ in range(n_days):
            mu = params.theta + (v - params.theta) * exp_neg_k
            v = mu + std * rng.gauss(0, 1)
            v = max(5.0, v)  # VIX floor
            path.append(round(v, 4))
        paths.append(path)
    return paths
