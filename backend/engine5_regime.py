"""Engine 5 – Global Regime Classification Engine.

4-factor stress scoring: higher score = more stress.
- FX stress (30%): funding currency bid = stress
- Yield stress (25%): flattening/inversion = stress
- Commodity stress (20%): risk-off commodity pattern = stress
- IV stress (25%): high IV rank = stress

Classification uses composite score **plus** per-component OR triggers.
Default thresholds (aligned with config.py ENGINE5_REGIME_* values):
- Risk-On:   score <= 30 AND fx <= 45 AND iv <= 55
- Stressed:  score >= 75 OR fx >= 80 OR iv >= 70
- Risk-Off:  score >= 55 (when not Stressed)
- Transitional: otherwise
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------


@dataclass
class GlobalRegime:
    date: str
    label: str                                  # Risk-On | Risk-Off | Transitional | Stressed
    score: float                                # 0-100 composite (higher = more stress)
    components: Dict[str, float] = field(default_factory=dict)
    allowed_structures: List[str] = field(default_factory=list)
    position_size_modifier: float = 1.0
    suppression_flags: List[str] = field(default_factory=list)
    small_cap_bias: Optional[Dict[str, float]] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GlobalRegime":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class RegimeTransitionTriggers:
    """Describes what would change the current regime classification."""

    current_label: str
    current_score: float
    top_drivers: List[Dict[str, Any]]        # Top 2 components by stress, e.g. [{"name": "FX Stress", "key": "fx_stress", "value": 85.8}]
    flip_up_conditions: List[str]            # Human-readable conditions to improve (go toward Risk-On)
    flip_down_conditions: List[str]          # Human-readable conditions to worsen (go toward Stressed)
    boundary_distances: Dict[str, float]     # Signed distance to nearest relevant flip boundary
    proximity_flags: List[str] = field(default_factory=list)  # "near_risk_on", "near_stressed"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_STRUCTURES = [
    "put_credit_spread",
    "call_credit_spread",
    "iron_condor",
    "calendar",
    "diagonal",
]

DIRECTIONAL_ONLY = [
    "put_credit_spread",
    "call_credit_spread",
]

CREDIT_SPREADS = [
    "put_credit_spread",
    "call_credit_spread",
    "iron_condor",
]


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _clamp(lo: float, hi: float, x: float) -> float:
    return max(lo, min(hi, x))


def _percentile_rank(x: float, xs: List[float]) -> Optional[float]:
    """Percentile rank in [0, 1] using <= comparison."""
    vals = [v for v in xs if v is not None and isinstance(v, (int, float)) and math.isfinite(v)]
    if not vals:
        return None
    c = sum(1 for v in vals if v <= x)
    return c / len(vals)


def _safe_mean(xs: List[float]) -> Optional[float]:
    vals = [v for v in xs if v is not None and math.isfinite(v)]
    if not vals:
        return None
    return statistics.mean(vals)


# ---------------------------------------------------------------------------
# Factor computations
# ---------------------------------------------------------------------------


def _compute_fx_stress(
    audusd_returns_5d: List[float],
    usdjpy_returns_5d: List[float],
    fx_stress_history_252d: List[float],
) -> float:
    """FX stress: falling AUDUSD + rising USDJPY = stress (higher score).

    Composite: -mean(AUDUSD 5d returns) + mean(USDJPY 5d returns).
    Positive composite = stress. Percentile-ranked against 252d history.
    Returns 0-100.
    """
    aud_mean = _safe_mean(audusd_returns_5d) or 0.0
    jpy_mean = _safe_mean(usdjpy_returns_5d) or 0.0

    # Stress metric: when AUD falls (negative return) and JPY rises vs USD
    # USDJPY rising = JPY weakening = risk-on, USDJPY falling = JPY strengthening = stress
    # So stress = -AUDUSD_return + (-USDJPY_return) = -AUDUSD - USDJPY
    # Wait: USDJPY falling means JPY strengthening (funding currency bid) = stress
    # So stress = -AUDUSD_return - USDJPY_return
    stress_metric = -aud_mean - jpy_mean

    if fx_stress_history_252d:
        pct = _percentile_rank(stress_metric, fx_stress_history_252d)
        if pct is not None:
            return round(_clamp(0, 100, pct * 100), 2)

    # Fallback: map metric directly to 0-100 scale
    return round(_clamp(0, 100, 50 + stress_metric * 5000), 2)


def _compute_yield_stress(
    slope_2s10s: float,
    slope_5d_change: float,
    yield_stress_history_252d: List[float],
) -> float:
    """Yield stress: flattening/inversion = stress (higher score).

    Composite: -slope_2s10s + abs(slope_5d_change if flattening).
    Negative slope = inversion = high stress.
    Percentile-ranked against 252d history.
    Returns 0-100.
    """
    # More negative slope = more stress. Flattening (negative change) = more stress.
    stress_metric = -slope_2s10s
    if slope_5d_change < 0:
        stress_metric += abs(slope_5d_change) * 0.5  # Extra stress for active flattening

    if yield_stress_history_252d:
        pct = _percentile_rank(stress_metric, yield_stress_history_252d)
        if pct is not None:
            return round(_clamp(0, 100, pct * 100), 2)

    # Fallback heuristic
    if slope_2s10s < -0.5:
        return 90.0
    elif slope_2s10s < 0:
        return 70.0
    elif slope_2s10s < 0.5:
        return 50.0
    elif slope_2s10s < 1.0:
        return 30.0
    return 15.0


def _compute_commodity_stress(
    oil_returns_5d: List[float],
    copper_returns_5d: List[float],
    gold_returns_5d: List[float],
    commodity_stress_history_252d: List[float],
) -> float:
    """Commodity stress: declining oil+copper with rising gold = stress.

    Composite: -mean(oil) - mean(copper) + mean(gold).
    Positive = stress pattern. Percentile-ranked.
    Returns 0-100.
    """
    oil_mean = _safe_mean(oil_returns_5d) or 0.0
    copper_mean = _safe_mean(copper_returns_5d) or 0.0
    gold_mean = _safe_mean(gold_returns_5d) or 0.0

    stress_metric = -oil_mean - copper_mean + gold_mean

    if commodity_stress_history_252d:
        pct = _percentile_rank(stress_metric, commodity_stress_history_252d)
        if pct is not None:
            return round(_clamp(0, 100, pct * 100), 2)

    return round(_clamp(0, 100, 50 + stress_metric * 3000), 2)


def _compute_iv_stress(
    spy_iv_rank: Optional[float],
) -> float:
    """IV stress: high IV rank = stress. Already oriented correctly.

    spy_iv_rank: 0.0 to 1.0 (percentile). Directly maps to 0-100.
    Returns 0-100.
    """
    if spy_iv_rank is None:
        return 50.0  # Neutral if unavailable
    return round(_clamp(0, 100, spy_iv_rank * 100), 2)


def _compute_small_cap_bias(
    iwm_rets: List[float],
    spy_rets: List[float],
) -> Optional[Dict[str, float]]:
    """Compute IWM-SPY relative strength as a regime quality signal.

    Small-cap outperformance (IWM > SPY) confirms risk-on breadth.
    Small-cap underperformance during apparent risk-on is a divergence warning.

    Returns dict with relative_strength_5d, bias label, and divergence flag,
    or None if data is insufficient.
    """
    if len(iwm_rets) < 3 or len(spy_rets) < 3:
        return None

    n = min(len(iwm_rets), len(spy_rets))
    iwm_cum = sum(iwm_rets[-n:])
    spy_cum = sum(spy_rets[-n:])
    rel_strength = iwm_cum - spy_cum

    if rel_strength > 0.5:
        bias = "small_cap_leading"
    elif rel_strength < -0.5:
        bias = "small_cap_lagging"
    else:
        bias = "neutral"

    return {
        "iwm_5d_return": round(iwm_cum, 4),
        "spy_5d_return": round(spy_cum, 4),
        "relative_strength_5d": round(rel_strength, 4),
        "bias": bias,
    }


# ---------------------------------------------------------------------------
# Regime classification
# ---------------------------------------------------------------------------


def classify_regime(
    *,
    date: str,
    fx_stress: float,
    yield_stress: float,
    commodity_stress: float,
    iv_stress: float,
    yield_snapshot: Optional[dict] = None,
    small_cap_bias: Optional[Dict[str, float]] = None,
    stressed_threshold: float = 75.0,
    risk_off_threshold: float = 55.0,
    transitional_threshold: float = 30.0,
) -> GlobalRegime:
    """Classify the global regime from four stress factor scores.

    All inputs are 0-100, higher = more stress.
    Defaults are aligned with config.py ENGINE5_REGIME_* values.

    Uses composite score plus per-component OR triggers:
    - Stressed: score >= stressed_threshold OR fx >= 80 OR iv >= 70
    - Risk-On:  score <= transitional_threshold AND fx <= 45 AND iv <= 55

    If small_cap_bias is provided, it is stored on the regime and used to
    generate a divergence warning when risk-on regime coincides with
    small-cap underperformance.
    """
    score = (
        0.30 * fx_stress
        + 0.25 * yield_stress
        + 0.20 * commodity_stress
        + 0.25 * iv_stress
    )
    score = round(_clamp(0, 100, score), 2)

    # --- OR-trigger classification (stress propagates faster than calm) ---

    # Stressed: any single OR trigger is enough
    is_stressed = (
        score >= stressed_threshold
        or fx_stress >= 80
        or iv_stress >= 70
    )

    # Risk-On: all conditions must hold (calm requires consensus)
    is_risk_on = (
        score <= transitional_threshold
        and fx_stress <= 45
        and iv_stress <= 55
    )

    if is_stressed:
        label = "Stressed"
        allowed = []
        size_mod = 0.0
    elif is_risk_on:
        label = "Risk-On"
        allowed = list(ALL_STRUCTURES)
        size_mod = 1.0
    elif score >= risk_off_threshold:
        label = "Risk-Off"
        allowed = list(DIRECTIONAL_ONLY)
        size_mod = 0.50
    else:
        label = "Transitional"
        allowed = list(CREDIT_SPREADS)
        size_mod = 0.75

    # Suppression flags
    flags: List[str] = []
    if yield_snapshot:
        slope = yield_snapshot.get("us_2s10s_slope")
        if slope is not None and slope < -0.5:
            flags.append("yield_inversion_deepening")
    if fx_stress >= 85:
        flags.append("fx_dislocation")
    if iv_stress >= 90:
        flags.append("iv_extreme")
    if commodity_stress >= 85:
        flags.append("commodity_stress_extreme")

    # IWM small-cap divergence: risk-on regime + small-cap lagging = breadth warning
    if small_cap_bias and small_cap_bias.get("bias") == "small_cap_lagging":
        if label == "Risk-On":
            flags.append("small_cap_divergence")
            size_mod = min(size_mod, 0.85)

    return GlobalRegime(
        date=date,
        label=label,
        score=score,
        components={
            "fx_stress": round(fx_stress, 2),
            "yield_stress": round(yield_stress, 2),
            "commodity_stress": round(commodity_stress, 2),
            "iv_stress": round(iv_stress, 2),
        },
        allowed_structures=allowed,
        position_size_modifier=size_mod,
        suppression_flags=flags,
        small_cap_bias=small_cap_bias,
    )


# ---------------------------------------------------------------------------
# High-level entry point
# ---------------------------------------------------------------------------


def compute_regime_from_bars(
    *,
    date: str,
    bars_history: Dict[str, List[dict]],
    yield_snapshots: List[dict],
    spy_iv_rank: Optional[float] = None,
    stress_histories: Optional[Dict[str, List[float]]] = None,
    stressed_threshold: float = 75.0,
    risk_off_threshold: float = 55.0,
    transitional_threshold: float = 30.0,
) -> GlobalRegime:
    """Compute regime from raw bar history and yield snapshots.

    Args:
        bars_history: {symbol: [{"date": ..., "return_1d_local": ...}, ...]}
        yield_snapshots: List of yield snapshot dicts, sorted by date.
        spy_iv_rank: Current SPY IV rank from ORATS (0-1).
        stress_histories: Optional pre-computed 252d histories for each stress metric.
    """
    histories = stress_histories or {}

    # Extract 5-day return series for FX
    audusd_rets = _extract_recent_returns(bars_history.get("AUDUSD.FOREX", []), 5)
    usdjpy_rets = _extract_recent_returns(bars_history.get("USDJPY.FOREX", []), 5)
    fx_stress = _compute_fx_stress(
        audusd_rets, usdjpy_rets,
        histories.get("fx_stress", []),
    )

    # Yield stress
    latest_yield = yield_snapshots[-1] if yield_snapshots else {}
    slope = latest_yield.get("us_2s10s_slope", 0.0) or 0.0
    # 5-day slope change
    slope_5d_ago = 0.0
    if len(yield_snapshots) >= 6:
        slope_5d_ago = yield_snapshots[-6].get("us_2s10s_slope", 0.0) or 0.0
    slope_change = slope - slope_5d_ago

    yield_stress = _compute_yield_stress(
        slope, slope_change,
        histories.get("yield_stress", []),
    )

    # Commodity stress
    oil_rets = _extract_recent_returns(bars_history.get("USO.US", []), 5)
    copper_rets = _extract_recent_returns(bars_history.get("CPER.US", []), 5)
    gold_rets = _extract_recent_returns(bars_history.get("GLD.US", []), 5)
    commodity_stress = _compute_commodity_stress(
        oil_rets, copper_rets, gold_rets,
        histories.get("commodity_stress", []),
    )

    # IV stress
    iv_stress = _compute_iv_stress(spy_iv_rank)

    # IWM small-cap bias (relative to SPY)
    iwm_rets = _extract_recent_returns(bars_history.get("IWM.US", []), 5)
    spy_rets = _extract_recent_returns(bars_history.get("SPY.US", []), 5)
    small_cap_bias = _compute_small_cap_bias(iwm_rets, spy_rets)

    return classify_regime(
        date=date,
        fx_stress=fx_stress,
        yield_stress=yield_stress,
        commodity_stress=commodity_stress,
        iv_stress=iv_stress,
        yield_snapshot=latest_yield,
        small_cap_bias=small_cap_bias,
        stressed_threshold=stressed_threshold,
        risk_off_threshold=risk_off_threshold,
        transitional_threshold=transitional_threshold,
    )


def _extract_recent_returns(bars: List[dict], n: int) -> List[float]:
    """Extract the last n return_1d_local values from a bar history."""
    sorted_bars = sorted(bars, key=lambda b: str(b.get("date", "")))
    returns = []
    for b in sorted_bars:
        r = b.get("return_1d_local")
        if r is not None:
            try:
                returns.append(float(r))
            except (TypeError, ValueError):
                pass
    return returns[-n:] if returns else []


# ---------------------------------------------------------------------------
# Regime transition triggers
# ---------------------------------------------------------------------------

_COMPONENT_LABELS = {
    "fx_stress": "FX Stress",
    "yield_stress": "Yield Stress",
    "commodity_stress": "Commodity Stress",
    "iv_stress": "IV Stress",
}


def compute_regime_triggers(
    regime: GlobalRegime,
    *,
    stressed_threshold: float = 75.0,
    risk_off_threshold: float = 55.0,
    transitional_threshold: float = 30.0,
) -> RegimeTransitionTriggers:
    """Compute what would change the current regime classification.

    Thresholds must match the values used in classify_regime / config.py
    to produce accurate boundary distances and flip condition text.

    Returns human-readable flip conditions, boundary distances, and
    proximity flags for the desk.
    """
    label = regime.label
    score = regime.score
    comps = regime.components
    fx = comps.get("fx_stress", 50.0)
    iv = comps.get("iv_stress", 50.0)
    yld = comps.get("yield_stress", 50.0)
    cmdty = comps.get("commodity_stress", 50.0)

    fx_stressed = 80
    iv_stressed = 70
    fx_risk_on = 45
    iv_risk_on = 55

    # --- Top 2 drivers (highest stress values) ---
    sorted_comps = sorted(comps.items(), key=lambda kv: kv[1], reverse=True)
    top_drivers = [
        {"name": _COMPONENT_LABELS.get(k, k), "key": k, "value": round(v, 1)}
        for k, v in sorted_comps[:2]
    ]

    # --- Boundary distances ---
    distances: Dict[str, float] = {
        "score_to_stressed": round(score - stressed_threshold, 1),
        "score_to_risk_on": round(score - transitional_threshold, 1),
        "fx_to_stressed": round(fx - fx_stressed, 1),
        "fx_to_risk_on": round(fx - fx_risk_on, 1),
        "iv_to_stressed": round(iv - iv_stressed, 1),
        "iv_to_risk_on": round(iv - iv_risk_on, 1),
    }

    # --- Flip conditions ---
    flip_up: List[str] = []
    flip_down: List[str] = []

    if label == "Transitional":
        flip_up.append(f"Upgrade to Risk-On if Total Score <= {transitional_threshold:.0f} (now {score:.0f}) AND FX Stress <= {fx_risk_on} (now {fx:.0f}) AND IV Stress <= {iv_risk_on} (now {iv:.0f}).")
        flip_down.append(f"Downgrade to Risk-Off if Total Score >= {risk_off_threshold:.0f} (now {score:.0f}).")
        flip_down.append(f"Downgrade to Stressed if FX Stress >= {fx_stressed} (now {fx:.0f}) OR IV Stress >= {iv_stressed} (now {iv:.0f}) OR Total Score >= {stressed_threshold:.0f}.")
    elif label == "Risk-On":
        flip_down.append(f"Downgrade to Transitional if Total Score > {transitional_threshold:.0f} (now {score:.0f}) OR FX Stress > {fx_risk_on} (now {fx:.0f}) OR IV Stress > {iv_risk_on} (now {iv:.0f}).")
        flip_down.append(f"Downgrade to Stressed if FX Stress >= {fx_stressed} OR IV Stress >= {iv_stressed} OR Total Score >= {stressed_threshold:.0f}.")
    elif label == "Risk-Off":
        flip_up.append(f"Improve to Transitional if Total Score < {risk_off_threshold:.0f} (now {score:.0f}) AND no component OR trigger active.")
        flip_down.append(f"Downgrade to Stressed if FX Stress >= {fx_stressed} (now {fx:.0f}) OR IV Stress >= {iv_stressed} (now {iv:.0f}) OR Total Score >= {stressed_threshold:.0f}.")
    elif label == "Stressed":
        flip_up.append(f"Improve to Risk-Off if FX Stress < {fx_stressed - 5} (now {fx:.0f}) AND IV Stress < {iv_stressed - 5} (now {iv:.0f}) AND Total Score < {stressed_threshold:.0f} (now {score:.0f}).")
        flip_up.append(f"Improve to Transitional if Total Score < {risk_off_threshold:.0f} AND all component OR triggers clear.")

    # --- Proximity flags ---
    proximity: List[str] = []
    stressed_proximity = stressed_threshold - 10
    if label == "Transitional":
        if score <= transitional_threshold + 10 or fx <= fx_risk_on + 5:
            proximity.append("near_risk_on")
        if score >= stressed_proximity or fx >= fx_stressed - 5 or iv >= iv_stressed - 5:
            proximity.append("near_stressed")
    elif label == "Risk-Off":
        if score >= stressed_proximity or fx >= fx_stressed - 5 or iv >= iv_stressed - 5:
            proximity.append("near_stressed")
        if score <= risk_off_threshold + 3:
            proximity.append("near_transitional")

    return RegimeTransitionTriggers(
        current_label=label,
        current_score=score,
        top_drivers=top_drivers,
        flip_up_conditions=flip_up,
        flip_down_conditions=flip_down,
        boundary_distances=distances,
        proximity_flags=proximity,
    )
