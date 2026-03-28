"""Engine 1 — Earnings Vol-Crush VRP Engine.

Computes:
  1. Earnings Variance Risk Premium (VRP) score from historical implied/realized ratios
  2. EM x Wing Width backtest grid (single-name earnings IC)
  3. Entry quality score from current market data
  4. Deterministic desk consensus (TRADE / LEAN_PASS / PASS)

All inputs come from the existing Engine 1 ``compute_breach_stats`` payload —
no new data sources are required.
"""
from __future__ import annotations

import logging
import math
import statistics
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


def _f(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _round2(v: Optional[float]) -> Optional[float]:
    return round(v, 2) if v is not None else None


def _round3(v: Optional[float]) -> Optional[float]:
    return round(v, 3) if v is not None else None


# ---------------------------------------------------------------------------
# 1. VRP Score
# ---------------------------------------------------------------------------

def compute_vrp_score(
    events: List[Dict[str, Any]],
    *,
    current_implied_move_pct: Optional[float] = None,
) -> Dict[str, Any]:
    """Compute Earnings VRP score from historical event data.

    Each event dict is expected to carry ``impliedMovePct`` and ``realizedMovePct``
    (the fields already present in Engine 1 ``events`` list).
    """
    ratios: List[float] = []
    ctc_ratios: List[float] = []
    implied_vals: List[float] = []

    for ev in events:
        imp = _f(ev.get("impliedMovePct"))
        real = _f(ev.get("realizedMovePct"))
        if imp and imp > 0 and real is not None:
            ratios.append(real / imp)
            implied_vals.append(imp)

        ctc = ev.get("ctc") or {}
        ctc_move = _f(ctc.get("ctcMovePct"))
        if imp and imp > 0 and ctc_move is not None:
            ctc_ratios.append(ctc_move / imp)

    n = len(ratios)
    if n < 3:
        return {
            "vrpScore": None,
            "meanRatio": None,
            "stdRatio": None,
            "trendDelta": None,
            "ctcMeanRatio": None,
            "sampleSize": n,
            "ivElevation": None,
            "confidence": "INSUFFICIENT_DATA",
            "components": {},
            "notes": [f"Only {n} usable events — need at least 3."],
        }

    mean_ratio = statistics.mean(ratios)
    std_ratio = statistics.stdev(ratios) if n >= 3 else 0.0

    # Trend: compare recent half vs older half
    mid = n // 2
    recent_mean = statistics.mean(ratios[:mid]) if mid > 0 else mean_ratio
    older_mean = statistics.mean(ratios[mid:]) if (n - mid) > 0 else mean_ratio
    trend_delta = recent_mean - older_mean

    ctc_mean = statistics.mean(ctc_ratios) if ctc_ratios else None

    # IV elevation: current EM vs trailing 4-quarter average
    iv_elevation: Optional[float] = None
    if current_implied_move_pct and implied_vals:
        avg_hist_iv = statistics.mean(implied_vals)
        if avg_hist_iv > 0:
            iv_elevation = current_implied_move_pct / avg_hist_iv

    # --- Composite VRP score (0-100) ---
    # Mean ratio component (30%): lower ratio = higher score
    ratio_score = max(0.0, min(100.0, (1.0 - mean_ratio) * 200.0))

    # Consistency component (25%): lower std = higher score
    consistency_score = max(0.0, min(100.0, (0.5 - std_ratio) * 200.0))

    # Sample size component (20%): more events = higher confidence
    sample_score = min(100.0, (n / 20.0) * 100.0)

    # Trend component (15%): negative trend (getting better) = higher score
    trend_score = max(0.0, min(100.0, 50.0 - trend_delta * 100.0))

    # CTC confirmation component (10%): CTC ratio < 1.0 confirms gap-based VRP
    ctc_score = 50.0
    if ctc_mean is not None:
        ctc_score = max(0.0, min(100.0, (1.0 - ctc_mean) * 200.0))

    composite = (
        ratio_score * 0.30
        + consistency_score * 0.25
        + sample_score * 0.20
        + trend_score * 0.15
        + ctc_score * 0.10
    )
    vrp_score = round(max(0.0, min(100.0, composite)), 1)

    confidence = "HIGH" if n >= 12 and std_ratio < 0.35 else "MED" if n >= 6 else "LOW"

    return {
        "vrpScore": vrp_score,
        "meanRatio": _round3(mean_ratio),
        "stdRatio": _round3(std_ratio),
        "trendDelta": _round3(trend_delta),
        "ctcMeanRatio": _round3(ctc_mean),
        "sampleSize": n,
        "ivElevation": _round2(iv_elevation),
        "confidence": confidence,
        "components": {
            "ratioScore": _round2(ratio_score),
            "consistencyScore": _round2(consistency_score),
            "sampleScore": _round2(sample_score),
            "trendScore": _round2(trend_score),
            "ctcScore": _round2(ctc_score),
        },
        "notes": [],
    }


# ---------------------------------------------------------------------------
# 2. EM x Wing Width Backtest Grid
# ---------------------------------------------------------------------------

def compute_earnings_width_comparison(
    events: List[Dict[str, Any]],
    *,
    em_mults: List[float],
    wing_pts: List[float],
    current_implied_move_pct: Optional[float] = None,
    stock_price: Optional[float] = None,
) -> Tuple[List[Dict[str, Any]], Dict[str, Any]]:
    """Build an EM x Wing Width backtest matrix from Engine 1 event data.

    Returns (width_comparison_rows, em_breach_summary).
    """
    em_breach_summary: Dict[str, Any] = {}
    width_comparison: List[Dict[str, Any]] = []

    # Pre-compute per-event: for each EM, did the gap breach? For each (EM, wing), did it go outside?
    # We use the existing impliedMovePct and realizedMovePct per event.
    valid_events: List[Dict[str, float]] = []
    for ev in events:
        imp = _f(ev.get("impliedMovePct"))
        real = _f(ev.get("realizedMovePct"))
        if imp is None or imp <= 0 or real is None:
            continue
        valid_events.append({"implied": imp, "realized": real})

    n_obs = len(valid_events)
    if n_obs < 3:
        return [], {}

    for em in em_mults:
        breach_count = sum(1 for ve in valid_events if ve["realized"] > ve["implied"] * em)
        em_breach_pct = round(breach_count / n_obs * 100.0, 2)
        em_breach_summary[str(em)] = em_breach_pct
        survival_pct = round(100.0 - em_breach_pct, 2)

        for wp in wing_pts:
            # "Outside wings" = realized move exceeded short strike + wing width
            # In pct terms: realized > implied * em + (wing_dollars / stockPrice * 100)
            # For simplicity with percentage-based data, we model wing as additional
            # EM-additive percentage of implied move.
            if stock_price and stock_price > 0 and current_implied_move_pct and current_implied_move_pct > 0:
                wing_pct_adder = (wp / stock_price) * 100.0
            else:
                wing_pct_adder = wp * 0.5  # fallback heuristic

            outside_count = 0
            loss_pts_list: List[float] = []
            for ve in valid_events:
                short_threshold = ve["implied"] * em
                long_threshold = short_threshold + wing_pct_adder
                is_outside = ve["realized"] > long_threshold
                if is_outside:
                    outside_count += 1
                # Partial/full loss in wing-width-relative terms
                if ve["realized"] > short_threshold:
                    intrusion = ve["realized"] - short_threshold
                    loss = min(intrusion, wing_pct_adder) if wing_pct_adder > 0 else intrusion
                    loss_pts_list.append(loss)
                else:
                    loss_pts_list.append(0.0)

            outside_pct = round(outside_count / n_obs * 100.0, 2) if n_obs > 0 else None
            avg_mean_loss = statistics.mean(loss_pts_list) if loss_pts_list else None

            # Credit proxy: actuarial expected loss * VRP factor
            max_loss = float(wp) * 100.0
            if avg_mean_loss is not None and max_loss > 0:
                vrp_factor = 1.25 + 0.10 * em
                credit_proxy = round(avg_mean_loss / wing_pct_adder * max_loss * vrp_factor, 2) if wing_pct_adder > 0 else 0.0
                credit_proxy = max(credit_proxy, round(max_loss * 0.02, 2))
            else:
                credit_proxy = round(max_loss * 0.10 * math.exp(-0.3 * em), 2)

            roc = round(credit_proxy / (max_loss - credit_proxy) * 100.0, 2) if max_loss > credit_proxy > 0 else None
            risk_adj_roc = round(roc * survival_pct / 100.0, 2) if roc is not None else None

            label = "Tight / Higher ROC"
            if wp <= 2.5:
                label = "Tight / Higher ROC"
            elif wp <= 5:
                label = "Standard"
            elif wp <= 7.5:
                label = "Moderate"
            else:
                label = "Wide / Safer"

            width_comparison.append({
                "emMult": float(em),
                "wingWidthPts": float(wp),
                "breachPct": em_breach_pct,
                "outsidePct": outside_pct,
                "fullLossPct": outside_pct,
                "survivalPct": survival_pct,
                "creditProxy": credit_proxy,
                "expectedLoss": _round2(avg_mean_loss / wing_pct_adder * max_loss) if avg_mean_loss is not None and wing_pct_adder > 0 else None,
                "maxLoss": max_loss,
                "rocPct": roc,
                "riskAdjRocPct": risk_adj_roc,
                "totalObs": n_obs,
                "label": label,
            })

    width_comparison.sort(key=lambda x: (x["emMult"], -(x.get("riskAdjRocPct") or 0)))
    return width_comparison, em_breach_summary


# ---------------------------------------------------------------------------
# 3. Entry Quality Score
# ---------------------------------------------------------------------------

def compute_entry_quality(
    *,
    iv_elevation: Optional[float] = None,
    skew_overlay: Optional[Dict[str, Any]] = None,
    regime: Optional[Dict[str, Any]] = None,
    ticker_dealer_gamma: Optional[Dict[str, Any]] = None,
    current: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Compute entry quality score (0-100) from current market data."""
    scores: Dict[str, Optional[float]] = {}
    flags: List[str] = []

    # IV elevation (25%): higher IV relative to history = more premium
    if iv_elevation is not None:
        iv_score = max(0.0, min(100.0, (iv_elevation - 0.5) * 100.0))
        scores["ivElevation"] = round(iv_score, 1)
    else:
        scores["ivElevation"] = 50.0  # neutral

    # Skew richness (20%): steep put skew = expensive puts = good for selling
    skew_score = 50.0
    if skew_overlay and isinstance(skew_overlay.get("current"), dict):
        sq = skew_overlay["current"].get("skewQuality", "")
        if sq == "RICH_PUT_SKEW":
            skew_score = 80.0
        elif sq == "NORMAL":
            skew_score = 60.0
        elif sq == "FLAT":
            skew_score = 40.0
        elif sq == "INVERTED":
            skew_score = 20.0
            flags.append("inverted_skew")
    scores["skewRichness"] = skew_score

    # Regime alignment (25%): calm = best
    regime_score = 50.0
    if regime:
        bucket = str(regime.get("regimeBucket") or regime.get("bucket") or "").upper()
        r_score_val = _f(regime.get("regimeScore") or regime.get("score"))
        if bucket == "NO_TRADE":
            regime_score = 0.0
            flags.append("no_trade_regime")
        elif r_score_val is not None:
            regime_score = max(0.0, min(100.0, 100.0 - r_score_val))
    scores["regimeAlignment"] = round(regime_score, 1)

    # Dealer gamma context (15%): positive gamma = dampened moves
    gamma_score = 50.0
    if ticker_dealer_gamma and isinstance(ticker_dealer_gamma.get("dealerGamma"), dict):
        dg = ticker_dealer_gamma["dealerGamma"]
        sign = str(dg.get("netGammaSign") or "")
        if sign == "positive":
            gamma_score = 75.0
        elif sign == "negative":
            gamma_score = 25.0
            flags.append("negative_ticker_gamma")
    scores["dealerGamma"] = gamma_score

    # Liquidity (15%): placeholder based on whether current snapshot has data
    liquidity_score = 50.0
    if current and _f(current.get("stockPrice")):
        px = _f(current.get("stockPrice"))
        if px and px > 50:
            liquidity_score = 70.0
        elif px and px > 20:
            liquidity_score = 55.0
        else:
            liquidity_score = 35.0
    scores["liquidity"] = liquidity_score

    # Composite
    composite = (
        (scores.get("ivElevation") or 50.0) * 0.25
        + (scores.get("skewRichness") or 50.0) * 0.20
        + (scores.get("regimeAlignment") or 50.0) * 0.25
        + (scores.get("dealerGamma") or 50.0) * 0.15
        + (scores.get("liquidity") or 50.0) * 0.15
    )
    entry_quality = round(max(0.0, min(100.0, composite)), 1)

    return {
        "entryQuality": entry_quality,
        "components": scores,
        "flags": flags,
    }


# ---------------------------------------------------------------------------
# 4. Deterministic Desk Consensus
# ---------------------------------------------------------------------------

def compute_e1_desk_consensus(
    *,
    vrp: Dict[str, Any],
    entry_quality: Dict[str, Any],
    em_breach_summary: Dict[str, Any],
    regime: Optional[Dict[str, Any]] = None,
    gap_vs_ctc: Optional[Dict[str, Any]] = None,
    event_risk: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Deterministic TRADE / LEAN_PASS / PASS decision for earnings IC.

    Independent of existing go_no_go.py — uses VRP score, breach rate,
    entry quality, regime, CTC drift, and event-risk context.
    """
    vrp_score = _f(vrp.get("vrpScore"))
    eq_score = _f(entry_quality.get("entryQuality"))
    eq_flags = entry_quality.get("flags") or []

    # Best EM: pick the widest EM with breach < 25%, else 2.0x
    preferred_em = 2.0
    for em_str in ["1.0", "1.5", "2.0"]:
        bp = _f(em_breach_summary.get(em_str))
        if bp is not None and bp < 25.0:
            preferred_em = float(em_str)
            break

    best_breach = _f(em_breach_summary.get(str(preferred_em)))
    def _bp(k: str) -> float:
        v = _f(em_breach_summary.get(k))
        return v if v is not None else 100.0

    all_breach_high = all(_bp(k) > 35.0 for k in ["1.0", "1.5", "2.0"])

    # CTC drift check
    ctc_all_high = False
    if gap_vs_ctc and isinstance(gap_vs_ctc.get("ctc"), dict):
        ctc = gap_vs_ctc["ctc"]

        def _ctc_bp(k: str) -> float:
            v = _f(ctc.get(k))
            return v if v is not None else 100.0

        ctc_all_high = all(_ctc_bp(k) > 40.0 for k in ["1.0", "1.5", "2.0"])

    regime_bucket = ""
    if regime:
        regime_bucket = str(regime.get("regimeBucket") or regime.get("bucket") or "").upper()

    macro_intensity_high = False
    if event_risk and isinstance(event_risk, dict):
        score01 = _f(event_risk.get("score01"))
        if score01 is not None and score01 >= 0.60:
            macro_intensity_high = True

    # --- Decision ---
    verdict = "TRADE"
    reasons: List[str] = []

    # PASS conditions (any one triggers)
    if vrp_score is not None and vrp_score < 40:
        verdict = "PASS"
        reasons.append(f"VRP score {vrp_score} < 40 — name does not systematically overprice earnings")
    if all_breach_high:
        verdict = "PASS"
        reasons.append("Breach rate > 35% at ALL EM levels")
    if regime_bucket == "NO_TRADE":
        verdict = "PASS"
        reasons.append("Regime bucket is NO_TRADE")
    if ctc_all_high:
        verdict = "PASS"
        reasons.append("CTC breach rate > 40% at all EM levels — dangerous post-open drift")
    if "no_trade_regime" in eq_flags:
        verdict = "PASS"
        reasons.append("No-trade regime detected in entry quality")

    # LEAN_PASS conditions (borderline)
    if verdict == "TRADE":
        lean_reasons: List[str] = []
        if vrp_score is not None and 40 <= vrp_score < 60:
            lean_reasons.append(f"VRP score {vrp_score} is borderline (40-60)")
        if best_breach is not None and 25 <= best_breach < 35:
            lean_reasons.append(f"Breach rate {best_breach}% at {preferred_em}x is elevated (25-35%)")
        if eq_score is not None and 35 <= eq_score < 50:
            lean_reasons.append(f"Entry quality {eq_score} is borderline (35-50)")
        if macro_intensity_high:
            lean_reasons.append("Elevated macro/event risk intensity")
        if "negative_ticker_gamma" in eq_flags:
            lean_reasons.append("Negative ticker dealer gamma")
        if "inverted_skew" in eq_flags:
            lean_reasons.append("Inverted skew")

        if lean_reasons:
            verdict = "LEAN_PASS"
            reasons.extend(lean_reasons)

    # Determine suggested EM floor
    suggested_em_floor = 2.0
    _bb = best_breach if best_breach is not None else 100.0
    if vrp_score is not None and vrp_score >= 75 and _bb < 15:
        suggested_em_floor = 1.0
    elif vrp_score is not None and vrp_score >= 55 and _bb < 25:
        suggested_em_floor = 1.5

    risk_level = "low"
    if verdict == "PASS":
        risk_level = "high"
    elif verdict == "LEAN_PASS":
        risk_level = "elevated"
    elif vrp_score is not None and vrp_score >= 75 and eq_score is not None and eq_score >= 65:
        risk_level = "low"
    else:
        risk_level = "moderate"

    return {
        "verdict": verdict,
        "riskLevel": risk_level,
        "suggestedEmFloor": suggested_em_floor,
        "preferredEm": preferred_em,
        "vrpScore": vrp_score,
        "entryQuality": eq_score,
        "bestBreachPct": best_breach,
        "reasons": reasons,
    }


# ---------------------------------------------------------------------------
# 5. EM preference (composite-scored preferred EM)
# ---------------------------------------------------------------------------

def compute_em_preference(
    em_breach_summary: Dict[str, Any],
    vrp_score: Optional[float],
    entry_quality_score: Optional[float],
) -> Dict[str, Any]:
    """Pick the preferred EM multiple and label (aggressive/standard/defensive)."""
    preferred = 2.0
    label = "defensive"

    for em_str, lbl in [("1.0", "aggressive"), ("1.5", "standard"), ("2.0", "defensive")]:
        bp = _f(em_breach_summary.get(em_str))
        if bp is not None and bp < 25.0:
            preferred = float(em_str)
            label = lbl
            break

    # Tighten if VRP and quality are strong
    if vrp_score is not None and vrp_score >= 80 and entry_quality_score is not None and entry_quality_score >= 70:
        if _f(em_breach_summary.get("1.0")) is not None and _f(em_breach_summary.get("1.0")) < 20:  # type: ignore[arg-type]
            preferred = 1.0
            label = "aggressive"

    return {
        "preferredEm": preferred,
        "label": label,
    }
