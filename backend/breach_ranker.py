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
    (80, "slamDunk", "Slam Dunk"),
    (65, "strong", "Strong"),
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


def score_liquidity(
    dollar_vol: Optional[float],
    spread_coverage: Optional[float] = None,
) -> Dict[str, Any]:
    """
    Score tradability based on volume and option spreads.
    
    Underlying thresholds:
    - $500M+: Excellent (100)
    - $200-500M: Good (80)
    - $100-200M: OK (60)
    - $50-100M: Marginal (45)
    - <$50M: Poor (30)
    
    Option spread coverage (if available):
    - >80%: +0
    - 60-80%: -10
    - <60%: -20
    """
    if dollar_vol is None:
        return {
            "score": 50,
            "value": None,
            "label": "N/A",
            "status": "ok",
        }
    
    vol = float(dollar_vol)
    vol_millions = vol / 1_000_000
    
    if vol_millions >= 500:
        base_score = 100
    elif vol_millions >= 200:
        base_score = 80
    elif vol_millions >= 100:
        base_score = 60
    elif vol_millions >= 50:
        base_score = 45
    else:
        base_score = 30
    
    # Apply spread coverage adjustment if available
    adjustment = 0
    if spread_coverage is not None:
        cov = float(spread_coverage)
        if cov < 60:
            adjustment = -20
        elif cov < 80:
            adjustment = -10
    
    score = max(20, base_score + adjustment)
    
    # Format label
    if vol_millions >= 1000:
        label = f"${vol_millions/1000:.1f}B"
    else:
        label = f"${vol_millions:.0f}M"
    
    return {
        "score": score,
        "value": vol,
        "label": label,
        "status": _get_status(score),
    }


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
    elif gamma_check.get("result") == "FAIL":
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
    if legal_reg_check and legal_reg_check.get("result") == "FAIL":
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
    
    # Convert list to dict keyed by check name
    checks = {}
    for check in checks_list:
        if isinstance(check, dict) and "check" in check:
            checks[check["check"]] = check
    
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


def _extract_liquidity(payload: Dict[str, Any]) -> Tuple[Optional[float], Optional[float]]:
    """Extract dollar volume and spread coverage."""
    checks = _extract_go_no_go_checks(payload)
    liq_check = checks.get("SN_LIQUIDITY", {})
    detail = liq_check.get("detail", {})
    
    dollar_vol = detail.get("avgDollarVol20d")
    spread_cov = detail.get("spreadCoverage")
    
    return dollar_vol, spread_cov


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
    # Extract metrics from payload
    summary = breach_payload.get("summary", {})
    breach_pct = summary.get("breachRatePct")
    
    iv30_pct, iv30_abs = _extract_iv_metrics(breach_payload)
    em_to_median = _extract_em_richness(breach_payload)
    em_to_p90 = _extract_tail_coverage(breach_payload)
    dollar_vol, spread_cov = _extract_liquidity(breach_payload)
    macro_checks = _extract_macro_checks(breach_payload)
    legal_check, bz_score = _extract_event_risk(breach_payload)
    
    # Score each factor
    factors = {
        "breachRate": score_breach_rate(breach_pct),
        "ivElevation": score_iv_elevation(iv30_pct, iv30_abs),
        "emRichness": score_em_richness(em_to_median),
        "liquidity": score_liquidity(dollar_vol, spread_cov),
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
    tier_id, tier_label = _get_tier(composite)
    
    return {
        "score": composite,
        "tier": tier_id,
        "tierLabel": tier_label,
        "factors": factors,
    }


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
            
            # Extract quick stats for display
            summary = payload.get("summary", {})
            current = payload.get("current", {})
            
            results.append({
                "ticker": ticker,
                "compositeScore": scoring["score"],
                "tier": scoring["tier"],
                "tierLabel": scoring["tierLabel"],
                "factors": scoring["factors"],
                "quickStats": {
                    "breachRatePct": summary.get("breachRatePct"),
                    "eventsUsed": summary.get("eventsUsed"),
                    "impliedMovePct": current.get("impliedMovePct"),
                },
                "fullPayload": payload,
            })
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
