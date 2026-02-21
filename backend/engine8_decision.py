"""Engine 8 – Decision Framework + Confidence Scoring.

Produces a final CONTINUE / FADE / PASS decision with a 0-100
composite confidence score.

Early-exit conditions (before scoring)
--------------------------------------
1. ``historical_result.force_pass`` is True  →  PASS (insufficient sample).
2. ``regime_score == 0``                     →  PASS (regime hard-block).

Regime overlay is an explicit numeric score
-------------------------------------------
``compute_regime_overlay()`` returns ``(label, tradeGate)``.
Engine 8 maps that to a 0-100 ``regime_score``:

  * NO_TRADE                        → 0   (hard block)
  * CAUTION + Elevated              → 25
  * CAUTION + Normal                → 40
  * OK      + Normal                → 60
  * OK      + Calm                  → 80

A ±10 directional-alignment adjustment is applied, clamped to [0, 100].
No ambiguous "favors" logic exists.

Confidence score components (weighted sum, all 0-100)
-----------------------------------------------------
  * Displacement magnitude (EM + ATR)       20 %
  * Gap structure quality                   15 %
  * Momentum / exhaustion characteristics   15 %
  * Historical pattern alignment            20 %
  * Event clarity (LLM sentiment)           15 %
  * Regime alignment (regime_score)         15 %
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from backend.config import FeatureFlags, get_flags
from backend.engine8_classifier import DisplacementProfile
from backend.engine8_historical import HistoricalPatternResult
from backend.engine8_snapshot import PostEventSnapshot


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class ExtensionDecision:
    ticker: str
    decision: str                            # CONTINUE | FADE | PASS
    direction: Optional[str] = None          # Long | Short | None
    confidence_score: float = 0.0
    risk_units: float = 0.0
    holding_period_days: int = 0
    entry_preference: str = "open"           # open | pullback | confirmation
    event_tag: str = "earnings"
    component_scores: Dict[str, float] = field(default_factory=dict)
    regime_score: float = 0.0
    regime_detail: Dict[str, Any] = field(default_factory=dict)
    pass_reason: Optional[str] = None
    derived_trade_outcome: str = "unknown"

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Regime score mapping
# ---------------------------------------------------------------------------

_REGIME_BASE: Dict[tuple, float] = {
    ("NO_TRADE", "Stress"):   0.0,
    ("NO_TRADE", "Elevated"): 0.0,
    ("NO_TRADE", "Normal"):   0.0,
    ("NO_TRADE", "Calm"):     0.0,
    ("CAUTION",  "Elevated"): 25.0,
    ("CAUTION",  "Normal"):   40.0,
    ("CAUTION",  "Calm"):     50.0,
    ("CAUTION",  "Stress"):   10.0,
    ("OK",       "Elevated"): 50.0,
    ("OK",       "Normal"):   60.0,
    ("OK",       "Calm"):     80.0,
    ("OK",       "Stress"):   30.0,
}


def compute_regime_score(
    *,
    trade_gate: str,
    regime_label: str,
    gap_direction: Optional[str],
    spy_5d_return: Optional[float],
    decision_type: str,             # "CONTINUE" or "FADE"
) -> float:
    """Map regime overlay output to an explicit 0-100 numeric score.

    ``decision_type`` determines the directional alignment bonus:
    - CONTINUE: +10 if SPY 5d is same sign as gap, -10 if opposing.
    - FADE: +10 if SPY 5d opposes gap (mean-reversion context), -10 otherwise.
    """
    base = _REGIME_BASE.get((trade_gate, regime_label))
    if base is None:
        base = _REGIME_BASE.get((trade_gate, "Normal"), 50.0)

    if base == 0.0:
        return 0.0

    bonus = 0.0
    if gap_direction and spy_5d_return is not None:
        gap_positive = gap_direction == "UP"
        spy_positive = spy_5d_return > 0
        same_dir = gap_positive == spy_positive

        if decision_type == "CONTINUE":
            bonus = 10.0 if same_dir else -10.0
        elif decision_type == "FADE":
            bonus = 10.0 if not same_dir else -10.0

    return max(0.0, min(100.0, base + bonus))


# ---------------------------------------------------------------------------
# Sub-score computations (all return 0-100)
# ---------------------------------------------------------------------------

def _score_magnitude(profile: DisplacementProfile) -> float:
    """Higher score for cleaner, larger displacement."""
    em_map = {"extreme": 90, "over": 70, "at": 50, "under": 25, "unknown": 30}
    atr_map = {"extreme": 85, "elevated": 65, "normal": 40, "unknown": 30}
    em_s = em_map.get(profile.magnitude_em_label, 30)
    atr_s = atr_map.get(profile.magnitude_atr_label, 30)
    return 0.6 * em_s + 0.4 * atr_s


def _score_gap_structure(profile: DisplacementProfile) -> float:
    """GAP_AND_HOLD is best for continuation; GAP_AND_FADE for reversal."""
    struct_map = {
        "GAP_AND_HOLD": 80,
        "GAP_AND_FADE": 60,
        "GAP_AND_CONSOLIDATION": 40,
        "unknown": 30,
    }
    return float(struct_map.get(profile.structure_label, 30))


def _score_momentum(profile: DisplacementProfile) -> float:
    """Context alignment as a proxy for momentum quality."""
    ctx_map = {"ALIGNED": 80, "NEUTRAL": 50, "OPPOSED": 25, "unknown": 40}
    return float(ctx_map.get(profile.context_label, 40))


def _score_historical(hist: HistoricalPatternResult, decision_type: str) -> float:
    if hist.force_pass:
        return 0.0

    if decision_type == "CONTINUE":
        prob = hist.continuation_prob_3d
    else:
        prob = hist.reversion_prob_3d

    if prob is None:
        return 30.0

    band_factor = {"HIGH": 1.0, "MEDIUM": 0.85, "LOW": 0.6}.get(hist.confidence_band, 0.6)
    return min(100.0, prob * 100.0 * band_factor)


def _score_event_clarity(snapshot: PostEventSnapshot) -> float:
    """LLM sentiment confidence as event clarity score (current snapshot only)."""
    conf = snapshot.sentiment_confidence
    if snapshot.sentiment == "MIXED":
        return 40.0 + conf * 20.0
    return min(100.0, 50.0 + conf * 50.0)


# ---------------------------------------------------------------------------
# Risk units + holding period
# ---------------------------------------------------------------------------

def _compute_risk_units(confidence: float, flags: FeatureFlags) -> float:
    lo = flags.ENGINE8_MIN_RISK_UNITS
    hi = flags.ENGINE8_MAX_RISK_UNITS
    t = max(0.0, min(1.0, (confidence - 50.0) / 50.0))
    return round(lo + t * (hi - lo), 2)


def _compute_holding_period(hist: HistoricalPatternResult, direction: str, max_days: int) -> int:
    """Pick the horizon with the highest continuation/reversion probability."""
    if hist.force_pass:
        return 0

    best_h = 3
    best_p = 0.0
    for h in (1, 2, 3, 5):
        if direction == "CONTINUE":
            p = getattr(hist, f"continuation_prob_{h}d", None)
        else:
            p = getattr(hist, f"reversion_prob_{h}d", None)
        if p is not None and p > best_p:
            best_p = p
            best_h = h

    return min(best_h, max_days)


def _compute_entry_preference(profile: DisplacementProfile) -> str:
    if profile.structure_label == "GAP_AND_HOLD":
        return "pullback"
    if profile.structure_label == "GAP_AND_FADE":
        return "confirmation"
    return "open"


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def make_decision(
    *,
    ticker: str,
    snapshot: PostEventSnapshot,
    profile: DisplacementProfile,
    historical: HistoricalPatternResult,
    regime_overlay: Dict[str, Any],
    spy_5d_return: Optional[float] = None,
    derived_trade_outcome: str = "unknown",
    flags: Optional[FeatureFlags] = None,
) -> ExtensionDecision:
    """Produce the final CONTINUE / FADE / PASS decision.

    Returns an ``ExtensionDecision`` with full component-score
    transparency and ``pass_reason`` when PASS.
    """
    if flags is None:
        flags = get_flags()

    guidance = regime_overlay.get("guidance") if isinstance(regime_overlay.get("guidance"), dict) else {}
    trade_gate = str(guidance.get("tradeGate") or regime_overlay.get("tradeGate") or "OK")
    regime_label = str(regime_overlay.get("label") or "Normal")

    base_kwargs = dict(
        ticker=ticker,
        derived_trade_outcome=derived_trade_outcome,
    )

    # -- Early exit: force PASS from historical layer -------------------------
    if historical.force_pass:
        return ExtensionDecision(
            **base_kwargs,
            decision="PASS",
            pass_reason="insufficient_historical_sample",
            component_scores={"sample_size": historical.sample_size},
        )

    # -- Early exit: regime hard-block ----------------------------------------
    regime_score_continue = compute_regime_score(
        trade_gate=trade_gate,
        regime_label=regime_label,
        gap_direction=snapshot.direction,
        spy_5d_return=spy_5d_return,
        decision_type="CONTINUE",
    )
    regime_score_fade = compute_regime_score(
        trade_gate=trade_gate,
        regime_label=regime_label,
        gap_direction=snapshot.direction,
        spy_5d_return=spy_5d_return,
        decision_type="FADE",
    )
    if regime_score_continue == 0.0 and regime_score_fade == 0.0:
        return ExtensionDecision(
            **base_kwargs,
            decision="PASS",
            pass_reason="regime_blocked",
            regime_score=0.0,
            regime_detail={"trade_gate": trade_gate, "regime_label": regime_label},
        )

    # -- Compute sub-scores for both candidate decisions ----------------------
    s_magnitude = _score_magnitude(profile)
    s_structure = _score_gap_structure(profile)
    s_momentum = _score_momentum(profile)
    s_event_clarity = _score_event_clarity(snapshot)

    candidates: list[tuple[str, float, float, dict]] = []  # (type, conf, regime_s, components)

    for dtype, regime_s in [("CONTINUE", regime_score_continue), ("FADE", regime_score_fade)]:
        if regime_s == 0.0:
            continue

        s_hist = _score_historical(historical, dtype)

        conf = (
            0.20 * s_magnitude
            + 0.15 * s_structure
            + 0.15 * s_momentum
            + 0.20 * s_hist
            + 0.15 * s_event_clarity
            + 0.15 * regime_s
        )
        conf = round(conf, 2)

        threshold = flags.ENGINE8_CONTINUE_THRESHOLD if dtype == "CONTINUE" else flags.ENGINE8_FADE_THRESHOLD
        prob_key = "continuation_prob_3d" if dtype == "CONTINUE" else "reversion_prob_3d"
        prob_min = flags.ENGINE8_CONTINUATION_PROB_MIN if dtype == "CONTINUE" else flags.ENGINE8_REVERSION_PROB_MIN
        prob_val = getattr(historical, prob_key, None)

        qualifies = (
            conf >= threshold
            and prob_val is not None
            and prob_val >= prob_min
            and regime_s > 0
        )

        comp = {
            "magnitude": round(s_magnitude, 2),
            "gap_structure": round(s_structure, 2),
            "momentum": round(s_momentum, 2),
            "historical": round(s_hist, 2),
            "event_clarity": round(s_event_clarity, 2),
            "regime": round(regime_s, 2),
            "threshold": threshold,
            "prob_3d": prob_val,
            "prob_min": prob_min,
            "qualified": qualifies,
        }

        if qualifies:
            candidates.append((dtype, conf, regime_s, comp))

    # -- Select best candidate or PASS ----------------------------------------
    if not candidates:
        best_regime = max(regime_score_continue, regime_score_fade)
        return ExtensionDecision(
            **base_kwargs,
            decision="PASS",
            pass_reason="below_threshold",
            confidence_score=0.0,
            regime_score=best_regime,
            regime_detail={"trade_gate": trade_gate, "regime_label": regime_label},
        )

    # If both qualify, pick higher confidence; ties → PASS
    candidates.sort(key=lambda c: c[1], reverse=True)
    if len(candidates) >= 2 and candidates[0][1] == candidates[1][1]:
        return ExtensionDecision(
            **base_kwargs,
            decision="PASS",
            pass_reason="tied_candidates",
            confidence_score=candidates[0][1],
            regime_score=candidates[0][2],
        )

    best_type, best_conf, best_regime_s, best_comp = candidates[0]

    if best_type == "CONTINUE":
        direction = "Long" if snapshot.direction == "UP" else "Short"
    else:
        direction = "Short" if snapshot.direction == "UP" else "Long"

    risk_units = _compute_risk_units(best_conf, flags)
    holding = _compute_holding_period(historical, best_type, flags.ENGINE8_MAX_HOLDING_DAYS)
    entry_pref = _compute_entry_preference(profile)

    return ExtensionDecision(
        **base_kwargs,
        decision=best_type,
        direction=direction,
        confidence_score=best_conf,
        risk_units=risk_units,
        holding_period_days=holding,
        entry_preference=entry_pref,
        event_tag="earnings",
        component_scores=best_comp,
        regime_score=best_regime_s,
        regime_detail={"trade_gate": trade_gate, "regime_label": regime_label},
    )
