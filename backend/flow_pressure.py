"""Raven-Tech 2.0 – Flow Pressure: first-class composite object.

Flow Pressure is the desk's "weather" — a single composite score (0-100)
with a label (Risk-On / Neutral / Risk-Off) for each tracked symbol
(SPX, QQQ, sector ETFs).

Sub-components (each 0-100):
  1. Dealer gamma support        – from dealer_gamma_context
  2. Vol term structure drift     – from IV7-IV30 z-score
  3. EM richness + skew           – from IV-RV spread percentile
  4. Liquidity / tape stress      – ADV proxy
  5. Macro event density          – from Benzinga calendar

Update cadence: 60s in-memory TTLCache, Redis snapshot every 15 min.
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class FlowPressure:
    """Per-symbol flow pressure reading."""

    timestamp: str = ""
    symbol: str = ""
    score: float = 50.0          # 0-100 (50 = neutral)
    label: str = "Neutral"       # Risk-On | Neutral | Risk-Off
    components: Dict[str, float] = field(default_factory=dict)
    change_since_prior: Optional[float] = None
    prior_label: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "FlowPressure":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class FlowPressureSnapshot:
    """Multi-symbol snapshot."""

    timestamp: str = ""
    symbols: Dict[str, dict] = field(default_factory=dict)  # keyed by symbol
    composite_label: str = "Neutral"
    composite_score: float = 50.0

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------

def _clamp(lo: float, hi: float, x: float) -> float:
    return max(lo, min(hi, x))


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except Exception:
        return None


def _zscore(x: float, xs: List[float]) -> float:
    vals = [v for v in xs if v is not None and math.isfinite(v)]
    if len(vals) < 8:
        return 0.0
    m = sum(vals) / len(vals)
    try:
        s = statistics.pstdev(vals)
    except Exception:
        s = 0.0
    if not math.isfinite(s) or s <= 1e-9:
        return 0.0
    return (x - m) / s


def _percentile_rank(x: float, xs: List[float]) -> float:
    vals = [v for v in xs if v is not None and math.isfinite(v)]
    if not vals:
        return 0.5
    c = sum(1 for v in vals if v <= x)
    return c / len(vals)


def _label_from_score(score: float) -> str:
    if score >= 65:
        return "Risk-On"
    if score <= 35:
        return "Risk-Off"
    return "Neutral"


# ---------------------------------------------------------------------------
# Component computations
# ---------------------------------------------------------------------------


def _compute_dealer_gamma_support(gamma_ctx: Optional[dict]) -> float:
    """Convert dealer gamma context dict into a 0-100 sub-score.

    Supportive (positive, high magnitude) = high score = Risk-On.
    Hostile (negative) = low score = Risk-Off.
    """
    if not gamma_ctx or not isinstance(gamma_ctx, dict):
        return 50.0  # neutral when unavailable

    sign = str(gamma_ctx.get("netGammaSign") or "").lower()
    mag = str(gamma_ctx.get("magnitudeBucket") or "").lower()
    imbalance = _to_float(gamma_ctx.get("callPutImbalance"))

    base = 50.0
    if sign == "positive":
        if mag == "high":
            base = 80.0
        elif mag == "medium":
            base = 68.0
        else:
            base = 58.0
    elif sign == "negative":
        if mag == "high":
            base = 15.0
        elif mag == "medium":
            base = 30.0
        else:
            base = 40.0

    # Slight adjustment from call/put imbalance
    if imbalance is not None:
        base += _clamp(-5.0, 5.0, imbalance * 10.0)

    return _clamp(0.0, 100.0, base)


def _compute_vol_term_structure(
    *,
    iv7: Optional[float],
    iv30: Optional[float],
    rv10: Optional[float],
    iv7_hist: List[float],
    iv30_hist: List[float],
) -> float:
    """Vol term structure drift sub-score (0-100).

    Normal backwardation (IV7 < IV30) = supportive = higher score.
    Inversion (IV7 > IV30) = stress = lower score.
    """
    if iv7 is None or iv30 is None:
        return 50.0

    term_spread = iv30 - iv7  # positive = normal, negative = inverted
    # Normalize to rough z-score using IV30 as denominator
    if iv30 > 0.01:
        norm_spread = term_spread / iv30
    else:
        norm_spread = 0.0

    # Map to 0-100: large positive spread -> high score
    score = 50.0 + norm_spread * 200.0
    return _clamp(0.0, 100.0, score)


def _compute_em_richness_skew(
    *,
    iv7: Optional[float],
    rv10: Optional[float],
    ivrv_hist: List[float],
) -> float:
    """EM richness + skew sub-score (0-100).

    High IV-RV spread (IV much > RV) = premium rich = supportive for selling = high score.
    """
    if iv7 is None or rv10 is None:
        return 50.0

    ivrv = iv7 - rv10
    if ivrv_hist:
        pct = _percentile_rank(ivrv, ivrv_hist)
    else:
        # Rough mapping: positive IV-RV = good for sellers
        pct = _clamp(0.0, 1.0, 0.5 + ivrv * 5.0)

    return _clamp(0.0, 100.0, pct * 100.0)


def _compute_liquidity_stress(adv_20d: Optional[float]) -> float:
    """Liquidity / tape stress sub-score (0-100).

    High ADV = liquid = less stress = higher score.
    Low/missing ADV = stressed = lower score.
    """
    if adv_20d is None or adv_20d <= 0:
        return 40.0  # slight concern when missing

    # Typical thresholds based on go_no_go.py
    if adv_20d >= 1_000_000_000:
        return 90.0
    if adv_20d >= 500_000_000:
        return 75.0
    if adv_20d >= 200_000_000:
        return 60.0
    if adv_20d >= 50_000_000:
        return 45.0
    return 25.0


def _compute_macro_event_density(
    event_count_5d: int,
    high_severity_count: int,
) -> float:
    """Macro event density sub-score (0-100).

    Few events = calm = higher score.
    Many events / high severity = stress = lower score.
    """
    # Base: fewer events is better
    if event_count_5d <= 1:
        base = 85.0
    elif event_count_5d <= 3:
        base = 70.0
    elif event_count_5d <= 5:
        base = 55.0
    elif event_count_5d <= 8:
        base = 40.0
    else:
        base = 25.0

    # High severity events penalize further
    penalty = min(20.0, high_severity_count * 8.0)
    return _clamp(0.0, 100.0, base - penalty)


# ---------------------------------------------------------------------------
# Main computation
# ---------------------------------------------------------------------------

# Default weights for component aggregation
COMPONENT_WEIGHTS = {
    "dealer_gamma_support": 0.25,
    "vol_term_structure_drift": 0.25,
    "em_richness_skew": 0.20,
    "liquidity_tape_stress": 0.15,
    "macro_event_density": 0.15,
}


def compute_flow_pressure(
    *,
    symbol: str,
    timestamp: str = "",
    gamma_ctx: Optional[dict] = None,
    iv7: Optional[float] = None,
    iv30: Optional[float] = None,
    rv10: Optional[float] = None,
    iv7_hist: Optional[List[float]] = None,
    iv30_hist: Optional[List[float]] = None,
    ivrv_hist: Optional[List[float]] = None,
    adv_20d: Optional[float] = None,
    event_count_5d: int = 0,
    high_severity_count: int = 0,
    prior: Optional[FlowPressure] = None,
    weights: Optional[Dict[str, float]] = None,
) -> FlowPressure:
    """Compute a Flow Pressure reading for a single symbol."""

    w = weights or COMPONENT_WEIGHTS

    c_gamma = _compute_dealer_gamma_support(gamma_ctx)
    c_term = _compute_vol_term_structure(
        iv7=iv7, iv30=iv30, rv10=rv10,
        iv7_hist=iv7_hist or [], iv30_hist=iv30_hist or [],
    )
    c_em = _compute_em_richness_skew(
        iv7=iv7, rv10=rv10, ivrv_hist=ivrv_hist or [],
    )
    c_liq = _compute_liquidity_stress(adv_20d)
    c_macro = _compute_macro_event_density(event_count_5d, high_severity_count)

    components = {
        "dealer_gamma_support": round(c_gamma, 1),
        "vol_term_structure_drift": round(c_term, 1),
        "em_richness_skew": round(c_em, 1),
        "liquidity_tape_stress": round(c_liq, 1),
        "macro_event_density": round(c_macro, 1),
    }

    total_w = sum(w.get(k, 0.0) for k in components)
    if total_w <= 0:
        total_w = 1.0

    score = sum(components[k] * w.get(k, 0.0) for k in components) / total_w
    score = round(_clamp(0.0, 100.0, score), 1)
    label = _label_from_score(score)

    change = None
    prior_label = None
    if prior is not None:
        change = round(score - prior.score, 1)
        prior_label = prior.label

    return FlowPressure(
        timestamp=timestamp,
        symbol=symbol,
        score=score,
        label=label,
        components=components,
        change_since_prior=change,
        prior_label=prior_label,
    )


def compute_flow_pressure_snapshot(
    readings: List[FlowPressure],
    timestamp: str = "",
) -> FlowPressureSnapshot:
    """Aggregate multiple per-symbol readings into a snapshot."""

    symbols = {}
    for r in readings:
        symbols[r.symbol] = r.to_dict()

    # Weighted composite: SPX 50%, QQQ 30%, sectors 20% (equal split among sectors)
    spx = next((r for r in readings if r.symbol == "SPX"), None)
    qqq = next((r for r in readings if r.symbol == "QQQ"), None)
    sectors = [r for r in readings if r.symbol not in ("SPX", "QQQ")]

    parts: List[tuple] = []  # (score, weight)
    if spx:
        parts.append((spx.score, 0.50))
    if qqq:
        parts.append((qqq.score, 0.30))
    if sectors:
        sector_avg = sum(s.score for s in sectors) / len(sectors)
        parts.append((sector_avg, 0.20))

    if not parts:
        composite_score = 50.0
    else:
        total_w = sum(w for _, w in parts)
        composite_score = sum(s * w for s, w in parts) / total_w if total_w > 0 else 50.0

    composite_score = round(_clamp(0.0, 100.0, composite_score), 1)
    composite_label = _label_from_score(composite_score)

    return FlowPressureSnapshot(
        timestamp=timestamp,
        symbols=symbols,
        composite_label=composite_label,
        composite_score=composite_score,
    )
