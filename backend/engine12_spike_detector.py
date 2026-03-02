"""Engine 12 — VIX Spike Detection and Regime Classification.

Detects whether the current VIX move qualifies as a geopolitical shock,
classifies severity, and estimates scenario probabilities dynamically
adjusted by dealer gamma state and cross-asset stress.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

_SHOCK_DB_PATH = os.path.join(os.path.dirname(__file__), "..", "data", "universe", "geopolitical_shocks.json")


@dataclass
class SpikeSignal:
    detected: bool = False
    vix_current: float = 0.0
    vix_20d_ma: float = 0.0
    vix_20d_std: float = 0.0
    spike_pct_above_ma: float = 0.0
    z_score: float = 0.0
    pre_event_regime: str = "unknown"   # low_vol | normal | elevated | high_vol
    pre_event_mean: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "detected": self.detected,
            "vixCurrent": round(self.vix_current, 2),
            "vix20dMA": round(self.vix_20d_ma, 2),
            "vix20dStd": round(self.vix_20d_std, 2),
            "spikePctAboveMA": round(self.spike_pct_above_ma, 1),
            "zScore": round(self.z_score, 2),
            "preEventRegime": self.pre_event_regime,
            "preEventMean": round(self.pre_event_mean, 2),
        }


@dataclass
class SeverityScore:
    score: float = 0.0          # 0-100
    vix_spike_pct: float = 0.0
    spx_gap_pct: float = 0.0
    oil_gap_pct: float = 0.0
    dealer_gamma_sign: str = "unknown"
    cross_asset_stress: float = 50.0
    components: Dict[str, float] = None

    def __post_init__(self):
        if self.components is None:
            self.components = {}

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "vixSpikePct": round(self.vix_spike_pct, 1),
            "spxGapPct": round(self.spx_gap_pct, 2),
            "oilGapPct": round(self.oil_gap_pct, 2),
            "dealerGammaSign": self.dealer_gamma_sign,
            "crossAssetStress": round(self.cross_asset_stress, 1),
            "components": {k: round(v, 2) for k, v in (self.components or {}).items()},
        }


@dataclass
class ScenarioProbabilities:
    p_contained: float = 0.60
    p_disruption: float = 0.25
    p_escalation: float = 0.15
    adjustments: List[str] = None

    def __post_init__(self):
        if self.adjustments is None:
            self.adjustments = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "pContained": round(self.p_contained, 3),
            "pDisruption": round(self.p_disruption, 3),
            "pEscalation": round(self.p_escalation, 3),
            "adjustments": self.adjustments,
        }


def _clamp(lo: float, hi: float, x: float) -> float:
    return max(lo, min(hi, x))


def detect_vix_spike(closes: List[float]) -> SpikeSignal:
    """Detect if current VIX move qualifies as a geopolitical shock.

    Criteria:
    (a) VIX prior 20d mean was in low-vol regime (< 20)
    (b) Current VIX is > 20% above 20d MA
    (c) Move is > 2 standard deviations from recent history
    """
    if len(closes) < 21:
        return SpikeSignal()

    current = closes[-1]
    lookback = closes[-21:-1]
    ma_20 = sum(lookback) / len(lookback)
    var_20 = sum((v - ma_20) ** 2 for v in lookback) / max(1, len(lookback) - 1)
    std_20 = math.sqrt(var_20) if var_20 > 0 else 1.0

    spike_pct = ((current - ma_20) / ma_20 * 100.0) if ma_20 > 0 else 0.0
    z = (current - ma_20) / std_20 if std_20 > 0.1 else 0.0

    if ma_20 < 16:
        regime = "low_vol"
    elif ma_20 < 20:
        regime = "normal"
    elif ma_20 < 25:
        regime = "elevated"
    else:
        regime = "high_vol"

    detected = spike_pct > 15.0 and z > 1.5

    return SpikeSignal(
        detected=detected,
        vix_current=current,
        vix_20d_ma=ma_20,
        vix_20d_std=std_20,
        spike_pct_above_ma=spike_pct,
        z_score=z,
        pre_event_regime=regime,
        pre_event_mean=ma_20,
    )


def classify_event_severity(
    *,
    vix_spike_pct: float,
    spx_gap_pct: float,
    oil_gap_pct: float,
    cross_asset_stress: float = 50.0,
    dealer_gamma_sign: str = "unknown",
    dealer_gamma_bucket: str = "low",
) -> SeverityScore:
    """Composite severity score (0-100) from multi-factor inputs.

    Weights: VIX spike 35%, SPX gap 25%, Oil gap 20%, Cross-asset stress 20%.
    Dealer gamma modifies the final score (short gamma amplifies severity).
    """
    # Normalize each factor to 0-100 scale
    vix_component = _clamp(0, 100, abs(vix_spike_pct) * 2.5)
    spx_component = _clamp(0, 100, abs(spx_gap_pct) * 15.0)
    oil_component = _clamp(0, 100, abs(oil_gap_pct) * 5.0)
    stress_component = _clamp(0, 100, cross_asset_stress)

    raw = (
        vix_component * 0.35
        + spx_component * 0.25
        + oil_component * 0.20
        + stress_component * 0.20
    )

    # Dealer gamma modifier
    gamma_adj = 0.0
    if dealer_gamma_sign == "negative":
        amp = {"low": 5.0, "medium": 10.0, "high": 15.0}.get(dealer_gamma_bucket, 5.0)
        gamma_adj = amp
    elif dealer_gamma_sign == "positive":
        damp = {"low": -3.0, "medium": -6.0, "high": -10.0}.get(dealer_gamma_bucket, -3.0)
        gamma_adj = damp

    score = _clamp(0, 100, raw + gamma_adj)

    return SeverityScore(
        score=score,
        vix_spike_pct=vix_spike_pct,
        spx_gap_pct=spx_gap_pct,
        oil_gap_pct=oil_gap_pct,
        dealer_gamma_sign=dealer_gamma_sign,
        cross_asset_stress=cross_asset_stress,
        components={
            "vix": vix_component,
            "spx": spx_component,
            "oil": oil_component,
            "cross_asset": stress_component,
            "gamma_adj": gamma_adj,
        },
    )


def estimate_scenario_probabilities(
    severity_score: float,
    *,
    dealer_gamma_sign: str = "unknown",
    dealer_gamma_bucket: str = "low",
    cross_asset_stress: float = 50.0,
) -> ScenarioProbabilities:
    """Estimate P(contained), P(disruption), P(escalation).

    Base rates from historical shock DB frequencies, then dynamically
    adjusted by dealer gamma state and cross-asset stress.
    """
    adjustments: List[str] = []

    # Base rates (calibrated from shock DB: ~55% contained, ~27% disruption, ~18% escalation)
    if severity_score < 30:
        p_c, p_d, p_e = 0.70, 0.20, 0.10
    elif severity_score < 50:
        p_c, p_d, p_e = 0.55, 0.28, 0.17
    elif severity_score < 70:
        p_c, p_d, p_e = 0.40, 0.35, 0.25
    else:
        p_c, p_d, p_e = 0.25, 0.35, 0.40

    # Dealer gamma adjustment
    if dealer_gamma_sign == "negative":
        shift = {"low": 0.03, "medium": 0.06, "high": 0.10}.get(dealer_gamma_bucket, 0.03)
        p_c -= shift
        p_e += shift * 0.6
        p_d += shift * 0.4
        adjustments.append(
            f"Dealers short gamma ({dealer_gamma_bucket}): escalation +{shift * 0.6:.0%}, "
            f"contained -{shift:.0%}"
        )
    elif dealer_gamma_sign == "positive":
        shift = {"low": 0.02, "medium": 0.04, "high": 0.07}.get(dealer_gamma_bucket, 0.02)
        p_c += shift
        p_e -= shift * 0.6
        p_d -= shift * 0.4
        adjustments.append(
            f"Dealers long gamma ({dealer_gamma_bucket}): contained +{shift:.0%}"
        )

    # Cross-asset stress adjustment
    if cross_asset_stress > 70:
        shift = min(0.12, (cross_asset_stress - 70) / 250.0)
        p_c -= shift
        p_d += shift * 0.5
        p_e += shift * 0.5
        adjustments.append(f"Cross-asset stress elevated ({cross_asset_stress:.0f}): away from contained")
    elif cross_asset_stress < 35:
        shift = min(0.08, (35 - cross_asset_stress) / 350.0)
        p_c += shift
        p_e -= shift * 0.6
        p_d -= shift * 0.4
        adjustments.append(f"Cross-asset stress low ({cross_asset_stress:.0f}): favoring contained")

    # Normalize to sum to 1
    total = p_c + p_d + p_e
    if total > 0:
        p_c /= total
        p_d /= total
        p_e /= total

    p_c = _clamp(0.05, 0.90, p_c)
    p_d = _clamp(0.05, 0.90, p_d)
    p_e = _clamp(0.02, 0.80, p_e)

    # Re-normalize after clamping
    total = p_c + p_d + p_e
    p_c /= total
    p_d /= total
    p_e /= total

    return ScenarioProbabilities(
        p_contained=p_c,
        p_disruption=p_d,
        p_escalation=p_e,
        adjustments=adjustments,
    )


def load_shock_db() -> List[Dict[str, Any]]:
    """Load the curated geopolitical shock database."""
    try:
        path = os.path.normpath(_SHOCK_DB_PATH)
        with open(path, "r") as f:
            data = json.load(f)
        return data.get("events", [])
    except Exception as exc:
        LOG.warning("Failed to load geopolitical shock DB: %s", exc)
        return []


def find_similar_events(
    *,
    vix_spike_pct: float,
    spx_gap_pct: float,
    oil_gap_pct: float,
    shock_db: Optional[List[Dict[str, Any]]] = None,
    top_n: int = 10,
) -> List[Dict[str, Any]]:
    """Find historical events most similar to current conditions.

    Similarity = inverse Euclidean distance in normalized feature space.
    """
    events = shock_db if shock_db is not None else load_shock_db()
    if not events:
        return []

    scored = []
    for evt in events:
        vix_pre = evt.get("vix_pre_close", 0)
        vix_open = evt.get("vix_event_open", 0)
        if vix_pre <= 0:
            continue
        evt_spike = (vix_open - vix_pre) / vix_pre * 100.0
        evt_spx = evt.get("spx_gap_pct", 0)
        evt_oil = evt.get("oil_gap_pct", 0)
        peak = evt.get("peak_vix", vix_open)
        jump_ratio = peak / vix_open if vix_open > 0 else 1.0

        dist = math.sqrt(
            ((vix_spike_pct - evt_spike) / 10.0) ** 2
            + ((spx_gap_pct - evt_spx) / 3.0) ** 2
            + ((oil_gap_pct - evt_oil) / 5.0) ** 2
        )

        enriched = dict(evt)
        enriched["computed_spike_pct"] = round(evt_spike, 1)
        enriched["jump_ratio"] = round(jump_ratio, 3)
        enriched["similarity_distance"] = round(dist, 3)
        scored.append((dist, enriched))

    scored.sort(key=lambda x: x[0])
    return [evt for _, evt in scored[:top_n]]
