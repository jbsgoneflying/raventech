"""Engine 5 – Global to US Volatility Lead-Lag Module.

Detects when global volatility information has not yet been priced into
US options and outputs modifiers for weekly income trade construction:
- Structure preference (wider spreads, aggressive PCS/CCS, etc.)
- Strike width multiplier
- Position size multiplier (stacks with regime modifier)

Design principles:
- EOD data only; no intraday recalculation
- Volatility is a state variable, not a forecast
- Output affects *how* we sell premium, not *whether* markets move

Core flow:
1. Compute GlobalVolScore: weighted z-score composite of global vol proxies
2. Classify US IV State: LOW / NEUTRAL / HIGH from ORATS IV rank
3. Classify VolLagState: mismatch matrix -> UNDERPRICED_RISK / OVERPRICED_RISK
   / CONFIRMED_STRESS / NORMAL
4. Derive modifiers: strike width multiplier, size multiplier, structure bias
"""

from __future__ import annotations

import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------


@dataclass
class VolLeadLagResult:
    """Complete vol lead-lag assessment for the current session."""

    global_vol_score: float = 0.0           # -3 to +3 composite
    global_vol_direction: str = "flat"      # "rising" | "falling" | "flat"
    us_iv_state: str = "NEUTRAL"            # "LOW" | "NEUTRAL" | "HIGH"
    vol_lag_state: str = "NORMAL"           # UNDERPRICED_RISK | OVERPRICED_RISK | CONFIRMED_STRESS | NORMAL
    structure_bias: str = ""                # Human-readable recommendation
    strike_width_multiplier: float = 1.0    # 0.85 to 1.50
    vol_size_multiplier: float = 1.0        # 0.50 to 1.10
    components: Dict[str, float] = field(default_factory=dict)  # Per-proxy z-scores
    suppressed: bool = False                # True if < 2 valid inputs
    suppression_reason: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Strike width multiplier by VolLagState (spec Section 7.2)
STRIKE_WIDTH_MULT = {
    "UNDERPRICED_RISK": 1.25,
    "OVERPRICED_RISK": 0.85,
    "CONFIRMED_STRESS": 1.50,
    "NORMAL": 1.00,
}

# Position size multiplier by VolLagState (spec Section 7.3)
SIZE_MULT = {
    "UNDERPRICED_RISK": 0.75,
    "OVERPRICED_RISK": 1.10,
    "CONFIRMED_STRESS": 0.50,
    "NORMAL": 1.00,
}

# Structure bias text by VolLagState (spec Section 7.1)
STRUCTURE_BIAS = {
    "UNDERPRICED_RISK": "Wider spreads / Iron Condors preferred — vol not yet priced in US options",
    "OVERPRICED_RISK": "Aggressive PCS/CCS — vol decay edge, US IV overpricing risk",
    "CONFIRMED_STRESS": "Very wide IC or no trade — risk acknowledged across regions",
    "NORMAL": "Standard PCS/CCS — neutral vol conditions",
}


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _safe_z_score(value: float, series: List[float], min_n: int = 10) -> Optional[float]:
    """Compute z-score of value against a series. Returns None if insufficient data."""
    vals = [v for v in series if v is not None and math.isfinite(v)]
    if len(vals) < min_n:
        return None
    mu = statistics.mean(vals)
    try:
        sd = statistics.stdev(vals)
    except statistics.StatisticsError:
        return None
    if sd < 1e-12:
        return None
    return (value - mu) / sd


def _realized_vol(returns: List[float], window: int = 20) -> Optional[float]:
    """Compute annualized realized volatility from daily log returns.

    Uses the last `window` returns. Returns None if insufficient data.
    """
    vals = [r for r in returns if r is not None and math.isfinite(r)]
    if len(vals) < max(5, window // 2):
        return None
    recent = vals[-window:]
    if len(recent) < 5:
        return None
    try:
        sd = statistics.stdev(recent)
    except statistics.StatisticsError:
        return None
    return sd * math.sqrt(252)


# ---------------------------------------------------------------------------
# Step 4.1 + 4.2: Global Vol Score
# ---------------------------------------------------------------------------


def _extract_returns(bars: List[dict], n: int) -> List[float]:
    """Extract the last n return_1d_local values from sorted bar history."""
    sorted_bars = sorted(bars, key=lambda b: str(b.get("date", "")))
    returns = []
    for b in sorted_bars:
        r = b.get("return_1d_local")
        if r is not None:
            try:
                returns.append(float(r))
            except (TypeError, ValueError):
                pass
    return returns[-n:] if len(returns) >= n else returns


def _extract_closes(bars: List[dict]) -> List[float]:
    """Extract close values from sorted bar history."""
    sorted_bars = sorted(bars, key=lambda b: str(b.get("date", "")))
    closes = []
    for b in sorted_bars:
        c = b.get("close")
        if c is not None:
            try:
                closes.append(float(c))
            except (TypeError, ValueError):
                pass
    return closes


def _compute_vol_proxy_zscore_from_level(
    bars: List[dict],
    zscore_window: int,
) -> Optional[float]:
    """Compute z-score of 1-day change in a volatility index level.

    Used for direct vol indices (e.g. VSTOXX/V2TX) where higher level = higher vol.
    Positive z-score = vol expanding.
    """
    closes = _extract_closes(bars)
    if len(closes) < 2:
        return None

    # Daily changes
    changes = [closes[i] - closes[i - 1] for i in range(1, len(closes))]
    if len(changes) < max(10, zscore_window // 2):
        return None

    current_change = changes[-1]
    history = changes[-zscore_window:] if len(changes) >= zscore_window else changes
    return _safe_z_score(current_change, history)


def _compute_vol_proxy_zscore_from_realized(
    bars: List[dict],
    zscore_window: int,
    rv_window: int = 20,
) -> Optional[float]:
    """Compute z-score of realized vol change for an equity index.

    Used as fallback when no direct volatility index is available.
    Computes 20-day realized vol, then z-scores the daily change.
    Positive z-score = vol expanding.
    """
    returns = _extract_returns(bars, zscore_window + rv_window + 5)
    if len(returns) < rv_window + 10:
        return None

    # Compute rolling realized vol for each day
    rv_series = []
    for i in range(rv_window, len(returns)):
        window_rets = returns[i - rv_window:i]
        rv = _realized_vol(window_rets, rv_window)
        if rv is not None:
            rv_series.append(rv)

    if len(rv_series) < 5:
        return None

    # Daily change in realized vol
    rv_changes = [rv_series[i] - rv_series[i - 1] for i in range(1, len(rv_series))]
    if len(rv_changes) < 5:
        return None

    current_change = rv_changes[-1]
    history = rv_changes[-zscore_window:] if len(rv_changes) >= zscore_window else rv_changes
    return _safe_z_score(current_change, history)


def _compute_fx_vol_zscore(
    bars: List[dict],
    zscore_window: int,
) -> Optional[float]:
    """Compute z-score of FX absolute return magnitude.

    FX vol is inferred from abs(daily return), not implied FX vol.
    Positive z-score = vol expanding (larger absolute moves).
    """
    returns = _extract_returns(bars, zscore_window + 5)
    if len(returns) < max(10, zscore_window // 2):
        return None

    abs_returns = [abs(r) for r in returns]
    current_abs = abs_returns[-1]
    history = abs_returns[-zscore_window:] if len(abs_returns) >= zscore_window else abs_returns
    return _safe_z_score(current_abs, history)


def compute_global_vol_score(
    bars_history: Dict[str, List[dict]],
    universe: dict,
    zscore_window: int = 60,
) -> tuple[float, Dict[str, float], int]:
    """Compute the weighted global volatility composite score.

    Returns:
        (global_vol_score, component_zscores, valid_count)
        global_vol_score: weighted composite clipped to [-3, +3]
        component_zscores: {proxy_label: z_score}
        valid_count: number of valid proxy z-scores
    """
    components: Dict[str, float] = {}
    weighted_sum = 0.0
    total_weight = 0.0

    # --- Europe volatility ---
    vol_indices = universe.get("volatility_indices", [])
    for vi in vol_indices:
        sym = vi.get("symbol")
        fallback = vi.get("fallback_realized_from")
        weight = vi.get("weight", 0.0)
        label = vi.get("label", sym or fallback or "Unknown")

        z = None
        if sym and sym in bars_history:
            # Direct vol index available
            z = _compute_vol_proxy_zscore_from_level(bars_history[sym], zscore_window)
            if z is not None:
                LOG.info("Vol proxy %s (direct): z=%.2f", label, z)

        if z is None and fallback and fallback in bars_history:
            # Fallback: compute realized vol from equity index
            z = _compute_vol_proxy_zscore_from_realized(bars_history[fallback], zscore_window)
            if z is not None:
                LOG.info("Vol proxy %s (realized from %s): z=%.2f", label, fallback, z)

        if z is not None:
            components[label] = round(z, 4)
            weighted_sum += weight * z
            total_weight += weight

    # --- FX volatility ---
    fx_proxies = universe.get("fx_vol_proxies", [])
    for fp in fx_proxies:
        sym = fp.get("symbol")
        weight = fp.get("weight", 0.0)
        label = fp.get("label", sym or "FX")

        if sym and sym in bars_history:
            z = _compute_fx_vol_zscore(bars_history[sym], zscore_window)
            if z is not None:
                components[label] = round(z, 4)
                weighted_sum += weight * z
                total_weight += weight
                LOG.info("Vol proxy %s (FX abs): z=%.2f", label, z)

    valid_count = len(components)

    if total_weight > 0:
        score = weighted_sum / total_weight  # Normalize by actual weight used
    else:
        score = 0.0

    # Clip to [-3, +3]
    score = max(-3.0, min(3.0, score))
    return round(score, 4), components, valid_count


# ---------------------------------------------------------------------------
# Step 5.1: US IV State
# ---------------------------------------------------------------------------


def classify_us_iv_state(
    iv_rank: Optional[float],
    low_threshold: float = 30.0,
    high_threshold: float = 60.0,
) -> str:
    """Classify US IV state from ORATS IV rank.

    iv_rank: 0.0 to 1.0 (or 0 to 100 if pre-scaled). We handle both.
    Returns: "LOW" | "NEUTRAL" | "HIGH"
    """
    if iv_rank is None:
        return "NEUTRAL"

    # Normalize: if value is 0-1, scale to 0-100
    rank = float(iv_rank)
    if rank <= 1.0 and rank >= 0.0:
        rank *= 100.0

    if rank < low_threshold:
        return "LOW"
    elif rank > high_threshold:
        return "HIGH"
    return "NEUTRAL"


# ---------------------------------------------------------------------------
# Step 6: VolLagState classification
# ---------------------------------------------------------------------------


def classify_vol_lag_state(
    global_vol_score: float,
    us_iv_state: str,
    rising_threshold: float = 0.75,
    falling_threshold: float = -0.75,
    noise_floor: float = 0.40,
) -> str:
    """Classify the volatility lead-lag mismatch.

    Returns one of: UNDERPRICED_RISK | OVERPRICED_RISK | CONFIRMED_STRESS | NORMAL
    """
    # Guard: below noise floor -> NORMAL regardless
    if abs(global_vol_score) < noise_floor:
        return "NORMAL"

    is_rising = global_vol_score > rising_threshold
    is_falling = global_vol_score < falling_threshold

    if is_rising and us_iv_state in ("LOW", "NEUTRAL"):
        return "UNDERPRICED_RISK"
    if is_falling and us_iv_state == "HIGH":
        return "OVERPRICED_RISK"
    if is_rising and us_iv_state == "HIGH":
        return "CONFIRMED_STRESS"

    return "NORMAL"


# ---------------------------------------------------------------------------
# Step 7: Modifiers
# ---------------------------------------------------------------------------


def compute_vol_modifiers(vol_lag_state: str) -> tuple[float, float, str]:
    """Return (strike_width_multiplier, vol_size_multiplier, structure_bias_text)."""
    sw = STRIKE_WIDTH_MULT.get(vol_lag_state, 1.0)
    sm = SIZE_MULT.get(vol_lag_state, 1.0)
    sb = STRUCTURE_BIAS.get(vol_lag_state, "Standard conditions")
    return sw, sm, sb


# ---------------------------------------------------------------------------
# Top-level orchestrator
# ---------------------------------------------------------------------------


def compute_vol_leadlag(
    *,
    bars_history: Dict[str, List[dict]],
    universe: dict,
    spy_iv_rank: Optional[float] = None,
    rising_threshold: float = 0.75,
    falling_threshold: float = -0.75,
    noise_floor: float = 0.40,
    iv_low_threshold: float = 30.0,
    iv_high_threshold: float = 60.0,
    zscore_window: int = 60,
) -> VolLeadLagResult:
    """One-call computation for the full vol lead-lag assessment.

    Args:
        bars_history: {symbol: [bar_dicts]} from Redis durable history.
        universe: Loaded global_assets.json.
        spy_iv_rank: SPY IV rank from ORATS (0-1 or 0-100).
        rising_threshold: GlobalVolScore above this = "rising".
        falling_threshold: GlobalVolScore below this = "falling".
        noise_floor: |score| below this = NORMAL.
        iv_low_threshold: IV rank below this = LOW.
        iv_high_threshold: IV rank above this = HIGH.
        zscore_window: Rolling z-score lookback (trading days).
    """
    # Step 1: Global Vol Score
    global_score, components, valid_count = compute_global_vol_score(
        bars_history, universe, zscore_window,
    )

    # Suppression check: need at least 2 valid proxy inputs
    if valid_count < 2:
        LOG.warning("Vol lead-lag suppressed: only %d valid proxy inputs (need >= 2)", valid_count)
        return VolLeadLagResult(
            global_vol_score=global_score,
            global_vol_direction="flat",
            us_iv_state=classify_us_iv_state(spy_iv_rank, iv_low_threshold, iv_high_threshold),
            vol_lag_state="NORMAL",
            structure_bias="Module suppressed — insufficient global vol data",
            strike_width_multiplier=1.0,
            vol_size_multiplier=1.0,
            components=components,
            suppressed=True,
            suppression_reason=f"Only {valid_count} valid proxy inputs (minimum 2 required)",
        )

    # Direction
    if global_score > rising_threshold:
        direction = "rising"
    elif global_score < falling_threshold:
        direction = "falling"
    else:
        direction = "flat"

    # Step 2: US IV State
    us_state = classify_us_iv_state(spy_iv_rank, iv_low_threshold, iv_high_threshold)

    # Step 3: VolLagState
    lag_state = classify_vol_lag_state(
        global_score, us_state, rising_threshold, falling_threshold, noise_floor,
    )

    # Step 4: Modifiers
    sw_mult, sz_mult, struct_bias = compute_vol_modifiers(lag_state)

    LOG.info(
        "Vol lead-lag: score=%.2f (%s), US IV=%s, state=%s, sw=%.2f, sz=%.2f",
        global_score, direction, us_state, lag_state, sw_mult, sz_mult,
    )

    return VolLeadLagResult(
        global_vol_score=round(global_score, 4),
        global_vol_direction=direction,
        us_iv_state=us_state,
        vol_lag_state=lag_state,
        structure_bias=struct_bias,
        strike_width_multiplier=sw_mult,
        vol_size_multiplier=sz_mult,
        components=components,
        suppressed=False,
        suppression_reason=None,
    )
