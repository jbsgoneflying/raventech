"""Engine 7 – Thematic Relative Value (Pairs) Engine: core signal construction.

Computes ratio-based statistical signals for a fixed library of 20 asset pairs.
All computation is deterministic: same inputs always produce same outputs.

INV-5: ORATS overlay is optional.  When unavailable the score is computed from
price-only components with renormalised weights.  No hard failures.
"""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger(__name__)

_PAIRS_LIB_PATH = Path(__file__).resolve().parent.parent / "data" / "universe" / "pairs_library.json"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairDefinition:
    pair_id: str
    long_ticker: str
    short_ticker: str
    tier: int
    label: str
    default_lookback_days: int

    @classmethod
    def from_dict(cls, d: dict) -> "PairDefinition":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PairAnalysis:
    """Intermediate analysis state produced by the statistical pipeline."""
    pair_id: str = ""
    long_ticker: str = ""
    short_ticker: str = ""
    tier: int = 0
    label: str = ""

    # Ratio series (aligned by date)
    ratio_dates: List[str] = field(default_factory=list)
    ratio_series: List[float] = field(default_factory=list)

    # Statistical positioning
    ratio_current: float = 0.0
    ratio_mean: float = 0.0
    ratio_std: float = 0.0
    z_score: float = 0.0

    # Momentum
    momentum_5d_roc: float = 0.0
    momentum_10d_roc: float = 0.0
    momentum_alignment: float = 0.0  # 0-1; 1 = both ROCs agree

    # Trend context
    ratio_vs_sma20: float = 0.0  # ratio / sma20 - 1
    ratio_vs_sma50: float = 0.0
    trend_structure: str = ""  # "breakout_up", "breakout_down", "mean_reverting", "neutral"

    # Mode
    mode: str = ""  # "mean_reversion" | "momentum"

    # Component scores (each 0-100)
    score_z: float = 0.0
    score_momentum: float = 0.0
    score_trend: float = 0.0
    score_theme: float = 0.0
    score_orats: float = 0.0

    orats_available: bool = False
    orats_overlay: Optional[Dict[str, Any]] = None


@dataclass(frozen=True)
class PairSignal:
    """Final output signal.  Immutable once constructed."""
    pair_id: str
    long_asset: str
    short_asset: str
    tier: int
    label: str
    signal_date: str

    mode: str  # "mean_reversion" | "momentum"
    confidence_score: int  # 0-100
    grade: str  # "A+" | "A" | "B" | "C"

    eligibility: str  # "ELIGIBLE" | "NOT_ELIGIBLE"
    ineligibility_reason: Optional[str]
    tradable: bool

    risk_units: float  # 0.5-1.5
    expected_hold_days: int  # 2-10

    theme_tags: Tuple[str, ...]
    llm_annotation: Optional[Dict[str, Any]]

    z_score: float
    momentum_5d_roc: float
    momentum_10d_roc: float
    ratio_current: float
    ratio_mean: float
    ratio_std: float

    orats_available: bool
    orats_overlay: Optional[Dict[str, Any]]
    overlap_flags: Tuple[str, ...]

    # Component scores for transparency
    score_z: float
    score_momentum: float
    score_trend: float
    score_theme: float
    score_orats: float

    def to_dict(self) -> dict:
        d = asdict(self)
        d["theme_tags"] = list(self.theme_tags)
        d["overlap_flags"] = list(self.overlap_flags)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PairSignal":
        d2 = dict(d)
        if isinstance(d2.get("theme_tags"), list):
            d2["theme_tags"] = tuple(d2["theme_tags"])
        if isinstance(d2.get("overlap_flags"), list):
            d2["overlap_flags"] = tuple(d2["overlap_flags"])
        return cls(**{k: v for k, v in d2.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Pair library loader
# ---------------------------------------------------------------------------

_cached_library: Optional[List[PairDefinition]] = None


def load_pair_library(path: Optional[str] = None) -> List[PairDefinition]:
    global _cached_library
    if _cached_library is not None and path is None:
        return _cached_library
    p = Path(path) if path else _PAIRS_LIB_PATH
    if not p.exists():
        _LOG.warning("Pairs library not found at %s", p)
        return []
    with open(p, "r") as f:
        raw = json.load(f)
    lib = [PairDefinition.from_dict(r) for r in raw if isinstance(r, dict)]
    if path is None:
        _cached_library = lib
    return lib


# ---------------------------------------------------------------------------
# Ratio computation
# ---------------------------------------------------------------------------


def compute_ratio_series(
    bars_long: List[Any],
    bars_short: List[Any],
) -> Tuple[List[str], List[float]]:
    """Align two bar series by date and compute close(long) / close(short).

    Returns (dates, ratios) sorted ascending.  Bars with missing/zero close
    are skipped.
    """
    long_map: Dict[str, float] = {}
    for b in bars_long:
        c = getattr(b, "close", None)
        d = getattr(b, "trade_date", None)
        if c is not None and c > 0 and d:
            long_map[str(d)[:10]] = float(c)

    short_map: Dict[str, float] = {}
    for b in bars_short:
        c = getattr(b, "close", None)
        d = getattr(b, "trade_date", None)
        if c is not None and c > 0 and d:
            short_map[str(d)[:10]] = float(c)

    common = sorted(set(long_map.keys()) & set(short_map.keys()))
    dates: List[str] = []
    ratios: List[float] = []
    for dt_str in common:
        s = short_map[dt_str]
        if s <= 0:
            continue
        dates.append(dt_str)
        ratios.append(long_map[dt_str] / s)
    return dates, ratios


# ---------------------------------------------------------------------------
# Statistical positioning
# ---------------------------------------------------------------------------


def compute_statistical_position(
    ratio_series: List[float],
    window: int = 40,
) -> Tuple[float, float, float, float]:
    """Return (current_ratio, rolling_mean, rolling_std, z_score).

    Uses the last *window* values.  If fewer values are available, uses all.
    """
    if not ratio_series:
        return 0.0, 0.0, 0.0, 0.0

    current = ratio_series[-1]
    w = min(max(window, 5), len(ratio_series))
    segment = ratio_series[-w:]
    mu = sum(segment) / float(w)
    var = sum((v - mu) ** 2 for v in segment) / float(w)
    sigma = math.sqrt(var) if var > 0 else 0.0
    z = (current - mu) / sigma if sigma > 0 else 0.0
    return current, mu, sigma, z


# ---------------------------------------------------------------------------
# Momentum
# ---------------------------------------------------------------------------


def compute_momentum(ratio_series: List[float]) -> Tuple[float, float, float]:
    """Return (roc_5d, roc_10d, alignment).

    alignment is 1.0 if both ROCs agree in sign, else 0.0.
    """
    if len(ratio_series) < 2:
        return 0.0, 0.0, 0.0

    def _roc(n: int) -> float:
        if len(ratio_series) <= n:
            return 0.0
        prev = ratio_series[-1 - n]
        if prev <= 0:
            return 0.0
        return (ratio_series[-1] - prev) / prev

    roc5 = _roc(5)
    roc10 = _roc(10)
    alignment = 1.0 if (roc5 > 0 and roc10 > 0) or (roc5 < 0 and roc10 < 0) else 0.0
    return roc5, roc10, alignment


# ---------------------------------------------------------------------------
# Trend context
# ---------------------------------------------------------------------------


def _simple_moving_average(xs: List[float], window: int) -> Optional[float]:
    if len(xs) < window or window <= 0:
        return None
    seg = xs[-window:]
    return sum(seg) / float(window)


def compute_trend_context(
    ratio_series: List[float],
) -> Tuple[float, float, str]:
    """Return (ratio_vs_sma20, ratio_vs_sma50, trend_structure).

    ratio_vs_smaX = ratio / smaX - 1 (positive = above).
    trend_structure: breakout_up | breakout_down | mean_reverting | neutral.
    """
    if not ratio_series:
        return 0.0, 0.0, "neutral"

    current = ratio_series[-1]
    sma20 = _simple_moving_average(ratio_series, 20)
    sma50 = _simple_moving_average(ratio_series, 50)

    vs20 = (current / sma20 - 1.0) if (sma20 and sma20 > 0) else 0.0
    vs50 = (current / sma50 - 1.0) if (sma50 and sma50 > 0) else 0.0

    # Classify structure
    if sma20 is not None and sma50 is not None:
        if current > sma20 > sma50:
            structure = "breakout_up"
        elif current < sma20 < sma50:
            structure = "breakout_down"
        elif abs(vs20) < 0.01:
            structure = "mean_reverting"
        else:
            structure = "neutral"
    elif sma20 is not None:
        if abs(vs20) < 0.01:
            structure = "mean_reverting"
        elif vs20 > 0.01:
            structure = "breakout_up"
        elif vs20 < -0.01:
            structure = "breakout_down"
        else:
            structure = "neutral"
    else:
        structure = "neutral"

    return vs20, vs50, structure


# ---------------------------------------------------------------------------
# Mode classification
# ---------------------------------------------------------------------------


def classify_mode(
    z_score: float,
    momentum_alignment: float,
    trend_structure: str,
    *,
    z_entry_threshold: float = 1.5,
    z_momentum_threshold: float = 1.0,
) -> str:
    """Classify trade mode: mean_reversion or momentum.

    Mean reversion: z-score at extremes, expecting reversion.
    Momentum: trend breaking with confirmation, expecting continuation.
    """
    abs_z = abs(z_score)

    # Momentum: z-score above momentum threshold + aligned momentum + trending structure
    if (abs_z >= z_momentum_threshold
            and momentum_alignment >= 0.5
            and trend_structure in ("breakout_up", "breakout_down")):
        return "momentum"

    # Mean reversion: z-score at extremes
    if abs_z >= z_entry_threshold:
        return "mean_reversion"

    # Moderate z with momentum confirmation -> momentum
    if abs_z >= z_momentum_threshold and momentum_alignment >= 0.5:
        return "momentum"

    # Default to mean reversion if there's any measurable signal
    if abs_z >= 0.75:
        return "mean_reversion"

    return "mean_reversion"


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------

# Weight table: component -> weight out of 100.
# When ORATS is unavailable, its weight is redistributed proportionally.
_WEIGHTS_FULL = {"z": 30, "momentum": 20, "trend": 15, "theme": 25, "orats": 10}
_WEIGHTS_NO_ORATS = {"z": 34, "momentum": 23, "trend": 17, "theme": 26}


def _z_score_to_component(z: float) -> float:
    """Map |z| to 0-100 component score.  Higher |z| = stronger signal."""
    az = abs(z)
    if az >= 3.0:
        return 100.0
    if az >= 2.5:
        return 90.0
    if az >= 2.0:
        return 80.0
    if az >= 1.5:
        return 65.0
    if az >= 1.0:
        return 45.0
    if az >= 0.75:
        return 30.0
    return max(0.0, az / 0.75 * 30.0)


def _momentum_to_component(roc5: float, roc10: float, alignment: float) -> float:
    """Map momentum metrics to 0-100 component score."""
    mag = (abs(roc5) + abs(roc10)) / 2.0
    base = min(100.0, mag * 2000.0)  # 5% avg ROC -> 100
    return base * (0.6 + 0.4 * alignment)


def _trend_to_component(vs_sma20: float, vs_sma50: float, structure: str) -> float:
    """Map trend context to 0-100 component score."""
    base = 50.0
    if structure in ("breakout_up", "breakout_down"):
        base = 80.0
    elif structure == "mean_reverting":
        base = 60.0

    dist = (abs(vs_sma20) + abs(vs_sma50)) / 2.0
    dist_bonus = min(20.0, dist * 400.0)  # 5% distance -> 20 bonus
    return min(100.0, base + dist_bonus)


def score_pair(
    analysis: PairAnalysis,
    theme_score: float,
    orats_data: Optional[Dict[str, Any]] = None,
) -> Tuple[int, str, Dict[str, float]]:
    """Composite confidence scorer.  Returns (score, grade, component_scores).

    INV-5: if orats_data is None, weights renormalise across price-only
    components.
    """
    comp_z = _z_score_to_component(analysis.z_score)
    comp_mom = _momentum_to_component(
        analysis.momentum_5d_roc, analysis.momentum_10d_roc, analysis.momentum_alignment,
    )
    comp_trend = _trend_to_component(
        analysis.ratio_vs_sma20, analysis.ratio_vs_sma50, analysis.trend_structure,
    )
    comp_theme = min(100.0, max(0.0, theme_score))

    comp_orats = 0.0
    use_orats = orats_data is not None
    if use_orats:
        iv_rank = orats_data.get("iv_rank")
        if iv_rank is not None:
            comp_orats = min(100.0, max(0.0, float(iv_rank)))
        else:
            use_orats = False

    if use_orats:
        weights = _WEIGHTS_FULL
        raw = (
            comp_z * weights["z"]
            + comp_mom * weights["momentum"]
            + comp_trend * weights["trend"]
            + comp_theme * weights["theme"]
            + comp_orats * weights["orats"]
        ) / 100.0
    else:
        weights = _WEIGHTS_NO_ORATS
        raw = (
            comp_z * weights["z"]
            + comp_mom * weights["momentum"]
            + comp_trend * weights["trend"]
            + comp_theme * weights["theme"]
        ) / 100.0

    score = int(round(min(100.0, max(0.0, raw))))

    if score >= 75:
        grade = "A+"
    elif score >= 60:
        grade = "A"
    elif score >= 45:
        grade = "B"
    else:
        grade = "C"

    components = {
        "score_z": round(comp_z, 2),
        "score_momentum": round(comp_mom, 2),
        "score_trend": round(comp_trend, 2),
        "score_theme": round(comp_theme, 2),
        "score_orats": round(comp_orats, 2) if use_orats else 0.0,
    }
    return score, grade, components


# ---------------------------------------------------------------------------
# Risk allocation helpers
# ---------------------------------------------------------------------------


def compute_risk_units(score: int, mode: str) -> float:
    """Map confidence score to 0.5-1.5 risk units."""
    if score >= 80:
        base = 1.5
    elif score >= 65:
        base = 1.0
    else:
        base = 0.5
    return base


def compute_expected_hold(mode: str) -> int:
    """Expected holding period in days based on mode."""
    if mode == "mean_reversion":
        return 3  # 2-5 day range, centre
    return 6  # 3-10 day range, centre


# ---------------------------------------------------------------------------
# Full single-pair analysis pipeline
# ---------------------------------------------------------------------------


def analyze_pair(
    pair_def: PairDefinition,
    bars_long: List[Any],
    bars_short: List[Any],
    *,
    z_score_window: int = 40,
    z_entry_threshold: float = 1.5,
    z_momentum_threshold: float = 1.0,
) -> PairAnalysis:
    """Run the full statistical pipeline for one pair.  Pure computation."""
    dates, ratios = compute_ratio_series(bars_long, bars_short)

    if len(ratios) < 10:
        a = PairAnalysis(
            pair_id=pair_def.pair_id,
            long_ticker=pair_def.long_ticker,
            short_ticker=pair_def.short_ticker,
            tier=pair_def.tier,
            label=pair_def.label,
        )
        return a

    current, mu, sigma, z = compute_statistical_position(ratios, z_score_window)
    roc5, roc10, alignment = compute_momentum(ratios)
    vs20, vs50, structure = compute_trend_context(ratios)
    mode = classify_mode(
        z, alignment, structure,
        z_entry_threshold=z_entry_threshold,
        z_momentum_threshold=z_momentum_threshold,
    )

    return PairAnalysis(
        pair_id=pair_def.pair_id,
        long_ticker=pair_def.long_ticker,
        short_ticker=pair_def.short_ticker,
        tier=pair_def.tier,
        label=pair_def.label,
        ratio_dates=dates,
        ratio_series=ratios,
        ratio_current=current,
        ratio_mean=mu,
        ratio_std=sigma,
        z_score=z,
        momentum_5d_roc=roc5,
        momentum_10d_roc=roc10,
        momentum_alignment=alignment,
        ratio_vs_sma20=vs20,
        ratio_vs_sma50=vs50,
        trend_structure=structure,
        mode=mode,
    )


def build_signal(
    analysis: PairAnalysis,
    signal_date: str,
    theme_score: float,
    theme_tags: List[str],
    *,
    orats_data: Optional[Dict[str, Any]] = None,
    llm_annotation: Optional[Dict[str, Any]] = None,
    theme_required: bool = True,
    min_score: int = 50,
    aplus_threshold: int = 75,
) -> PairSignal:
    """Assemble a PairSignal from analysis + theme results.

    INV-2: when theme_required and theme_score == 0, eligibility = NOT_ELIGIBLE.
    """
    score, grade, components = score_pair(analysis, theme_score, orats_data)

    # Eligibility (INV-2)
    if theme_required and theme_score <= 0:
        eligibility = "NOT_ELIGIBLE"
        ineligibility_reason = "no_theme_support"
        tradable = False
    elif score >= min_score:
        eligibility = "ELIGIBLE"
        ineligibility_reason = None
        tradable = True
    else:
        eligibility = "ELIGIBLE"
        ineligibility_reason = None
        tradable = False  # watchlist: below threshold

    return PairSignal(
        pair_id=analysis.pair_id,
        long_asset=analysis.long_ticker,
        short_asset=analysis.short_ticker,
        tier=analysis.tier,
        label=analysis.label,
        signal_date=signal_date,
        mode=analysis.mode,
        confidence_score=score,
        grade=grade,
        eligibility=eligibility,
        ineligibility_reason=ineligibility_reason,
        tradable=tradable,
        risk_units=compute_risk_units(score, analysis.mode),
        expected_hold_days=compute_expected_hold(analysis.mode),
        theme_tags=tuple(theme_tags),
        llm_annotation=llm_annotation,
        z_score=round(analysis.z_score, 4),
        momentum_5d_roc=round(analysis.momentum_5d_roc, 6),
        momentum_10d_roc=round(analysis.momentum_10d_roc, 6),
        ratio_current=round(analysis.ratio_current, 6),
        ratio_mean=round(analysis.ratio_mean, 6),
        ratio_std=round(analysis.ratio_std, 6),
        orats_available=analysis.orats_available,
        orats_overlay=analysis.orats_overlay,
        overlap_flags=(),
        score_z=components["score_z"],
        score_momentum=components["score_momentum"],
        score_trend=components["score_trend"],
        score_theme=components["score_theme"],
        score_orats=components["score_orats"],
    )
