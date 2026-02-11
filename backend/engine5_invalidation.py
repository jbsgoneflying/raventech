"""Engine 5 – Trade Invalidation Rules (Math-Grounded, EOD Only).

Three-tier invalidation framework for weekly income trade ideas:
- Tier A: Underlying Price Invalidation (EM-multiple + strike proximity)
- Tier B: Driver Invalidation (source-aware reversal checks)
- Tier C: Position Invalidation (delta as probability proxy)

Final status uses a two-of-three trigger:
- 0 triggered → VALID
- 1 triggered → SOFT
- 2+ triggered → HARD
"""

from __future__ import annotations

import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Result data class
# ---------------------------------------------------------------------------


@dataclass
class InvalidationResult:
    """Complete invalidation assessment for a single trade idea."""

    status: str = "VALID"                                       # VALID | SOFT | HARD
    invalidation_price_level: Optional[float] = None
    invalidation_price_distance_pct: Optional[float] = None
    invalidation_delta_threshold: Optional[float] = None
    invalidation_driver_rule: Optional[str] = None
    tests_triggered: List[str] = field(default_factory=list)    # Which of [price, delta, driver] fired
    actions: List[str] = field(default_factory=list)             # Human-readable action guidance

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Constants / defaults
# ---------------------------------------------------------------------------

# Tier A: alpha by regime (EM-multiple cushion; smaller alpha = tighter)
ALPHA_BY_REGIME = {
    "Risk-On": 1.25,
    "Transitional": 1.00,
    "Risk-Off": 1.00,
    "Stressed": 0.75,
}

# Tier A: beta for strike proximity rule
BETA_DEFAULT = 0.15

# Tier C: delta thresholds by regime (abs value)
DELTA_THRESHOLD_BY_REGIME = {
    "Risk-On": 0.35,
    "Transitional": 0.30,
    "Risk-Off": 0.30,
    "Stressed": 0.25,
}

# Tier B: driver-specific thresholds
YIELD_CURVE_CHANGE_BPS = -7        # 5-day curve flattening in bps
YIELD_CURVE_Z_THRESHOLD = -1.2     # z-score threshold for 5-day curve change
FX_STRESS_TRIGGER = 80             # FX stress level that triggers
FX_STRESS_3D_CHANGE = 10           # 3-day FX stress change trigger
COMMODITY_STRESS_TRIGGER = 75
COMMODITY_STRESS_3D_CHANGE = 12
IV_STRESS_TRIGGER = 70
IV_STRESS_3D_CHANGE = 10
GLOBAL_STRESS_OVERRIDE = 75        # Total score that fires all drivers


# ---------------------------------------------------------------------------
# Tier A — Underlying Price Invalidation (EOD)
# ---------------------------------------------------------------------------


def compute_price_invalidation(
    *,
    S0: float,
    EM: Optional[float],
    K_short: Optional[float],
    structure: str,
    regime_label: str,
    IV: Optional[float] = None,
    DTE: Optional[int] = None,
) -> Dict[str, Any]:
    """Compute price invalidation level.

    Returns dict with:
        triggered: bool
        invalidation_price_level: float or None
        invalidation_price_distance_pct: float or None
    """
    result: Dict[str, Any] = {
        "triggered": False,
        "invalidation_price_level": None,
        "invalidation_price_distance_pct": None,
    }

    # Compute EM if not supplied (fallback formula)
    if EM is None and IV is not None and DTE is not None and DTE > 0:
        T = DTE / 252.0
        EM = S0 * IV * math.sqrt(T)

    if EM is None or EM <= 0:
        return result  # Cannot compute without expected move

    alpha = ALPHA_BY_REGIME.get(regime_label, 1.0)
    beta = BETA_DEFAULT
    is_bearish = structure in ("call_credit_spread",)

    if is_bearish:
        # Call credit spread: invalidation upward
        S_inv_EM = S0 + alpha * EM
        S_inv_Strike = (K_short - beta * EM) if K_short is not None else None
        # Use the tighter (lower) level for CCS
        candidates = [S_inv_EM]
        if S_inv_Strike is not None:
            candidates.append(S_inv_Strike)
        S_inv = min(candidates)
        dist_pct = (S_inv - S0) / S0 if S0 != 0 else None
        # NOTE: for CCS, "triggered" conceptually means close >= S_inv,
        # but we only compute the level here — triggering is done against live close.
    else:
        # Put credit spread / iron condor: invalidation downward
        S_inv_EM = S0 - alpha * EM
        S_inv_Strike = (K_short + beta * EM) if K_short is not None else None
        # Use the tighter (higher) level for PCS
        candidates = [S_inv_EM]
        if S_inv_Strike is not None:
            candidates.append(S_inv_Strike)
        S_inv = max(candidates)
        dist_pct = (S0 - S_inv) / S0 if S0 != 0 else None

    result["invalidation_price_level"] = round(S_inv, 2)
    result["invalidation_price_distance_pct"] = round(dist_pct, 4) if dist_pct is not None else None
    # We don't trigger here — triggering happens when current close is compared at EOD
    return result


# ---------------------------------------------------------------------------
# Tier C — Position (Delta) Invalidation
# ---------------------------------------------------------------------------


def compute_delta_invalidation(
    *,
    delta_short: Optional[float],
    structure: str,
    regime_label: str,
) -> Dict[str, Any]:
    """Compute delta invalidation test.

    Returns dict with:
        triggered: bool
        threshold: float
    """
    threshold = DELTA_THRESHOLD_BY_REGIME.get(regime_label, 0.30)
    result: Dict[str, Any] = {
        "triggered": False,
        "threshold": threshold,
    }

    if delta_short is None:
        return result  # Cannot evaluate without delta

    if abs(delta_short) >= threshold:
        result["triggered"] = True

    return result


# ---------------------------------------------------------------------------
# Tier B — Driver Invalidation (Source-Aware)
# ---------------------------------------------------------------------------


def _compute_yield_driver_invalidation(
    yield_curve_series: List[float],
) -> Dict[str, Any]:
    """Check if yield curve has reversed (flattened materially).

    yield_curve_series: list of 2s10s slope values, sorted chronologically,
                        at least 6 entries for 5-day change, ideally 60+ for z-score.
    """
    result: Dict[str, Any] = {
        "triggered": False,
        "rule": "Yield curve 5D change >= -7 bps AND z >= -1.2",
    }

    if len(yield_curve_series) < 6:
        result["rule"] = "Insufficient yield data for driver check"
        return result

    current = yield_curve_series[-1]
    five_ago = yield_curve_series[-6]
    delta_5d = current - five_ago  # in percentage points

    # Convert to bps
    delta_5d_bps = delta_5d * 100  # yield slope already in pct; 1 pct = 100 bps

    # Z-score of 5-day change over rolling history
    changes = []
    for i in range(5, len(yield_curve_series)):
        ch = yield_curve_series[i] - yield_curve_series[i - 5]
        changes.append(ch)

    z_score = None
    if len(changes) >= 10:
        mean_ch = statistics.mean(changes)
        std_ch = statistics.stdev(changes) if len(changes) >= 2 else 0
        if std_ch > 0:
            z_score = (delta_5d - mean_ch) / std_ch

    triggered = False
    if delta_5d_bps < YIELD_CURVE_CHANGE_BPS:
        triggered = True
    if z_score is not None and z_score < YIELD_CURVE_Z_THRESHOLD:
        triggered = True

    rule_parts = []
    rule_parts.append(f"5D curve change: {delta_5d_bps:+.1f} bps (threshold: {YIELD_CURVE_CHANGE_BPS} bps)")
    if z_score is not None:
        rule_parts.append(f"z-score: {z_score:+.2f} (threshold: {YIELD_CURVE_Z_THRESHOLD})")
    else:
        rule_parts.append("z-score: N/A (insufficient history)")

    result["triggered"] = triggered
    result["rule"] = "; ".join(rule_parts)
    return result


def _compute_fx_driver_invalidation(
    fx_stress: float,
    fx_stress_3d_change: Optional[float],
) -> Dict[str, Any]:
    """FX driver invalidation: stress >= 80 OR 3D change >= +10."""
    triggered = (
        fx_stress >= FX_STRESS_TRIGGER
        or (fx_stress_3d_change is not None and fx_stress_3d_change >= FX_STRESS_3D_CHANGE)
    )
    parts = [f"FX Stress: {fx_stress:.0f} (trigger: {FX_STRESS_TRIGGER})"]
    if fx_stress_3d_change is not None:
        parts.append(f"3D change: {fx_stress_3d_change:+.1f} (trigger: +{FX_STRESS_3D_CHANGE})")
    return {
        "triggered": triggered,
        "rule": "; ".join(parts),
    }


def _compute_commodity_driver_invalidation(
    commodity_stress: float,
    commodity_stress_3d_change: Optional[float],
) -> Dict[str, Any]:
    """Commodity driver invalidation: stress >= 75 OR 3D change >= +12."""
    triggered = (
        commodity_stress >= COMMODITY_STRESS_TRIGGER
        or (commodity_stress_3d_change is not None and commodity_stress_3d_change >= COMMODITY_STRESS_3D_CHANGE)
    )
    parts = [f"Commodity Stress: {commodity_stress:.0f} (trigger: {COMMODITY_STRESS_TRIGGER})"]
    if commodity_stress_3d_change is not None:
        parts.append(f"3D change: {commodity_stress_3d_change:+.1f} (trigger: +{COMMODITY_STRESS_3D_CHANGE})")
    return {
        "triggered": triggered,
        "rule": "; ".join(parts),
    }


def _compute_iv_driver_invalidation(
    iv_stress: float,
    iv_stress_3d_change: Optional[float],
) -> Dict[str, Any]:
    """IV driver invalidation: stress >= 70 OR 3D change >= +10."""
    triggered = (
        iv_stress >= IV_STRESS_TRIGGER
        or (iv_stress_3d_change is not None and iv_stress_3d_change >= IV_STRESS_3D_CHANGE)
    )
    parts = [f"IV Stress: {iv_stress:.0f} (trigger: {IV_STRESS_TRIGGER})"]
    if iv_stress_3d_change is not None:
        parts.append(f"3D change: {iv_stress_3d_change:+.1f} (trigger: +{IV_STRESS_3D_CHANGE})")
    return {
        "triggered": triggered,
        "rule": "; ".join(parts),
    }


def compute_driver_invalidation(
    *,
    source_driver: str,
    regime_components: Dict[str, float],
    total_score: float,
    yield_curve_series: Optional[List[float]] = None,
    stress_3d_changes: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Route to the correct driver-specific invalidation check.

    source_driver: yield | fx | commodity | iv | mixed
    regime_components: {fx_stress, yield_stress, commodity_stress, iv_stress}
    stress_3d_changes: optional 3-day changes for each stress component
    yield_curve_series: rolling 2s10s slope values (for yield driver)
    """
    changes = stress_3d_changes or {}

    # Global stress override: if total >= 75, force all drivers triggered
    if total_score >= GLOBAL_STRESS_OVERRIDE:
        return {
            "triggered": True,
            "rule": f"Global stress override: Total Score {total_score:.0f} >= {GLOBAL_STRESS_OVERRIDE}",
        }

    fx = regime_components.get("fx_stress", 50)
    yld = regime_components.get("yield_stress", 50)
    cmdty = regime_components.get("commodity_stress", 50)
    iv = regime_components.get("iv_stress", 50)

    if source_driver == "yield":
        if yield_curve_series:
            return _compute_yield_driver_invalidation(yield_curve_series)
        return {
            "triggered": False,
            "rule": "Yield driver: insufficient curve data for invalidation check",
        }
    elif source_driver == "fx":
        return _compute_fx_driver_invalidation(fx, changes.get("fx_stress"))
    elif source_driver == "commodity":
        return _compute_commodity_driver_invalidation(cmdty, changes.get("commodity_stress"))
    elif source_driver == "iv":
        return _compute_iv_driver_invalidation(iv, changes.get("iv_stress"))
    else:
        # Mixed: check the most-stressed driver
        highest_key = max(regime_components, key=regime_components.get)  # type: ignore[arg-type]
        driver_map = {
            "fx_stress": "fx",
            "yield_stress": "yield",
            "commodity_stress": "commodity",
            "iv_stress": "iv",
        }
        fallback_driver = driver_map.get(highest_key, "fx")
        return compute_driver_invalidation(
            source_driver=fallback_driver,
            regime_components=regime_components,
            total_score=total_score,
            yield_curve_series=yield_curve_series,
            stress_3d_changes=stress_3d_changes,
        )


# ---------------------------------------------------------------------------
# Final two-of-three evaluation
# ---------------------------------------------------------------------------


def evaluate_invalidation(
    *,
    price_test: Dict[str, Any],
    delta_test: Dict[str, Any],
    driver_test: Dict[str, Any],
    S0: Optional[float] = None,
    structure: str = "put_credit_spread",
) -> InvalidationResult:
    """Combine three tier tests into a final invalidation result.

    Two-of-three logic:
    - 0 triggered → VALID
    - 1 triggered → SOFT
    - 2+ triggered → HARD
    """
    triggered: List[str] = []
    if price_test.get("triggered"):
        triggered.append("price")
    if delta_test.get("triggered"):
        triggered.append("delta")
    if driver_test.get("triggered"):
        triggered.append("driver")

    count = len(triggered)
    if count >= 2:
        status = "HARD"
    elif count == 1:
        status = "SOFT"
    else:
        status = "VALID"

    # Action guidance
    actions: List[str] = []
    if status == "HARD":
        actions.append("Do not enter if not in position.")
        actions.append("Plan exit / reduce risk if already in position.")
    elif status == "SOFT":
        actions.append("Reduce size / no new entries / tighten criteria.")
        actions.append("Wait for next EOD confirmation.")
    else:
        actions.append("Idea remains valid.")

    # Build human-readable driver rule
    driver_rule = driver_test.get("rule")

    return InvalidationResult(
        status=status,
        invalidation_price_level=price_test.get("invalidation_price_level"),
        invalidation_price_distance_pct=price_test.get("invalidation_price_distance_pct"),
        invalidation_delta_threshold=delta_test.get("threshold"),
        invalidation_driver_rule=driver_rule,
        tests_triggered=triggered,
        actions=actions,
    )


# ---------------------------------------------------------------------------
# High-level convenience function
# ---------------------------------------------------------------------------


def compute_invalidation_for_idea(
    *,
    S0: Optional[float],
    EM: Optional[float],
    IV: Optional[float],
    DTE: Optional[int],
    K_short: Optional[float],
    delta_short: Optional[float],
    structure: str,
    regime_label: str,
    source_driver: str,
    regime_components: Dict[str, float],
    total_score: float,
    yield_curve_series: Optional[List[float]] = None,
    stress_3d_changes: Optional[Dict[str, float]] = None,
) -> InvalidationResult:
    """One-call invalidation computation for a single trade idea.

    Runs all three tiers and combines with two-of-three logic.
    """
    # Tier A — price
    price_test: Dict[str, Any] = {
        "triggered": False,
        "invalidation_price_level": None,
        "invalidation_price_distance_pct": None,
    }
    if S0 is not None and S0 > 0:
        price_test = compute_price_invalidation(
            S0=S0, EM=EM, K_short=K_short, structure=structure,
            regime_label=regime_label, IV=IV, DTE=DTE,
        )

    # Tier C — delta
    delta_test = compute_delta_invalidation(
        delta_short=delta_short, structure=structure, regime_label=regime_label,
    )

    # Tier B — driver
    driver_test = compute_driver_invalidation(
        source_driver=source_driver,
        regime_components=regime_components,
        total_score=total_score,
        yield_curve_series=yield_curve_series,
        stress_3d_changes=stress_3d_changes,
    )

    return evaluate_invalidation(
        price_test=price_test,
        delta_test=delta_test,
        driver_test=driver_test,
        S0=S0,
        structure=structure,
    )
