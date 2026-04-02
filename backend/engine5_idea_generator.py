"""Engine 5 – Weekly Idea Generator.

Combines lead-lag signals, regime state, ORATS options surface data,
and Benzinga event filters into structured WeeklyIdea output.

Now includes:
- source_driver identification per idea (yield | fx | commodity | iv | mixed)
- Trade invalidation rules (three-tier, two-of-three logic)
- Regime transition triggers attached to output
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from backend.engine5_regime import GlobalRegime, RegimeTransitionTriggers, compute_regime_triggers
from backend.engine5_translation import SectorBias, IndexBias
from backend.engine5_invalidation import compute_invalidation_for_idea

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class TradeIdea:
    symbol: str
    structure: str                   # put_credit_spread | call_credit_spread | iron_condor | ...
    directional_lean: str            # bullish | bearish | neutral
    confidence: int                  # 0-100
    regime_context: str              # Risk-On | Risk-Off | ...
    lead_lag_source: str             # Human-readable
    source_driver: str = "mixed"     # yield | fx | commodity | iv | mixed
    iv_rank: Optional[float] = None
    expected_move: Optional[float] = None
    max_risk_estimate: Optional[str] = None
    roc_estimate_model: Optional[str] = None
    roc_assumptions: Optional[Dict[str, Any]] = None
    notes: List[str] = field(default_factory=list)
    suppressed: bool = False
    # --- Invalidation fields ---
    invalidation_status: str = "VALID"                          # VALID | SOFT | HARD
    invalidation_price_level: Optional[float] = None
    invalidation_price_distance_pct: Optional[float] = None
    invalidation_delta_threshold: Optional[float] = None
    invalidation_driver_rule: Optional[str] = None
    invalidation_tests_triggered: List[str] = field(default_factory=list)
    invalidation_actions: List[str] = field(default_factory=list)
    # --- Vol lead-lag fields ---
    global_vol_score: Optional[float] = None
    us_iv_rank_state: Optional[str] = None               # LOW | NEUTRAL | HIGH
    vol_lag_state: Optional[str] = None                   # UNDERPRICED_RISK | OVERPRICED_RISK | CONFIRMED_STRESS | NORMAL
    structure_bias_reason: Optional[str] = None
    strike_width_multiplier: float = 1.0
    vol_size_multiplier: float = 1.0

    def to_dict(self) -> dict:
        d = asdict(self)
        # camelCase for JSON output
        return {
            "symbol": d["symbol"],
            "structure": d["structure"],
            "directionalLean": d["directional_lean"],
            "confidence": d["confidence"],
            "regimeContext": d["regime_context"],
            "leadLagSource": d["lead_lag_source"],
            "sourceDriver": d["source_driver"],
            "ivRank": d["iv_rank"],
            "expectedMove": d["expected_move"],
            "maxRiskEstimate": d["max_risk_estimate"],
            "rocEstimateModel": d["roc_estimate_model"],
            "rocAssumptions": d["roc_assumptions"],
            "notes": d["notes"],
            "suppressed": d["suppressed"],
            "invalidationStatus": d["invalidation_status"],
            "invalidationPriceLevel": d["invalidation_price_level"],
            "invalidationPriceDistancePct": d["invalidation_price_distance_pct"],
            "invalidationDeltaThreshold": d["invalidation_delta_threshold"],
            "invalidationDriverRule": d["invalidation_driver_rule"],
            "invalidationTestsTriggered": d["invalidation_tests_triggered"],
            "invalidationActions": d["invalidation_actions"],
            "globalVolScore": d["global_vol_score"],
            "usIvRankState": d["us_iv_rank_state"],
            "volLagState": d["vol_lag_state"],
            "structureBiasReason": d["structure_bias_reason"],
            "strikeWidthMultiplier": d["strike_width_multiplier"],
            "volSizeMultiplier": d["vol_size_multiplier"],
        }


@dataclass
class WeeklyIdea:
    generated_at: str
    week_label: str
    regime: Dict[str, Any]
    sector_biases: List[Dict[str, Any]]
    index_biases: List[Dict[str, Any]]
    trade_ideas: List[Dict[str, Any]]
    suppressions: List[Dict[str, Any]]
    global_signal_summary: Dict[str, Any]
    vol_leadlag: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        return {
            "week": self.week_label,
            "generatedAt": self.generated_at,
            "regime": self.regime,
            "globalSignalSummary": self.global_signal_summary,
            "volLeadLag": self.vol_leadlag,
            "sectorBiases": self.sector_biases,
            "indexBiases": self.index_biases,
            "tradeIdeas": self.trade_ideas,
            "suppressions": self.suppressions,
        }


# ---------------------------------------------------------------------------
# Source driver inference
# ---------------------------------------------------------------------------

_FX_KEYWORDS = {"audusd", "usdjpy", "eurusd", "forex", "fx", "currency", "dxy"}
_YIELD_KEYWORDS = {"yield", "bond", "treasury", "2s10s", "curve", "10y", "2y", "bund", "jgb", "gbond"}
_COMMODITY_KEYWORDS = {"oil", "gold", "copper", "uso", "gld", "cper", "commodity", "wti", "brent"}
_IV_KEYWORDS = {"iv", "vix", "volatility", "implied", "skew"}


def _infer_source_driver(sources: List[str]) -> str:
    """Infer the primary driver type from human-readable signal sources.

    Returns one of: yield | fx | commodity | iv | mixed
    """
    scores = {"yield": 0, "fx": 0, "commodity": 0, "iv": 0}
    text = " ".join(s.lower() for s in sources)

    for kw in _YIELD_KEYWORDS:
        if kw in text:
            scores["yield"] += 1
    for kw in _FX_KEYWORDS:
        if kw in text:
            scores["fx"] += 1
    for kw in _COMMODITY_KEYWORDS:
        if kw in text:
            scores["commodity"] += 1
    for kw in _IV_KEYWORDS:
        if kw in text:
            scores["iv"] += 1

    # Pick the highest-scoring driver, or "mixed" if tied/all zero
    max_score = max(scores.values())
    if max_score == 0:
        return "mixed"
    winners = [k for k, v in scores.items() if v == max_score]
    return winners[0] if len(winners) == 1 else "mixed"


# ---------------------------------------------------------------------------
# Structure selection
# ---------------------------------------------------------------------------


def _select_structure(direction: str, regime_label: str, allowed: List[str]) -> Optional[str]:
    """Select the best options structure given direction and regime."""
    if not allowed:
        return None

    if direction == "bullish":
        if "put_credit_spread" in allowed:
            return "put_credit_spread"
        if "iron_condor" in allowed:
            return "iron_condor"
    elif direction == "bearish":
        if "call_credit_spread" in allowed:
            return "call_credit_spread"
        if "iron_condor" in allowed:
            return "iron_condor"
    else:
        # Neutral -> iron condor if regime allows
        if "iron_condor" in allowed:
            return "iron_condor"
        if "put_credit_spread" in allowed:
            return "put_credit_spread"

    return allowed[0] if allowed else None


# ---------------------------------------------------------------------------
# ROC estimation
# ---------------------------------------------------------------------------


def _estimate_roc(
    structure: str,
    expected_move: Optional[float],
    iv_rank: Optional[float],
) -> tuple[Optional[str], Optional[Dict[str, Any]]]:
    """Produce a model ROC estimate with explicit assumptions.

    This is a MODEL ESTIMATE, not a guarantee. Assumptions are always returned.
    """
    if expected_move is None or expected_move <= 0:
        return None, None

    # Default assumptions for a weekly credit spread
    dte = 5
    width = 5.0  # $5 wide spread
    # Estimate credit as fraction of width based on IV rank
    credit_fraction = 0.12 if iv_rank is None else max(0.08, min(0.25, 0.08 + iv_rank * 0.17))
    credit_mid = round(width * credit_fraction, 2)
    max_loss = round(width - credit_mid, 2)
    roc_pct = round((credit_mid / max_loss) * 100, 1) if max_loss > 0 else 0.0

    estimate = f"{roc_pct}% on risk (model estimate)"
    assumptions = {
        "dte": dte,
        "creditMid": credit_mid,
        "width": width,
        "maxLoss": max_loss,
        "basis": "ORATS mid",
    }
    return estimate, assumptions


# ---------------------------------------------------------------------------
# Narrative builder
# ---------------------------------------------------------------------------


def _build_narrative(
    signals: List[dict],
    bars: List[dict],
    regime: Dict[str, Any],
) -> Dict[str, Any]:
    """Build a human-readable global signal summary."""
    active_leaders = set()
    confirming_leaders = set()
    for sig in signals:
        leader = sig.get("leader_symbol", "")
        active_leaders.add(leader)
        if sig.get("confirmation_count", 0) > 0:
            confirming_leaders.add(leader)

    # Dominant theme: most common direction
    directions = [s.get("direction") for s in signals if s.get("direction")]
    if directions:
        bullish_count = sum(1 for d in directions if d == "bullish")
        bearish_count = sum(1 for d in directions if d == "bearish")
        if bullish_count > bearish_count:
            theme = "Global cyclical strength"
        elif bearish_count > bullish_count:
            theme = "Global risk-off pressure"
        else:
            theme = "Mixed global signals"
    else:
        theme = "Insufficient data for theme"

    # Build narrative text from bars
    parts: List[str] = []
    for b in bars:
        sym = b.get("symbol", "")
        ret = b.get("return_1d_local")
        z = b.get("z_score_20d")
        if ret is not None:
            pct = f"{ret * 100:+.1f}%"
            z_str = f" (z={z:.1f})" if z is not None else ""
            parts.append(f"{sym} {pct}{z_str}")

    narrative = "; ".join(parts[:8])  # Limit to 8 most notable
    if not narrative:
        narrative = "Global data collected; see signals for details."

    return {
        "narrative": narrative,
        "leadersActive": len(active_leaders),
        "leadersConfirming": len(confirming_leaders),
        "dominantTheme": theme,
    }


# ---------------------------------------------------------------------------
# Suppression logic
# ---------------------------------------------------------------------------


def _check_suppressions(
    sector_biases: List[SectorBias],
    earnings_symbols: List[str],
    macro_event_flags: List[str],
    regime: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """Check for suppressions: earnings, macro events, regime stress."""
    suppressions: List[Dict[str, Any]] = []

    # Earnings-based suppressions
    for bias in sector_biases:
        if bias.sector in earnings_symbols:
            suppressions.append({
                "symbol": bias.sector,
                "reason": f"Earnings window active for {bias.sector}; suppress until post-report",
                "source": "benzinga_earnings_filter",
            })

    # Macro event suppressions
    for flag in macro_event_flags:
        suppressions.append({
            "symbol": "ALL",
            "reason": flag,
            "source": "benzinga_macro_filter",
        })

    # Regime suppressions
    if regime.get("label") == "Stressed":
        suppressions.append({
            "symbol": "ALL",
            "reason": "Regime is Stressed; all ideas suppressed",
            "source": "engine5_regime",
        })

    return suppressions


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def generate_weekly_ideas(
    *,
    date: str,
    signals: List[dict],
    regime: GlobalRegime,
    sector_biases: List[SectorBias],
    index_biases: List[IndexBias],
    bars: List[dict],
    orats_data: Optional[Dict[str, dict]] = None,
    earnings_symbols: Optional[List[str]] = None,
    macro_event_flags: Optional[List[str]] = None,
    yield_curve_series: Optional[List[float]] = None,
    stress_3d_changes: Optional[Dict[str, float]] = None,
    vol_leadlag: Optional[Any] = None,
) -> WeeklyIdea:
    """Generate the weekly idea output.

    Args:
        date: Date string YYYY-MM-DD.
        signals: List of LeadLagSignal dicts.
        regime: GlobalRegime instance.
        sector_biases: List of SectorBias from translation engine.
        index_biases: List of IndexBias from translation engine.
        bars: Today's GlobalAssetBar dicts.
        orats_data: {symbol: {"iv_rank": float, "expected_move": float, ...}}
        earnings_symbols: Symbols with earnings in the week window.
        macro_event_flags: Macro event warnings.
        yield_curve_series: Rolling 2s10s slope values for driver invalidation.
        stress_3d_changes: 3-day stress component changes for driver invalidation.
        vol_leadlag: Optional VolLeadLagResult from vol lead-lag module.
    """
    orats = orats_data or {}
    earnings = earnings_symbols or []
    macro_flags = macro_event_flags or []
    regime_dict = regime.to_dict()

    # Compute regime transition triggers using config thresholds
    from backend.config import get_flags as _get_flags
    _fl = _get_flags()
    triggers = compute_regime_triggers(
        regime,
        stressed_threshold=_fl.ENGINE5_REGIME_STRESSED_THRESHOLD,
        risk_off_threshold=_fl.ENGINE5_REGIME_RISK_OFF_THRESHOLD,
        transitional_threshold=_fl.ENGINE5_REGIME_TRANSITIONAL_THRESHOLD,
    )
    regime_dict["transitionTriggers"] = triggers.to_dict()

    # Vol lead-lag data (if available and not suppressed)
    has_vol = vol_leadlag is not None and not getattr(vol_leadlag, "suppressed", True)
    vol_score = getattr(vol_leadlag, "global_vol_score", None) if vol_leadlag else None
    vol_us_state = getattr(vol_leadlag, "us_iv_state", None) if vol_leadlag else None
    vol_state = getattr(vol_leadlag, "vol_lag_state", "NORMAL") if has_vol else None
    vol_struct_bias = getattr(vol_leadlag, "structure_bias", None) if has_vol else None
    vol_sw_mult = getattr(vol_leadlag, "strike_width_multiplier", 1.0) if has_vol else 1.0
    vol_sz_mult = getattr(vol_leadlag, "vol_size_multiplier", 1.0) if has_vol else 1.0

    # Week label
    try:
        d = dt.date.fromisoformat(date)
        week_label = f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
    except Exception:
        week_label = date

    # Suppressions
    suppressions = _check_suppressions(sector_biases, earnings, macro_flags, regime_dict)
    suppressed_symbols = {s["symbol"] for s in suppressions}

    # Generate trade ideas from sector biases
    trade_ideas: List[Dict[str, Any]] = []
    for bias in sector_biases:
        if bias.confidence < 30:
            continue  # Too weak

        # Select structure (vol-aware: prefer iron condors for UNDERPRICED_RISK,
        # aggressive PCS/CCS for OVERPRICED_RISK)
        vol_adjusted_direction = bias.direction
        if has_vol and vol_state == "UNDERPRICED_RISK" and bias.direction != "bearish":
            vol_adjusted_direction = "neutral"  # Steers toward iron condors
        elif has_vol and vol_state == "OVERPRICED_RISK":
            vol_adjusted_direction = bias.direction  # Keep directional, aggressive PCS/CCS

        structure = _select_structure(
            vol_adjusted_direction,
            regime.label,
            regime.allowed_structures,
        )
        if structure is None:
            continue

        # ORATS data for this symbol
        sym_orats = orats.get(bias.sector, {})
        iv_rank = sym_orats.get("iv_rank")
        expected_move = sym_orats.get("expected_move")
        sym_close = sym_orats.get("close")
        sym_delta = sym_orats.get("delta_short")
        sym_iv = sym_orats.get("iv")
        sym_dte = sym_orats.get("dte")
        sym_k_short = sym_orats.get("k_short")

        # Infer source driver from the bias's signal sources
        source_driver = _infer_source_driver(bias.sources)

        # ROC estimate
        roc_est, roc_assumptions = _estimate_roc(structure, expected_move, iv_rank)

        # Notes
        notes: List[str] = []
        if regime.label == "Risk-On":
            notes.append("Regime allows full position sizing")
        elif regime.label == "Transitional":
            notes.append("Transitional regime; reduced position sizing (0.75x)")
        elif regime.label == "Risk-Off":
            notes.append("Risk-Off regime; reduced position sizing (0.50x)")

        if bias.sector not in earnings:
            notes.append("No earnings in window")
        else:
            notes.append(f"CAUTION: {bias.sector} has earnings this week")

        # Vol lead-lag notes
        if has_vol and vol_state and vol_state != "NORMAL":
            if vol_state == "UNDERPRICED_RISK":
                notes.append("Global vol rising while US IV neutral/low — wider strikes recommended")
            elif vol_state == "OVERPRICED_RISK":
                notes.append("US IV overpricing risk — vol decay edge, aggressive premium selling")
            elif vol_state == "CONFIRMED_STRESS":
                notes.append("Confirmed global stress — very wide IC or consider no trade")

        # Suppression check
        is_suppressed = (
            bias.sector in suppressed_symbols
            or "ALL" in suppressed_symbols
        )

        # --- Invalidation ---
        inv_status = "VALID"
        inv_price_level = None
        inv_price_dist = None
        inv_delta_thresh = None
        inv_driver_rule = None
        inv_tests: List[str] = []
        inv_actions: List[str] = ["Idea remains valid."]

        try:
            inv_result = compute_invalidation_for_idea(
                S0=sym_close,
                EM=expected_move,
                IV=sym_iv,
                DTE=sym_dte,
                K_short=sym_k_short,
                delta_short=sym_delta,
                structure=structure,
                regime_label=regime.label,
                source_driver=source_driver,
                regime_components=regime.components,
                total_score=regime.score,
                yield_curve_series=yield_curve_series,
                stress_3d_changes=stress_3d_changes,
            )
            inv_status = inv_result.status
            inv_price_level = inv_result.invalidation_price_level
            inv_price_dist = inv_result.invalidation_price_distance_pct
            inv_delta_thresh = inv_result.invalidation_delta_threshold
            inv_driver_rule = inv_result.invalidation_driver_rule
            inv_tests = inv_result.tests_triggered
            inv_actions = inv_result.actions
        except Exception as e:
            LOG.warning("Invalidation computation failed for %s: %s", bias.sector, e)
            notes.append("Invalidation check unavailable")

        idea = TradeIdea(
            symbol=bias.sector,
            structure=structure,
            directional_lean=bias.direction,
            confidence=bias.confidence,
            regime_context=regime.label,
            lead_lag_source="; ".join(bias.sources[:3]),
            source_driver=source_driver,
            iv_rank=round(iv_rank, 2) if iv_rank is not None else None,
            expected_move=round(expected_move, 2) if expected_move is not None else None,
            max_risk_estimate=f"${roc_assumptions['maxLoss'] * 100:.0f} per spread" if roc_assumptions else None,
            roc_estimate_model=roc_est,
            roc_assumptions=roc_assumptions,
            notes=notes,
            suppressed=is_suppressed,
            invalidation_status=inv_status,
            invalidation_price_level=inv_price_level,
            invalidation_price_distance_pct=inv_price_dist,
            invalidation_delta_threshold=inv_delta_thresh,
            invalidation_driver_rule=inv_driver_rule,
            invalidation_tests_triggered=inv_tests,
            invalidation_actions=inv_actions,
            global_vol_score=round(vol_score, 4) if vol_score is not None else None,
            us_iv_rank_state=vol_us_state,
            vol_lag_state=vol_state,
            structure_bias_reason=vol_struct_bias,
            strike_width_multiplier=vol_sw_mult,
            vol_size_multiplier=vol_sz_mult,
        )
        trade_ideas.append(idea.to_dict())

    # Narrative
    narrative = _build_narrative(signals, bars, regime_dict)

    return WeeklyIdea(
        generated_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        week_label=week_label,
        regime=regime_dict,
        sector_biases=[b.to_dict() for b in sector_biases],
        index_biases=[b.to_dict() for b in index_biases],
        trade_ideas=trade_ideas,
        suppressions=suppressions,
        global_signal_summary=narrative,
        vol_leadlag=vol_leadlag.to_dict() if vol_leadlag is not None else None,
    )
