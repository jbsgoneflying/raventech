"""
Engine 1 Ticker Ranking: Composite Scoring Module

Ranks multiple tickers for earnings plays based on weighted factor analysis.
Does NOT use the binary GO/NO-GO flag - instead scores each underlying factor
individually on a gradient scale.

Factors and Weights:
- Breach Rate (25%): Historical breach frequency
- IV Elevation (20%): Premium richness (IV30 percentile)
- EM Richness (15%): Expected move vs historical realized
- Liquidity (15%): Dollar volume + option spread quality
- Tail Coverage (10%): EM coverage of P90 realized
- Market Regime (10%): SPX gamma, RV, forced flows
- Event Risk (5%): Legal/regulatory events
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger("breach_ranker")

# ---------------------------------------------------------------------------
# Factor Weights (must sum to 1.0)
# ---------------------------------------------------------------------------

WEIGHTS = {
    "breachRate": 0.25,
    "ivElevation": 0.20,
    "emRichness": 0.15,
    "liquidity": 0.15,
    "tailCoverage": 0.10,
    "marketRegime": 0.10,
    "eventRisk": 0.05,
}

# ---------------------------------------------------------------------------
# Tier Thresholds
# ---------------------------------------------------------------------------

TIER_THRESHOLDS = [
    (90, "slamDunk", "Slam Dunk"),
    (79, "strong", "Strong"),
    (50, "standard", "Standard"),
    (35, "caution", "Caution"),
    (0, "avoid", "Avoid"),
]


def _get_tier(score: float) -> Tuple[str, str]:
    """Get tier ID and label for a given score."""
    for threshold, tier_id, tier_label in TIER_THRESHOLDS:
        if score >= threshold:
            return tier_id, tier_label
    return "avoid", "Avoid"


def _get_status(score: float) -> str:
    """Get status indicator based on score."""
    if score >= 70:
        return "good"
    elif score >= 40:
        return "ok"
    else:
        return "poor"


# ---------------------------------------------------------------------------
# Individual Factor Scoring Functions
# ---------------------------------------------------------------------------

def score_breach_rate(breach_pct: Optional[float]) -> Dict[str, Any]:
    """
    Score breach rate on 0-100 scale (lower breach = higher score).
    
    Thresholds:
    - <15%: Excellent (100)
    - 15-20%: Very Good (85)
    - 20-25%: Good (70)
    - 25-35%: Acceptable (55)
    - 35-45%: Marginal (40)
    - >45%: Poor (20)
    """
    if breach_pct is None:
        return {
            "score": 50,
            "value": None,
            "label": "N/A",
            "status": "ok",
        }
    
    pct = float(breach_pct)
    
    if pct < 15:
        score = 100
    elif pct < 20:
        score = 85
    elif pct < 25:
        score = 70
    elif pct < 35:
        score = 55
    elif pct < 45:
        score = 40
    else:
        score = 20
    
    return {
        "score": score,
        "value": round(pct, 1),
        "label": f"{pct:.1f}%",
        "status": _get_status(score),
    }


def score_iv_elevation(iv30_pct: Optional[float], iv30_abs: Optional[float] = None) -> Dict[str, Any]:
    """
    Score IV percentile (higher IV = higher score).
    Uses IV30 percentile directly as score with floor at 20.
    """
    if iv30_pct is None:
        return {
            "score": 50,
            "value": None,
            "label": "N/A",
            "status": "ok",
        }
    
    pct = float(iv30_pct)
    # Use percentile directly, with floor at 20
    score = max(20, min(100, pct))
    
    return {
        "score": score,
        "value": round(pct, 0),
        "label": f"{pct:.0f}th pctl",
        "status": _get_status(score),
    }


def score_em_richness(em_to_median: Optional[float]) -> Dict[str, Any]:
    """
    Score expected move richness vs historical realized median.
    
    Thresholds:
    - 1.20x+: Excellent (100)
    - 1.15x: Very Good (90)
    - 1.10x: Good (80)
    - 1.05x: OK (70)
    - 1.00x: Marginal (60)
    - 0.95x: Poor (45)
    - <0.90x: Very Poor (30)
    """
    if em_to_median is None:
        return {
            "score": 50,
            "value": None,
            "label": "N/A",
            "status": "ok",
        }
    
    ratio = float(em_to_median)
    
    if ratio >= 1.20:
        score = 100
    elif ratio >= 1.15:
        score = 90
    elif ratio >= 1.10:
        score = 80
    elif ratio >= 1.05:
        score = 70
    elif ratio >= 1.00:
        score = 60
    elif ratio >= 0.95:
        score = 45
    else:
        score = 30
    
    return {
        "score": score,
        "value": round(ratio, 2),
        "label": f"{ratio:.2f}x",
        "status": _get_status(score),
    }


def _vol_label(dollar_vol: Optional[float]) -> str:
    """Format dollar volume as human-readable label."""
    if dollar_vol is None:
        return "Vol N/A"
    vol_m = float(dollar_vol) / 1_000_000
    if vol_m >= 1000:
        return f"${vol_m/1000:.1f}B"
    return f"${vol_m:.0f}M"


def _vol_base_score(dollar_vol: Optional[float]) -> Tuple[float, float]:
    """Return (base_score, vol_millions) from underlying dollar volume."""
    if dollar_vol is None:
        return 40.0, 0.0
    vol_m = float(dollar_vol) / 1_000_000
    if vol_m >= 500:
        return 100.0, vol_m
    if vol_m >= 200:
        return 80.0, vol_m
    if vol_m >= 100:
        return 60.0, vol_m
    if vol_m >= 50:
        return 45.0, vol_m
    return 30.0, vol_m


def score_liquidity(liq_data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Score tradability based on underlying volume AND option liquidity.

    Three-tier severity from goNoGo:
    - BLOCK: genuinely illiquid — hard cap at 15 (Avoid tier)
    - FLAG:  execution concern — penalize but let composite speak (cap 55)
    - PASS:  clean — score on gradient from dollar vol + option metrics
    - MISSING: can't verify — cap at 30
    """
    if not isinstance(liq_data, dict):
        return {
            "score": 30,
            "value": None,
            "label": "N/A",
            "status": "poor",
            "warning": "No liquidity data",
        }

    state = liq_data.get("state", "MISSING")
    dollar_vol = liq_data.get("avgDollarVol20d")
    spread_cov = liq_data.get("spreadCoverage")
    put_oi = liq_data.get("putOI", 0) or 0
    call_oi = liq_data.get("callOI", 0) or 0
    total_oi = float(put_oi) + float(call_oi)

    # BLOCK: genuinely no executable market
    if state == "BLOCK":
        label = _vol_label(dollar_vol) + " - BLOCK"
        return {
            "score": 15,
            "value": dollar_vol,
            "label": label,
            "status": "poor",
            "warning": "No executable options market",
        }

    # FAIL (legacy compat): treat same as BLOCK
    if state == "FAIL":
        label = _vol_label(dollar_vol) + " - FAIL"
        return {
            "score": 15,
            "value": dollar_vol,
            "label": label,
            "status": "poor",
            "warning": "No executable options market",
        }

    # MISSING with no dollar vol: can't verify
    if state == "MISSING" and dollar_vol is None:
        return {
            "score": 30,
            "value": None,
            "label": "N/A - Verify manually",
            "status": "poor",
            "warning": "Liquidity data unavailable",
        }

    # Calculate base score from underlying volume
    base_score, vol_millions = _vol_base_score(dollar_vol)

    # Apply option spread coverage adjustment
    adjustment = 0
    if spread_cov is not None:
        cov = float(spread_cov)
        if cov < 60:
            adjustment = -25
        elif cov < 80:
            adjustment = -10

    if total_oi < 500 and total_oi > 0:
        adjustment -= 20
    elif total_oi < 2000 and total_oi > 0:
        adjustment -= 10

    score = max(15, base_score + adjustment)

    # FLAG: penalize but don't kill — cap at 55 so composite still works
    if state == "FLAG":
        score = max(30, min(score, 55))
        label = _vol_label(dollar_vol) + " ⚑"
        result: Dict[str, Any] = {
            "score": score,
            "value": dollar_vol,
            "label": label,
            "status": _get_status(score),
            "warning": "Liquidity flag — verify execution quality",
        }
        return result

    label = _vol_label(dollar_vol)
    if state == "PASS":
        label += " ✓"

    result = {
        "score": score,
        "value": dollar_vol,
        "label": label,
        "status": _get_status(score),
    }

    if score < 40:
        result["warning"] = "Low liquidity - execution risk"

    return result


def score_tail_coverage(em_to_p90: Optional[float]) -> Dict[str, Any]:
    """
    Score expected move coverage of P90 realized (tail risk protection).
    
    Thresholds:
    - >1.0x: Excellent - EM covers tail (100)
    - 0.90-1.0x: Good (80)
    - 0.80-0.90x: OK (65)
    - 0.70-0.80x: Marginal (50)
    - <0.70x: Poor - tail risk (35)
    """
    if em_to_p90 is None:
        return {
            "score": 50,
            "value": None,
            "label": "N/A",
            "status": "ok",
        }
    
    ratio = float(em_to_p90)
    
    if ratio >= 1.0:
        score = 100
    elif ratio >= 0.90:
        score = 80
    elif ratio >= 0.80:
        score = 65
    elif ratio >= 0.70:
        score = 50
    else:
        score = 35
    
    return {
        "score": score,
        "value": round(ratio, 2),
        "label": f"{ratio:.2f}x P90",
        "status": _get_status(score),
    }


def score_market_regime(macro_checks: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Score macro backdrop from individual MACRO_* checks.
    
    Points:
    - SPX Gamma positive: +40
    - No gamma flip nearby: +30
    - RV not accelerating: +20
    - No forced flows: +10
    """
    if macro_checks is None:
        return {
            "score": 50,
            "value": None,
            "label": "N/A",
            "status": "ok",
        }
    
    score = 0
    labels = []
    
    # SPX Gamma positive
    gamma_check = macro_checks.get("MACRO_GAMMA", {})
    if gamma_check.get("result") == "PASS":
        score += 40
        labels.append("Gamma+")
    elif gamma_check.get("result") in ("FAIL", "FLAG", "BLOCK"):
        labels.append("Gamma-")
    
    # No gamma flip nearby
    flip_check = macro_checks.get("MACRO_GAMMA_FLIP", {})
    if flip_check.get("result") == "PASS":
        score += 30
    else:
        labels.append("Flip risk")
    
    # RV not accelerating
    rv_check = macro_checks.get("MACRO_RV_ACCEL", {})
    if rv_check.get("result") == "PASS":
        score += 20
    else:
        labels.append("RV accel")
    
    # No forced flows
    flows_check = macro_checks.get("MACRO_FORCED_FLOWS", {})
    if flows_check.get("result") == "PASS":
        score += 10
    else:
        labels.append("Flows")
    
    label = ", ".join(labels) if labels else "Clear"
    
    return {
        "score": score,
        "value": None,
        "label": label,
        "status": _get_status(score),
    }


def score_event_risk(
    legal_reg_check: Optional[Dict[str, Any]],
    benzinga_score: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Score binary event exposure.
    
    - No events: 100
    - Minor events (benzinga < 0.3): 70
    - Moderate events (benzinga 0.3-0.6): 50
    - Major events (benzinga > 0.6 or legal_reg FAIL): 20
    """
    has_legal_event = False
    if legal_reg_check and legal_reg_check.get("result") in ("FAIL", "FLAG", "BLOCK"):
        has_legal_event = True
    
    bz = benzinga_score if benzinga_score is not None else 0
    
    if has_legal_event or bz > 0.6:
        score = 20
        label = "Major event"
    elif bz > 0.3:
        score = 50
        label = "Moderate event"
    elif bz > 0.1:
        score = 70
        label = "Minor event"
    else:
        score = 100
        label = "No events"
    
    return {
        "score": score,
        "value": bz if bz > 0 else None,
        "label": label,
        "status": _get_status(score),
    }


# ---------------------------------------------------------------------------
# Composite Scoring
# ---------------------------------------------------------------------------

def _extract_go_no_go_checks(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract individual checks from goNoGo payload."""
    go_no_go = payload.get("goNoGo", {})
    checks_list = go_no_go.get("checks", [])
    
    # Convert list to dict keyed by check ID
    # Note: goNoGo uses 'id' and 'state', not 'check' and 'result'
    checks = {}
    for check in checks_list:
        if isinstance(check, dict):
            check_id = check.get("id")
            if check_id:
                # Normalize to use 'result' for backwards compatibility
                normalized = dict(check)
                normalized["result"] = check.get("state")  # Map state -> result
                checks[check_id] = normalized
    
    return checks


def _extract_iv_metrics(payload: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Extract IV30 percentile and absolute value."""
    checks = _extract_go_no_go_checks(payload)
    iv_check = checks.get("SN_IV_ELEVATED", {})
    detail = iv_check.get("detail", {})
    
    iv30_pct = detail.get("iv30Pct")
    iv30_abs = detail.get("iv30")
    
    return iv30_pct, iv30_abs


def _extract_em_richness(payload: Dict[str, Any]) -> Optional[float]:
    """Extract EM to median ratio."""
    checks = _extract_go_no_go_checks(payload)
    em_check = checks.get("SN_EM_RICHNESS", {})
    detail = em_check.get("detail", {})
    
    return detail.get("emToMedian")


def _extract_tail_coverage(payload: Dict[str, Any]) -> Optional[float]:
    """Extract EM to P90 ratio."""
    checks = _extract_go_no_go_checks(payload)
    tail_check = checks.get("SN_TAIL_P90_RICHNESS", {})
    detail = tail_check.get("detail", {})
    
    return detail.get("emToP90")


def _extract_liquidity(payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Extract comprehensive liquidity data from goNoGo.checks.
    
    Returns dict with:
    - avgDollarVol20d: underlying dollar volume
    - spreadCoverage: option spread quality
    - optLiquidityOk: bool from delta band checks
    - underlyingOk: bool from underlying volume check
    - state: PASS/FLAG/BLOCK/MISSING
    """
    checks = _extract_go_no_go_checks(payload)
    liq_check = checks.get("SN_LIQUIDITY", {})
    
    # Get the result state
    state = liq_check.get("result", "MISSING")
    
    # Get value dict (where detailed liquidity lives)
    value = liq_check.get("value", {})
    if not isinstance(value, dict):
        value = {}
    
    # Extract key metrics
    dollar_vol = value.get("avgDollarVol20d")
    underlying_ok = value.get("avgDollarVolOk", False)
    
    # Delta band aggregates (put/call liquidity)
    delta_band = value.get("deltaBandAgg", {})
    put_band = delta_band.get("put", {}) if isinstance(delta_band, dict) else {}
    call_band = delta_band.get("call", {}) if isinstance(delta_band, dict) else {}
    
    # Coverage = fraction of strikes with valid quotes
    put_cov = put_band.get("coverage") if isinstance(put_band, dict) else None
    call_cov = call_band.get("coverage") if isinstance(call_band, dict) else None
    
    # Aggregate OI and Volume
    put_oi = put_band.get("sumOI", 0) if isinstance(put_band, dict) else 0
    call_oi = call_band.get("sumOI", 0) if isinstance(call_band, dict) else 0
    put_vol = put_band.get("sumVol", 0) if isinstance(put_band, dict) else 0
    call_vol = call_band.get("sumVol", 0) if isinstance(call_band, dict) else 0
    
    # Compute average spread coverage
    spread_cov = None
    if put_cov is not None and call_cov is not None:
        spread_cov = (float(put_cov) + float(call_cov)) / 2.0 * 100  # Convert to percentage
    
    return {
        "avgDollarVol20d": dollar_vol,
        "underlyingOk": underlying_ok,
        "spreadCoverage": spread_cov,
        "putOI": put_oi,
        "callOI": call_oi,
        "putVol": put_vol,
        "callVol": call_vol,
        "state": state,
    }


def _extract_macro_checks(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Extract MACRO_* checks."""
    checks = _extract_go_no_go_checks(payload)
    
    return {
        k: v for k, v in checks.items()
        if k.startswith("MACRO_")
    }


def _extract_event_risk(payload: Dict[str, Any]) -> Tuple[Optional[Dict], Optional[float]]:
    """Extract legal/reg check and Benzinga score."""
    checks = _extract_go_no_go_checks(payload)
    legal_check = checks.get("SN_LEGAL_REG")
    
    # Benzinga score if available
    event_risk = payload.get("eventRisk", {})
    bz_score = event_risk.get("compositeScore")
    
    return legal_check, bz_score


def compute_composite_score(breach_payload: Dict[str, Any]) -> Dict[str, Any]:
    """
    Compute weighted composite score from Engine 1 breach payload.
    
    Returns:
        {
            score: float (0-100),
            tier: str (slamDunk, strong, standard, caution, avoid),
            tierLabel: str,
            factors: {
                breachRate: {score, value, label, status},
                ivElevation: {...},
                emRichness: {...},
                liquidity: {...},
                tailCoverage: {...},
                marketRegime: {...},
                eventRisk: {...}
            }
        }
    """
    # -----------------------------------------------------------------------
    # EXTRACT METRICS - Try multiple paths for robustness
    # -----------------------------------------------------------------------
    
    # 1. Breach rate: summary.breach_rate_pct or baseline.breach_rate_pct
    summary = breach_payload.get("summary", {})
    baseline = breach_payload.get("baseline", {})
    breach_pct = (
        summary.get("breach_rate_pct") or 
        summary.get("breachRatePct") or 
        baseline.get("breach_rate_pct")
    )
    
    # 2. IV elevation: regime.inputs.tickerIv30Percentile (0-1 scale, convert to 0-100)
    # Or try goNoGo.checks path
    regime = breach_payload.get("regime", {})
    regime_inputs = regime.get("inputs", {})
    iv30_pct_raw = regime_inputs.get("tickerIv30Percentile")
    iv30_pct = iv30_pct_raw * 100 if iv30_pct_raw is not None else None
    iv30_abs = regime_inputs.get("tickerIv30")
    
    # Fallback to goNoGo if available
    if iv30_pct is None:
        iv30_pct, iv30_abs = _extract_iv_metrics(breach_payload)
    
    # 3. EM Richness: baseline.avg_ratio_realized_to_implied (inverted - we want EM > realized)
    # Also try goNoGo path
    avg_ratio = baseline.get("avg_ratio_realized_to_implied")
    if avg_ratio is not None and avg_ratio > 0:
        # If realized/implied < 1.0, EM is rich. Convert so higher = better.
        em_to_median = 1.0 / avg_ratio if avg_ratio > 0 else None
    else:
        em_to_median = _extract_em_richness(breach_payload)
    
    # 4. Tail Coverage: Try goNoGo path or estimate from summary
    em_to_p90 = _extract_tail_coverage(breach_payload)
    
    # 5. Liquidity: Extract comprehensive data from goNoGo (CRITICAL GATE)
    liq_data = _extract_liquidity(breach_payload)
    
    # 6. Market Regime: Use regime.label and scores
    regime_label = regime.get("label", "")
    regime_score_raw = regime.get("scores", {}).get("regimeScore")
    
    # Convert regime to macro checks format OR score directly
    if regime_label:
        macro_checks = _regime_to_macro_checks(regime_label, regime_score_raw)
    else:
        macro_checks = _extract_macro_checks(breach_payload)
    
    # 7. Event Risk: goNoGo path
    legal_check, bz_score = _extract_event_risk(breach_payload)
    
    # -----------------------------------------------------------------------
    # SCORE EACH FACTOR
    # -----------------------------------------------------------------------
    factors = {
        "breachRate": score_breach_rate(breach_pct),
        "ivElevation": score_iv_elevation(iv30_pct, iv30_abs),
        "emRichness": score_em_richness(em_to_median),
        "liquidity": score_liquidity(liq_data),
        "tailCoverage": score_tail_coverage(em_to_p90),
        "marketRegime": score_market_regime(macro_checks if macro_checks else None),
        "eventRisk": score_event_risk(legal_check, bz_score),
    }
    
    # Compute weighted composite score
    composite = 0.0
    for factor_name, weight in WEIGHTS.items():
        factor_score = factors.get(factor_name, {}).get("score", 50)
        composite += factor_score * weight
    
    composite = round(composite, 1)
    
    # Check for liquidity warning - this is a HARD GATE
    liq_score = factors.get("liquidity", {}).get("score", 50)
    liq_warning = factors.get("liquidity", {}).get("warning")
    
    # Only force Avoid for BLOCK (score <= 15). FLAG and everything else
    # let the composite score determine tier naturally.
    if liq_score <= 15:
        tier_id, tier_label = "avoid", "Avoid"
        liq_warning = liq_warning or "No executable options market"
    else:
        tier_id, tier_label = _get_tier(composite)
    
    result = {
        "score": composite,
        "tier": tier_id,
        "tierLabel": tier_label,
        "factors": factors,
    }
    
    if liq_warning:
        result["liquidityWarning"] = liq_warning
        result["liquidityBlock"] = liq_score <= 15

    return result


def _regime_to_macro_checks(regime_label: str, regime_score: Optional[float]) -> Dict[str, Any]:
    """
    Convert regime label/score to macro checks format for scoring.
    
    Normal (regimeScore < 0.4): All PASS
    Elevated (0.4-0.7): Partial PASS
    Stress (> 0.7): Mostly FAIL
    """
    label = (regime_label or "").lower()
    score = regime_score or 0.5
    
    checks = {}
    
    if label == "normal" or score < 0.4:
        # All clear
        checks["MACRO_GAMMA"] = {"result": "PASS"}
        checks["MACRO_GAMMA_FLIP"] = {"result": "PASS"}
        checks["MACRO_RV_ACCEL"] = {"result": "PASS"}
        checks["MACRO_FORCED_FLOWS"] = {"result": "PASS"}
    elif label == "elevated" or score < 0.7:
        # Mixed
        checks["MACRO_GAMMA"] = {"result": "PASS"}
        checks["MACRO_GAMMA_FLIP"] = {"result": "PASS"}
        checks["MACRO_RV_ACCEL"] = {"result": "FLAG"}
        checks["MACRO_FORCED_FLOWS"] = {"result": "PASS"}
    else:
        # Stress
        checks["MACRO_GAMMA"] = {"result": "FLAG"}
        checks["MACRO_GAMMA_FLIP"] = {"result": "FLAG"}
        checks["MACRO_RV_ACCEL"] = {"result": "FLAG"}
        checks["MACRO_FORCED_FLOWS"] = {"result": "FLAG"}
    
    return checks


def rank_tickers(payloads: List[Tuple[str, Dict[str, Any]]]) -> List[Dict[str, Any]]:
    """
    Score and rank multiple tickers.
    
    Args:
        payloads: List of (ticker, breach_payload) tuples
    
    Returns:
        Sorted list of ranked ticker results (best to worst)
    """
    results = []
    
    for ticker, payload in payloads:
        try:
            scoring = compute_composite_score(payload)
            
            # Extract quick stats for display (try both naming conventions)
            summary = payload.get("summary", {})
            baseline = payload.get("baseline", {})
            current = payload.get("current", {})
            expected_move = payload.get("expectedMove", {})
            next_event = payload.get("nextEvent", {})
            events = payload.get("events", [])
            
            breach_pct = (
                summary.get("breach_rate_pct") or 
                summary.get("breachRatePct") or
                baseline.get("breach_rate_pct")
            )
            events_used = (
                summary.get("events_used") or 
                summary.get("eventsUsed") or
                baseline.get("events_used")
            )
            
            # ORATS EM (impErnMv) - used for historical earnings event calculations
            # Try multiple fallback sources:
            # 1. current.impliedMovePct (from ORATS cores snapshot)
            # 2. current.impErnMv (raw value)
            # 3. nextEvent.impliedMovePctPlanned (if Monte Carlo enabled)
            # 4. summary.avg_implied_all_pct (average from historical events)
            # 5. Most recent event's impliedMovePct
            orats_em = None
            if current.get("impliedMovePct") is not None:
                orats_em = current.get("impliedMovePct")
            elif current.get("impErnMv") is not None:
                orats_em = current.get("impErnMv")
            elif next_event.get("impliedMovePctPlanned") is not None:
                orats_em = next_event.get("impliedMovePctPlanned")
            elif summary.get("avg_implied_all_pct") is not None:
                orats_em = summary.get("avg_implied_all_pct")
            elif events and isinstance(events, list) and len(events) > 0:
                # Get most recent event's implied move
                for evt in events:
                    if isinstance(evt, dict) and evt.get("impliedMovePct") is not None:
                        orats_em = evt.get("impliedMovePct")
                        break
            
            # Straddle EM (ATM-forward straddle calculation)
            straddle_em = expected_move.get("expectedMovePct") if isinstance(expected_move, dict) else None
            
            result_entry = {
                "ticker": ticker,
                "compositeScore": scoring["score"],
                "tier": scoring["tier"],
                "tierLabel": scoring["tierLabel"],
                "factors": scoring["factors"],
                "quickStats": {
                    "breachRatePct": breach_pct,
                    "eventsUsed": events_used,
                    "impliedMovePct": orats_em,  # Keep for backward compat
                    "oratsEmPct": orats_em,      # ORATS implied earnings move
                    "straddleEmPct": straddle_em,  # ATM-forward straddle EM
                },
                "fullPayload": payload,
            }
            
            # Add liquidity warning if present
            if scoring.get("liquidityWarning"):
                result_entry["liquidityWarning"] = scoring["liquidityWarning"]
            
            results.append(result_entry)
        except Exception as e:
            LOG.warning(f"Failed to score {ticker}: {e}")
            results.append({
                "ticker": ticker,
                "compositeScore": 0,
                "tier": "avoid",
                "tierLabel": "Error",
                "factors": {},
                "quickStats": {},
                "error": str(e),
            })
    
    # Sort by composite score (descending)
    results.sort(key=lambda x: x["compositeScore"], reverse=True)
    
    # Assign ranks
    for i, result in enumerate(results):
        result["rank"] = i + 1
    
    return results


def summarize_tiers(rankings: List[Dict[str, Any]]) -> Dict[str, int]:
    """Count tickers in each tier."""
    summary = {
        "slamDunk": 0,
        "strong": 0,
        "standard": 0,
        "caution": 0,
        "avoid": 0,
    }
    
    for r in rankings:
        tier = r.get("tier", "avoid")
        if tier in summary:
            summary[tier] += 1
    
    return summary
