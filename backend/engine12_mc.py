"""Engine 12 — Scenario-Weighted Monte Carlo with Empirical Tail Calibration.

Samples jump magnitudes from the historical shock database instead of
hard-coded parameters. Dealer gamma state adjusts secondary spike paths.
Computes P&L across 4 options structure types via Black-76 analytics.
"""

from __future__ import annotations

import logging
import math
import random
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.engine12_ou_model import OUParams

LOG = logging.getLogger(__name__)


@dataclass
class JumpDistribution:
    """Empirical jump-size distributions fitted from shock DB."""

    contained_ratios: List[float] = field(default_factory=list)
    disruption_ratios: List[float] = field(default_factory=list)
    escalation_ratios: List[float] = field(default_factory=list)
    contained_secondary_freq: float = 0.0
    disruption_secondary_freq: float = 0.0
    escalation_secondary_freq: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "containedRatios": [round(r, 3) for r in self.contained_ratios],
            "disruptionRatios": [round(r, 3) for r in self.disruption_ratios],
            "escalationRatios": [round(r, 3) for r in self.escalation_ratios],
            "containedSecondaryFreq": round(self.contained_secondary_freq, 3),
            "disruptionSecondaryFreq": round(self.disruption_secondary_freq, 3),
            "escalationSecondaryFreq": round(self.escalation_secondary_freq, 3),
        }


@dataclass
class StructurePnL:
    name: str = ""
    expected_pnl: float = 0.0
    p_profit: float = 0.0
    p_max_loss: float = 0.0
    max_loss: float = 0.0
    max_gain: float = 0.0
    cvar95: float = 0.0
    sharpe: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "expectedPnL": round(self.expected_pnl, 2),
            "pProfit": round(self.p_profit, 3),
            "pMaxLoss": round(self.p_max_loss, 3),
            "maxLoss": round(self.max_loss, 2),
            "maxGain": round(self.max_gain, 2),
            "cvar95": round(self.cvar95, 2),
            "sharpe": round(self.sharpe, 3),
        }


@dataclass
class MCResult:
    n_sims: int = 0
    seed: int = 0
    structures: List[StructurePnL] = field(default_factory=list)
    vix_terminal_pctiles: Dict[str, float] = field(default_factory=dict)
    notes: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "nSims": self.n_sims,
            "seed": self.seed,
            "structures": [s.to_dict() for s in self.structures],
            "vixTerminalPctiles": {k: round(v, 2) for k, v in self.vix_terminal_pctiles.items()},
            "notes": self.notes,
        }


def fit_empirical_jump_distribution(shock_db: List[Dict[str, Any]]) -> JumpDistribution:
    """Compute peak_vix / vix_event_open ratios grouped by outcome class."""

    contained_r, disruption_r, escalation_r = [], [], []
    contained_sec, disruption_sec, escalation_sec = 0, 0, 0
    contained_n, disruption_n, escalation_n = 0, 0, 0

    for evt in shock_db:
        vix_open = evt.get("vix_event_open", 0)
        peak = evt.get("peak_vix", vix_open)
        if vix_open <= 0:
            continue
        ratio = peak / vix_open
        outcome = evt.get("outcome_class", "contained")
        secondary = evt.get("secondary_spike", False)

        if outcome == "contained":
            contained_r.append(ratio)
            contained_n += 1
            if secondary:
                contained_sec += 1
        elif outcome == "disruption":
            disruption_r.append(ratio)
            disruption_n += 1
            if secondary:
                disruption_sec += 1
        elif outcome == "escalation":
            escalation_r.append(ratio)
            escalation_n += 1
            if secondary:
                escalation_sec += 1

    # Fallbacks when a class has no data
    if not contained_r:
        contained_r = [1.0, 1.05, 1.08]
    if not disruption_r:
        disruption_r = [1.15, 1.25, 1.35]
    if not escalation_r:
        escalation_r = [1.40, 1.65, 2.00]

    return JumpDistribution(
        contained_ratios=sorted(contained_r),
        disruption_ratios=sorted(disruption_r),
        escalation_ratios=sorted(escalation_r),
        contained_secondary_freq=contained_sec / max(1, contained_n),
        disruption_secondary_freq=disruption_sec / max(1, disruption_n),
        escalation_secondary_freq=escalation_sec / max(1, escalation_n),
    )


def _black76_call(f: float, k: float, sigma: float, t: float) -> float:
    """Black-76 call price. f=forward, k=strike, sigma=vol, t=time to expiry."""
    if t <= 0 or sigma <= 0 or f <= 0:
        return max(0.0, f - k)
    sv = sigma * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sv * sv) / sv
    d2 = d1 - sv
    return f * _norm_cdf(d1) - k * _norm_cdf(d2)


def _black76_put(f: float, k: float, sigma: float, t: float) -> float:
    """Black-76 put price."""
    if t <= 0 or sigma <= 0 or f <= 0:
        return max(0.0, k - f)
    sv = sigma * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * sv * sv) / sv
    d2 = d1 - sv
    return k * _norm_cdf(-d2) - f * _norm_cdf(-d1)


def _norm_cdf(x: float) -> float:
    """Standard normal CDF approximation (Abramowitz & Stegun)."""
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _gamma_amplification(sign: str, bucket: str, flags: Any = None) -> float:
    """Compute dealer gamma amplification factor."""
    if sign == "negative":
        return {"low": 0.10, "medium": 0.20, "high": 0.30}.get(bucket, 0.10)
    elif sign == "positive":
        return -{"low": 0.10, "medium": 0.20, "high": 0.30}.get(bucket, 0.10)
    return 0.0


def _cvar95(losses: List[float]) -> float:
    if not losses:
        return 0.0
    xs = sorted(losses)
    n = len(xs)
    tail_n = max(1, int(math.ceil(0.05 * n)))
    tail = xs[-tail_n:]
    return sum(tail) / len(tail)


def _pctiles(xs: List[float], ps: List[int]) -> Dict[str, float]:
    if not xs:
        return {}
    ys = sorted(xs)
    n = len(ys)
    out = {}
    for p in ps:
        if p <= 0:
            v = ys[0]
        elif p >= 100:
            v = ys[-1]
        else:
            pos = (p / 100.0) * (n - 1)
            lo = int(math.floor(pos))
            hi = min(int(math.ceil(pos)), n - 1)
            w = pos - lo
            v = (1.0 - w) * ys[lo] + w * ys[hi]
        out[str(p)] = v
    return out


def run_vix_fade_mc(
    *,
    vix_current: float,
    ou_params: OUParams,
    scenario_probs: Tuple[float, float, float],
    jump_dist: JumpDistribution,
    dealer_gamma_sign: str = "unknown",
    dealer_gamma_bucket: str = "low",
    n_sims: int = 10000,
    n_days: int = 10,
    seed: int = 42,
) -> MCResult:
    """Run scenario-weighted Monte Carlo with empirical tail calibration.

    Returns P&L for 4 structure types and VIX terminal distribution.
    """
    rng = random.Random(seed)
    p_c, p_d, p_e = scenario_probs
    total = p_c + p_d + p_e
    p_c, p_d, p_e = p_c / total, p_d / total, p_e / total

    gamma_amp = _gamma_amplification(dealer_gamma_sign, dealer_gamma_bucket)

    kappa_daily = ou_params.kappa / 252.0
    exp_neg_k = math.exp(-kappa_daily)
    sigma_daily = ou_params.sigma / math.sqrt(252.0)
    if ou_params.kappa > 1e-6:
        var = (sigma_daily ** 2 / (2.0 * kappa_daily)) * (1.0 - math.exp(-2.0 * kappa_daily))
    else:
        var = sigma_daily ** 2
    std = math.sqrt(max(0.0, var))

    vix_vol = ou_params.sigma / 100.0  # vol-of-vol for Black-76

    # Define 4 structures relative to current VIX
    # 1) Short call spread: sell ATM+2 / buy ATM+7
    cs_short_k = vix_current + 2
    cs_long_k = vix_current + 7
    cs_width = cs_long_k - cs_short_k
    cs_entry_credit = _black76_call(vix_current, cs_short_k, vix_vol, n_days / 252.0) - \
                      _black76_call(vix_current, cs_long_k, vix_vol, n_days / 252.0)
    cs_entry_credit = max(0.01, cs_entry_credit)

    # 2) Long put: buy ATM-1 put
    put_k = max(10, vix_current - 1)
    put_entry_cost = _black76_put(vix_current, put_k, vix_vol, n_days / 252.0)

    # 3) Long put spread: buy ATM-1 put / sell ATM-6 put
    ps_long_k = max(10, vix_current - 1)
    ps_short_k = max(8, vix_current - 6)
    ps_entry_cost = _black76_put(vix_current, ps_long_k, vix_vol, n_days / 252.0) - \
                    _black76_put(vix_current, ps_short_k, vix_vol, n_days / 252.0)

    # 4) Calendar: sell front-month call / buy back-month call
    cal_strike = vix_current + 1
    cal_front_credit = _black76_call(vix_current, cal_strike, vix_vol, n_days / 252.0)
    cal_back_cost = _black76_call(vix_current, cal_strike, vix_vol, (n_days + 20) / 252.0)
    cal_entry_debit = cal_back_cost - cal_front_credit

    vix_terminals = []

    cs_pnls = []
    put_pnls = []
    ps_pnls = []
    cal_pnls = []

    notes = []

    for _ in range(n_sims):
        # Draw scenario
        u = rng.random()
        if u < p_c:
            scenario = "contained"
            ratios = jump_dist.contained_ratios
            sec_freq = jump_dist.contained_secondary_freq
        elif u < p_c + p_d:
            scenario = "disruption"
            ratios = jump_dist.disruption_ratios
            sec_freq = jump_dist.disruption_secondary_freq
        else:
            scenario = "escalation"
            ratios = jump_dist.escalation_ratios
            sec_freq = jump_dist.escalation_secondary_freq

        # Simulate OU path with possible secondary spike
        v = vix_current
        has_secondary = rng.random() < sec_freq
        spike_day = rng.randint(1, min(3, n_days)) if has_secondary else -1
        jump_ratio = rng.choice(ratios) if ratios else 1.0
        jump_ratio = jump_ratio * (1.0 + gamma_amp) if has_secondary else jump_ratio

        for day in range(n_days):
            if day == spike_day and has_secondary:
                v = v * jump_ratio
            mu = ou_params.theta + (v - ou_params.theta) * exp_neg_k
            v = mu + std * rng.gauss(0, 1)
            v = max(5.0, v)

        vix_terminal = v
        vix_terminals.append(vix_terminal)

        # Structure P&L at terminal
        # 1) Short call spread: credit received - max(0, terminal - short_k) + max(0, terminal - long_k)
        cs_intrinsic = max(0, vix_terminal - cs_short_k) - max(0, vix_terminal - cs_long_k)
        cs_pnl = (cs_entry_credit - cs_intrinsic) * 100  # per contract
        cs_pnls.append(cs_pnl)

        # 2) Long put
        put_intrinsic = max(0, put_k - vix_terminal)
        put_pnl = (put_intrinsic - put_entry_cost) * 100
        put_pnls.append(put_pnl)

        # 3) Long put spread
        ps_intrinsic = max(0, ps_long_k - vix_terminal) - max(0, ps_short_k - vix_terminal)
        ps_pnl = (ps_intrinsic - ps_entry_cost) * 100
        ps_pnls.append(ps_pnl)

        # 4) Calendar (approximate: front expires worthless if VIX below strike)
        cal_front_value = max(0, vix_terminal - cal_strike)
        # Back-month still has time value — estimate with residual Black-76
        cal_back_value = _black76_call(vix_terminal, cal_strike, vix_vol, 20 / 252.0)
        cal_pnl = ((cal_back_value - cal_front_value) - cal_entry_debit) * 100
        cal_pnls.append(cal_pnl)

    def _build_structure(name: str, pnls: List[float]) -> StructurePnL:
        if not pnls:
            return StructurePnL(name=name)
        mean_pnl = sum(pnls) / len(pnls)
        p_profit = sum(1 for p in pnls if p > 0) / len(pnls)
        sorted_pnl = sorted(pnls)
        max_loss = sorted_pnl[0]
        max_gain = sorted_pnl[-1]
        p_max = sum(1 for p in pnls if p <= max_loss * 0.95) / len(pnls) if max_loss < 0 else 0
        losses = [-p for p in pnls if p < 0]
        cvar = _cvar95(losses) if losses else 0.0
        std_pnl = math.sqrt(sum((p - mean_pnl) ** 2 for p in pnls) / max(1, len(pnls) - 1))
        sharpe = mean_pnl / std_pnl if std_pnl > 0.01 else 0.0
        return StructurePnL(
            name=name,
            expected_pnl=mean_pnl,
            p_profit=p_profit,
            p_max_loss=p_max,
            max_loss=max_loss,
            max_gain=max_gain,
            cvar95=cvar,
            sharpe=sharpe,
        )

    structures = [
        _build_structure("Short Call Spread", cs_pnls),
        _build_structure("Long Put", put_pnls),
        _build_structure("Long Put Spread", ps_pnls),
        _build_structure("Calendar Spread", cal_pnls),
    ]

    vix_pctiles = _pctiles(vix_terminals, [5, 10, 25, 50, 75, 90, 95])

    if gamma_amp > 0:
        notes.append(f"Dealer short gamma amplification: +{gamma_amp:.0%}")
    elif gamma_amp < 0:
        notes.append(f"Dealer long gamma dampening: {gamma_amp:.0%}")

    return MCResult(
        n_sims=n_sims,
        seed=seed,
        structures=structures,
        vix_terminal_pctiles=vix_pctiles,
        notes=notes,
    )
