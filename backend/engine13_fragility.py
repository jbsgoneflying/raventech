"""Engine 13 — Catalyst Fragility Score.

Measures whether the market is treating a gap rally as a durable regime
shift or a fragile move.  Five independent sub-scores (0-100, higher =
more fragile) are weighted into a composite score with a human-readable
label (LOW / MODERATE / HIGH / EXTREME).

This module is deterministic — no LLM calls.  All inputs come from
Engine 13's existing layers plus new oil/headline data fetched by the
orchestrator.
"""
from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

_CLAMP = lambda lo, hi, v: max(lo, min(hi, v))

DEFAULT_WEIGHTS = {
    "optionsConviction": 0.30,
    "crossAssetConfirmation": 0.25,
    "historicalDurability": 0.20,
    "headlineMomentum": 0.15,
    "priceActionQuality": 0.10,
}

_FRAGILITY_LABELS = [
    (30, "LOW"),
    (50, "MODERATE"),
    (70, "HIGH"),
    (100, "EXTREME"),
]


def _label(score: float) -> str:
    for threshold, label in _FRAGILITY_LABELS:
        if score <= threshold:
            return label
    return "EXTREME"


# ---------------------------------------------------------------------------
# Sub-score 1: Options Conviction  (weight 30%)
# ---------------------------------------------------------------------------

def _score_options_conviction(
    options: Dict[str, Any],
    gap_direction: str,
) -> Tuple[float, List[str]]:
    """Score 0-100 based on whether options microstructure shows
    institutional conviction in the gap direction."""
    signals: List[str] = []
    scores: List[float] = []

    skew = options.get("skew") or {}
    skew_label = skew.get("label", "")
    skew_25d = skew.get("skew25d")
    pc_ratio = skew.get("putCallRatio")

    # Put skew on an up-gap day = institutions hedging the rally
    if gap_direction == "up":
        if "extreme_put_skew" in skew_label:
            scores.append(90)
            signals.append("Extreme put skew despite up-gap — heavy hedging")
        elif "elevated_put_skew" in skew_label:
            scores.append(65)
            signals.append("Elevated put skew on up-gap day")
        elif "call_skew" in skew_label:
            scores.append(15)
            signals.append("Call skew — options market confirms bullish tilt")
        else:
            scores.append(40)
            signals.append("Skew normal")
    else:
        if "extreme_put_skew" in skew_label:
            scores.append(20)
            signals.append("Extreme put skew confirms down-gap conviction")
        elif "call_skew" in skew_label:
            scores.append(80)
            signals.append("Call skew on down-gap — options market diverging")
        else:
            scores.append(40)

    # IV term structure
    ts = options.get("termStructure") or {}
    ts_label = ts.get("label", "")
    if ts_label == "backwardation":
        scores.append(80)
        signals.append("IV backwardation — near-term risk not resolved")
    elif ts_label == "contango":
        scores.append(20)
        signals.append("IV contango — term structure calm")
    else:
        scores.append(45)
        signals.append("IV term structure flat")

    # Dealer gamma
    dg = options.get("dealerGamma") or {}
    gamma_sign = dg.get("netGammaSign", "")
    gamma_bucket = dg.get("magnitudeBucket", "low")
    if gamma_sign == "negative":
        mag_score = {"low": 60, "medium": 75, "high": 90}.get(gamma_bucket, 70)
        scores.append(mag_score)
        signals.append(f"Dealer gamma negative ({gamma_bucket}) — amplifies reversals")
    elif gamma_sign == "positive":
        mag_score = {"low": 35, "medium": 25, "high": 15}.get(gamma_bucket, 30)
        scores.append(mag_score)
        signals.append(f"Dealer gamma positive ({gamma_bucket}) — stabilising")
    else:
        scores.append(50)

    # Unusual flow sentiment
    flow = options.get("unusualFlow") or {}
    net_sent = flow.get("netSentiment", "")
    if net_sent and gap_direction == "up":
        if net_sent == "bearish":
            scores.append(80)
            signals.append("Bearish unusual flow on up-gap — institutional skepticism")
        elif net_sent == "bullish":
            scores.append(20)
            signals.append("Bullish unusual flow confirms up-gap")
        else:
            scores.append(50)
            signals.append("Mixed unusual flow")
    elif net_sent and gap_direction == "down":
        if net_sent == "bullish":
            scores.append(75)
            signals.append("Bullish flow on down-gap — divergence")
        elif net_sent == "bearish":
            scores.append(20)
            signals.append("Bearish flow confirms down-gap")
        else:
            scores.append(50)
    else:
        scores.append(50)

    # Put/call ratio
    if pc_ratio is not None:
        if gap_direction == "up":
            if pc_ratio > 1.3:
                scores.append(75)
                signals.append(f"Put/call ratio {pc_ratio:.2f} — elevated hedging")
            elif pc_ratio < 0.7:
                scores.append(20)
                signals.append(f"Put/call ratio {pc_ratio:.2f} — call-heavy conviction")
            else:
                scores.append(45)
        else:
            if pc_ratio < 0.7:
                scores.append(70)
            elif pc_ratio > 1.3:
                scores.append(25)
            else:
                scores.append(45)

    final = round(sum(scores) / max(1, len(scores)), 1) if scores else 50.0
    return _CLAMP(0, 100, final), signals


# ---------------------------------------------------------------------------
# Sub-score 2: Cross-Asset Confirmation  (weight 25%)
# ---------------------------------------------------------------------------

def _score_cross_asset(
    gap_info: Dict[str, Any],
    vix: Dict[str, Any],
    oil_bars: Optional[List[Any]] = None,
) -> Tuple[float, List[str]]:
    """How well do VIX and oil confirm or contradict the gap?"""
    signals: List[str] = []
    scores: List[float] = []

    gap_pct = abs(gap_info.get("gapPct", 0))
    direction = gap_info.get("direction", "up")

    if not vix.get("enabled"):
        return 50.0, ["VIX data unavailable"]

    vix_change = vix.get("changePct", 0)
    vix_now = vix.get("vixNow", 0)
    ma20 = vix.get("ma20", 0)
    above_ma = vix.get("aboveMa20", False)
    snapback = vix.get("snapback", False)

    # VIX proportionality: a +2.5% SPX gap should produce ~-10 to -15% VIX change
    if direction == "up" and gap_pct > 0.5:
        expected_vix_drop = gap_pct * -5.0
        if vix_change > expected_vix_drop * 0.3:
            scores.append(80)
            signals.append(f"VIX under-reacting — only {vix_change:+.1f}% vs expected ~{expected_vix_drop:.0f}%")
        elif vix_change > expected_vix_drop * 0.6:
            scores.append(55)
            signals.append(f"VIX partially confirming ({vix_change:+.1f}%)")
        else:
            scores.append(20)
            signals.append(f"VIX fully confirming gap ({vix_change:+.1f}%)")
    elif direction == "down" and gap_pct > 0.5:
        expected_vix_spike = gap_pct * 5.0
        if vix_change < expected_vix_spike * 0.3:
            scores.append(75)
            signals.append(f"VIX under-reacting to down-gap")
        else:
            scores.append(25)
    else:
        scores.append(50)

    # VIX snapback
    if snapback:
        scores.append(85)
        signals.append("VIX snapback detected — market reversing initial calm")
    else:
        scores.append(30)

    # VIX vs 20d MA
    if direction == "up" and above_ma:
        scores.append(70)
        signals.append(f"VIX still above 20d MA ({ma20:.1f}) despite rally")
    elif direction == "up" and not above_ma:
        scores.append(25)
        signals.append(f"VIX below 20d MA ({ma20:.1f}) — sustained calm")
    elif direction == "down" and not above_ma:
        scores.append(65)
        signals.append(f"VIX below 20d MA despite down-gap — under-reacting")
    else:
        scores.append(30)

    # Oil confirmation (geopolitical catalysts)
    catalyst = str(gap_info.get("catalystTag", "")).lower()
    is_geo_catalyst = any(k in catalyst for k in (
        "geopolitical", "war", "iran", "hormuz", "oil", "energy",
        "tariff", "sanction", "conflict", "military", "ceasefire",
        "truce", "invasion",
    ))

    if is_geo_catalyst and oil_bars and len(oil_bars) >= 2:
        try:
            def _close(bar):
                return float(getattr(bar, "close", None) or (bar.get("close") if isinstance(bar, dict) else None) or 0)
            prev_oil = _close(oil_bars[-2])
            curr_oil = _close(oil_bars[-1])
            if prev_oil > 0:
                oil_change = (curr_oil - prev_oil) / prev_oil * 100.0
                if direction == "up":
                    # Up-gap on ceasefire → oil should drop
                    if oil_change > 0.5:
                        scores.append(75)
                        signals.append(f"Oil UP {oil_change:+.1f}% despite positive geo catalyst — divergence")
                    elif oil_change < -1.0:
                        scores.append(20)
                        signals.append(f"Oil down {oil_change:+.1f}% — confirming geo resolution")
                    else:
                        scores.append(45)
                        signals.append(f"Oil flat ({oil_change:+.1f}%) — tepid confirmation")
                else:
                    if oil_change < -0.5:
                        scores.append(70)
                        signals.append(f"Oil DOWN despite down-gap — mixed signal")
                    else:
                        scores.append(30)
        except Exception:
            pass
    elif is_geo_catalyst:
        signals.append("Oil data unavailable for geo catalyst check")

    final = round(sum(scores) / max(1, len(scores)), 1) if scores else 50.0
    return _CLAMP(0, 100, final), signals


# ---------------------------------------------------------------------------
# Sub-score 3: Historical Durability  (weight 20%)
# ---------------------------------------------------------------------------

def _score_historical_durability(
    historical: Dict[str, Any],
    geo_analogues: Optional[List[Dict[str, Any]]] = None,
) -> Tuple[float, List[str]]:
    """Score based on how durable similar moves were historically."""
    signals: List[str] = []
    scores: List[float] = []

    # Outcome distribution from gap analogues
    dist = historical.get("outcomeDistribution") or {}
    rev_pct = dist.get("reversion", 30)
    cont_pct = dist.get("continuation", 35)
    cons_pct = dist.get("consolidation", 35)

    if historical.get("count", 0) >= 3:
        rev_score = _CLAMP(0, 100, rev_pct * 1.2)
        scores.append(rev_score)
        if rev_pct > 40:
            signals.append(f"Analogues show {rev_pct:.0f}% reversion rate — historically fragile")
        elif cont_pct > 60:
            signals.append(f"Analogues show {cont_pct:.0f}% continuation — historically durable")
        else:
            signals.append(f"Analogue outcomes mixed: C {cont_pct:.0f}% / S {cons_pct:.0f}% / R {rev_pct:.0f}%")

        # Median gap fill
        med_fill = historical.get("medianIntradayGapFill")
        if med_fill is not None:
            fill_score = _CLAMP(0, 100, med_fill * 1.0)
            scores.append(fill_score)
            if med_fill > 50:
                signals.append(f"Median gap fill {med_fill:.0f}% — gaps tend to retrace")
    else:
        scores.append(50)
        signals.append("Insufficient analogues for durability assessment")

    # Geopolitical shock database
    if geo_analogues and len(geo_analogues) >= 2:
        contained = sum(1 for e in geo_analogues if e.get("outcome_class") == "contained")
        escalated = sum(1 for e in geo_analogues if e.get("outcome_class") in ("disruption", "escalation"))
        secondary = sum(1 for e in geo_analogues if e.get("secondary_spike"))
        n = len(geo_analogues)

        if n > 0:
            esc_rate = escalated / n
            esc_score = _CLAMP(0, 100, esc_rate * 120)
            scores.append(esc_score)
            if esc_rate > 0.5:
                signals.append(f"{escalated}/{n} geo analogues escalated/disrupted — high fragility")
            elif esc_rate < 0.3:
                signals.append(f"{contained}/{n} geo analogues contained — historically durable")
            else:
                signals.append(f"Geo analogues mixed: {contained} contained, {escalated} escalated")

            if secondary > 0:
                sec_rate = secondary / n * 100
                sec_score = _CLAMP(0, 100, sec_rate * 0.9)
                scores.append(sec_score)
                signals.append(f"Secondary VIX spike in {sec_rate:.0f}% of similar events")

    final = round(sum(scores) / max(1, len(scores)), 1) if scores else 50.0
    return _CLAMP(0, 100, final), signals


# ---------------------------------------------------------------------------
# Sub-score 4: Headline Momentum  (weight 15%)
# ---------------------------------------------------------------------------

_FRAGILE_KEYWORDS = re.compile(
    r"fragile|tentative|temporary|collapse|revert|fail|break\s*down|"
    r"uncertain|precarious|shaky|weak|conditional|caution|warn|skeptic|"
    r"deadline|ultimatum|threat|escalat|violat|breach",
    re.IGNORECASE,
)

_DURABLE_KEYWORDS = re.compile(
    r"permanent|signed|ratif|framework|agreement|progress|breakthrough|"
    r"optimis|confirm|solidif|endors|commit|implement|phas|support|"
    r"constructive|momentum|historic",
    re.IGNORECASE,
)


def _score_headline_momentum(
    headlines: Optional[List[str]] = None,
    theme_snapshot: Optional[Dict[str, Any]] = None,
) -> Tuple[float, List[str]]:
    """Score based on headline sentiment trajectory."""
    signals: List[str] = []
    scores: List[float] = []

    # Theme intelligence (from DMS / news_theme_intelligence)
    if theme_snapshot:
        geo_theme = None
        for reading in (theme_snapshot.get("readings") or []):
            if reading.get("key") == "geopolitical_escalation" or \
               reading.get("theme") == "Geopolitical Escalation":
                geo_theme = reading
                break

        if geo_theme:
            accel = geo_theme.get("acceleration", "stable")
            adj_intensity = geo_theme.get("adjusted_intensity", 0)

            if accel == "rising":
                scores.append(75)
                signals.append(f"Geopolitical escalation theme rising (intensity {adj_intensity:.0f})")
            elif accel == "falling":
                scores.append(25)
                signals.append(f"Geopolitical escalation theme falling — narrative easing")
            else:
                scores.append(50)
                signals.append(f"Geopolitical theme stable (intensity {adj_intensity:.0f})")

            if adj_intensity > 60:
                scores.append(70)
                signals.append(f"High geo theme intensity ({adj_intensity:.0f})")
            elif adj_intensity < 20:
                scores.append(25)

    # Headline keyword balance
    if headlines and len(headlines) >= 3:
        fragile_hits = sum(1 for h in headlines if _FRAGILE_KEYWORDS.search(h))
        durable_hits = sum(1 for h in headlines if _DURABLE_KEYWORDS.search(h))
        total = len(headlines)

        fragile_rate = fragile_hits / total
        durable_rate = durable_hits / total

        if fragile_rate > durable_rate * 1.5 and fragile_hits >= 2:
            sentiment_score = _CLAMP(60, 90, 50 + fragile_rate * 80)
            scores.append(sentiment_score)
            signals.append(f"Headline sentiment skews fragile ({fragile_hits} cautious vs {durable_hits} positive)")
        elif durable_rate > fragile_rate * 1.5 and durable_hits >= 2:
            sentiment_score = _CLAMP(10, 40, 50 - durable_rate * 80)
            scores.append(sentiment_score)
            signals.append(f"Headline sentiment skews durable ({durable_hits} positive vs {fragile_hits} cautious)")
        else:
            scores.append(50)
            signals.append(f"Mixed headline sentiment ({durable_hits} positive, {fragile_hits} cautious)")
    elif headlines:
        scores.append(50)
        signals.append(f"Only {len(headlines)} headlines — thin data")
    else:
        scores.append(50)
        signals.append("No headline data available")

    final = round(sum(scores) / max(1, len(scores)), 1) if scores else 50.0
    return _CLAMP(0, 100, final), signals


# ---------------------------------------------------------------------------
# Sub-score 5: Price Action Quality  (weight 10%)
# ---------------------------------------------------------------------------

def _score_price_action(gap_info: Dict[str, Any]) -> Tuple[float, List[str]]:
    """Score based on intraday price action relative to the gap."""
    signals: List[str] = []
    scores: List[float] = []

    if not gap_info.get("enabled"):
        return 50.0, ["No gap data"]

    gap_fill = gap_info.get("gapFillPct")
    direction = gap_info.get("direction", "up")
    live = gap_info.get("livePrice")
    today_open = gap_info.get("todayOpen")

    # Gap fill %
    if gap_fill is not None:
        fill_score = _CLAMP(0, 100, gap_fill * 1.1)
        scores.append(fill_score)
        if gap_fill > 60:
            signals.append(f"Gap {gap_fill:.0f}% filled intraday — weak conviction")
        elif gap_fill > 30:
            signals.append(f"Gap {gap_fill:.0f}% filled — partial fade")
        elif gap_fill < 10:
            signals.append(f"Gap holding ({gap_fill:.0f}% fill) — strong conviction")
        else:
            signals.append(f"Gap {gap_fill:.0f}% filled")

    # Price vs open
    if live is not None and today_open is not None and today_open > 0:
        pct_from_open = (live - today_open) / today_open * 100.0
        if direction == "up":
            if pct_from_open < -0.3:
                scores.append(75)
                signals.append(f"Price fading below open ({pct_from_open:+.2f}%) — rally losing steam")
            elif pct_from_open > 0.3:
                scores.append(15)
                signals.append(f"Price extending above open ({pct_from_open:+.2f}%) — building on gap")
            else:
                scores.append(45)
                signals.append(f"Price near open ({pct_from_open:+.2f}%)")
        else:
            if pct_from_open > 0.3:
                scores.append(70)
                signals.append(f"Price bouncing from gap-down open ({pct_from_open:+.2f}%)")
            elif pct_from_open < -0.3:
                scores.append(20)
                signals.append(f"Price extending gap-down ({pct_from_open:+.2f}%)")
            else:
                scores.append(45)

    if not scores:
        scores.append(50)
        signals.append("Intraday price data pending")

    final = round(sum(scores) / max(1, len(scores)), 1) if scores else 50.0
    return _CLAMP(0, 100, final), signals


# ---------------------------------------------------------------------------
# Composite — main entry point
# ---------------------------------------------------------------------------

def compute_catalyst_fragility(
    *,
    gap_info: Dict[str, Any],
    options: Dict[str, Any],
    vix: Dict[str, Any],
    historical: Dict[str, Any],
    geo_analogues: Optional[List[Dict[str, Any]]] = None,
    oil_bars: Optional[List[Any]] = None,
    headlines: Optional[List[str]] = None,
    theme_snapshot: Optional[Dict[str, Any]] = None,
    weights: Optional[Dict[str, float]] = None,
) -> Dict[str, Any]:
    """Build the composite Catalyst Fragility Score (0-100)."""
    if not gap_info.get("enabled"):
        return {"enabled": False, "notes": ["No gap detected — fragility score not applicable"]}

    direction = gap_info.get("direction", "up")
    w = weights or DEFAULT_WEIGHTS

    # Compute all five sub-scores
    opt_score, opt_signals = _score_options_conviction(options, direction)
    ca_score, ca_signals = _score_cross_asset(gap_info, vix, oil_bars)
    hd_score, hd_signals = _score_historical_durability(historical, geo_analogues)
    hm_score, hm_signals = _score_headline_momentum(headlines, theme_snapshot)
    pa_score, pa_signals = _score_price_action(gap_info)

    # Weighted composite
    composite = (
        w.get("optionsConviction", 0.30) * opt_score +
        w.get("crossAssetConfirmation", 0.25) * ca_score +
        w.get("historicalDurability", 0.20) * hd_score +
        w.get("headlineMomentum", 0.15) * hm_score +
        w.get("priceActionQuality", 0.10) * pa_score
    )
    composite = round(_CLAMP(0, 100, composite), 1)

    # Dominant factors: collect all signals with their sub-score contribution
    all_factors: List[Tuple[float, str]] = []
    for sc, sigs in [
        (opt_score, opt_signals),
        (ca_score, ca_signals),
        (hd_score, hd_signals),
        (hm_score, hm_signals),
        (pa_score, pa_signals),
    ]:
        for sig in sigs:
            all_factors.append((sc, sig))

    all_factors.sort(key=lambda x: x[0], reverse=True)
    dominant = [sig for score, sig in all_factors if score >= 55][:5]

    # Catalyst type inference
    catalyst_tag = str(gap_info.get("catalystTag", "unknown")).lower()
    catalyst_type = "unknown"
    if any(k in catalyst_tag for k in ("geopolitical", "war", "iran", "conflict", "military")):
        catalyst_type = "geopolitical"
    elif any(k in catalyst_tag for k in ("ceasefire", "truce", "peace", "agreement", "deal")):
        catalyst_type = "geopolitical_ceasefire"
    elif any(k in catalyst_tag for k in ("tariff", "trade", "sanction")):
        catalyst_type = "trade_policy"
    elif any(k in catalyst_tag for k in ("fed", "fomc", "rate", "monetary")):
        catalyst_type = "monetary_policy"
    elif any(k in catalyst_tag for k in ("earning", "guidance", "revenue")):
        catalyst_type = "earnings"

    return {
        "enabled": True,
        "score": composite,
        "label": _label(composite),
        "components": {
            "optionsConviction": {"score": round(opt_score, 1), "signals": opt_signals},
            "crossAssetConfirmation": {"score": round(ca_score, 1), "signals": ca_signals},
            "historicalDurability": {"score": round(hd_score, 1), "signals": hd_signals},
            "headlineMomentum": {"score": round(hm_score, 1), "signals": hm_signals},
            "priceActionQuality": {"score": round(pa_score, 1), "signals": pa_signals},
        },
        "dominantFactors": dominant,
        "catalystType": catalyst_type,
    }
