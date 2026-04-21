"""Desk Insight catalog — Engine 1 (Earnings Breach / single-name earnings).

The workhorse. Every single-name IC trade starts here: breach stats, VRP,
width comparison, entry quality, desk consensus.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "e1",
    "name":        "Engine 1 — Earnings Hold Risk / Breach Simulator",
    "description": (
        "Single-name earnings iron-condor staging engine. Runs Monte-Carlo "
        "+ empirical breach stats against N historical earnings events, "
        "combines with VRP (volatility risk premium), entry-quality score, "
        "regime / dealer-gamma gating, and event risk to emit a go/no-go "
        "verdict for today's premium-sale candidate."
    ),
    "asset_class": "single-name equities — earnings cycle",
}


CATALOG = {

    "breach_stats": {
        "title": "Breach Statistics",
        "spec": (
            "Historical frequency with which this ticker's realized "
            "earnings move breached its implied move (EM) across the last "
            "N events. The primary number on the page.\n"
            "- breachPct: % of historical events where realized > EM.\n"
            "- breachEitherPct: breached on the put side OR call side.\n"
            "- avgOvershoot: when it breached, by how much (as % of EM).\n"
            "- n: number of historical events admitted.\n"
            "High breachPct (>30%) + weak VRP = premium-sale headwind. "
            "Low breachPct (<15%) + positive VRP = the sweet spot."
        ),
        "related_cards": [
            {"engine": "e1",  "slug": "em_breach_summary", "label": "EM Breach Summary"},
            {"engine": "e1",  "slug": "width_comparison", "label": "Width Comparison"},
            {"engine": "e1",  "slug": "vrp_analysis", "label": "VRP Analysis"},
            {"engine": "e15", "slug": "planned_exit_outcome", "label": "E15 Planned Exit Outcome"},
        ],
    },

    "vrp_analysis": {
        "title": "VRP Analysis",
        "spec": (
            "Volatility Risk Premium — do options price in MORE vol than "
            "realized around this ticker's earnings?\n"
            "- vrpScore (-100..+100): positive = IV pricier than realized "
            "(tailwind for premium sellers).\n"
            "- ivElevation: current IV percentile vs trailing history; "
            "higher = juicier premium.\n"
            "- ivElevationZ: z-score of current IV vs ticker's own "
            "distribution.\n"
            "Rule of thumb: VRP > +40 and IV rank > 80 = classic premium-"
            "harvesting setup. VRP < 0 = IV is cheap and selling earnings "
            "premium is fighting the house edge."
        ),
        "related_cards": [
            {"engine": "e1",  "slug": "breach_stats", "label": "Breach Statistics"},
            {"engine": "e1",  "slug": "desk_consensus", "label": "Desk Consensus"},
            {"engine": "e1",  "slug": "em_preference", "label": "EM Preference"},
            {"engine": "e15", "slug": "vrp_crush_verdict", "label": "E15 VRP Verdict"},
        ],
    },

    "em_breach_summary": {
        "title": "EM Breach Summary",
        "spec": (
            "Breach frequency broken out by EM-multiple of the short "
            "strikes. Reads as: 'at 1.0x EM wings, breachPct = X; at 1.5x "
            "EM wings, breachPct = Y.'\n"
            "Use this to calibrate how far out you need to place short "
            "strikes to get acceptable breach. If 1.0x EM shows breach "
            "= 35% but 1.5x = 8%, the desk should prefer the 1.5x "
            "placement — the premium trade-off is usually worth it.\n"
            "Also shows stock-price distribution: how many historical "
            "earnings closed within 0-50%, 50-100%, 100-150%, 150%+ of "
            "the EM."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "breach_stats", "label": "Breach Statistics"},
            {"engine": "e1", "slug": "width_comparison", "label": "Width Comparison"},
            {"engine": "e1", "slug": "em_preference", "label": "EM Preference"},
        ],
    },

    "width_comparison": {
        "title": "Width Comparison",
        "spec": (
            "Parametric grid of {EM multiples} × {wing widths in points} "
            "showing what breach looks like at each combination. The "
            "heatmap lets the desk eyeball: 'if I go 1.25x EM on the "
            "shorts with 5pt wings, my historical breach is X%'.\n"
            "Green cells = acceptable breach (typically < 15%); amber = "
            "borderline; red = too aggressive. The card also surfaces "
            "the current IV's cone overlay so you can see where today's "
            "realistic wing placements sit."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "em_breach_summary", "label": "EM Breach Summary"},
            {"engine": "e1", "slug": "em_preference", "label": "EM Preference"},
            {"engine": "e1", "slug": "go_no_go", "label": "Go / No-Go"},
        ],
    },

    "entry_quality": {
        "title": "Entry Quality",
        "spec": (
            "Composite entry-quality score (0-100) that rolls up IV "
            "elevation + skew overlay + regime fit + event risk into a "
            "single gating read:\n"
            "- 80-100 = premium setup; VRP is rich, regime is friendly.\n"
            "- 60-80 = go with standard size.\n"
            "- 40-60 = marginal; verify all go/no-go legs pass.\n"
            "- < 40 = skip or trade tiny size.\n"
            "Drivers are broken out below the score so you can see which "
            "factor is dragging the grade."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "desk_consensus", "label": "Desk Consensus"},
            {"engine": "e1", "slug": "go_no_go", "label": "Go / No-Go"},
            {"engine": "e1", "slug": "vrp_analysis", "label": "VRP Analysis"},
        ],
    },

    "desk_consensus": {
        "title": "Desk Consensus",
        "spec": (
            "The aggregate verdict from combining VRP, entry quality, "
            "regime, gap risk, and event risk into a single "
            "Favorable/Neutral/Fade label with a short rationale. Serves "
            "as the TL;DR for the trader scanning the page.\n"
            "- Favorable: multiple tailwinds, no headwinds → ready to "
            "build a structure.\n"
            "- Neutral: mixed signals → do the work on wings and size.\n"
            "- Fade: structural headwinds (IV too cheap, regime wrong, "
            "event risk high) → preferred action is to pass."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "entry_quality", "label": "Entry Quality"},
            {"engine": "e1", "slug": "vrp_analysis", "label": "VRP Analysis"},
            {"engine": "e1", "slug": "event_risk", "label": "Event Risk"},
            {"engine": "e1", "slug": "go_no_go", "label": "Go / No-Go"},
        ],
    },

    "em_preference": {
        "title": "EM Preference",
        "spec": (
            "Recommended EM-multiple band to place short strikes, given "
            "breach stats + VRP + entry quality. Reads as: 'prefer 1.25x "
            "EM wings; premium is rich enough to tolerate the closer "
            "placement, breach at 1.25x is 12% vs 22% at 1.0x.'\n"
            "Driver chips show what swung the recommendation: high VRP "
            "(closer is OK), thin sample (wider is safer), elevated "
            "event risk (wider), etc."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "em_breach_summary", "label": "EM Breach Summary"},
            {"engine": "e1", "slug": "width_comparison", "label": "Width Comparison"},
            {"engine": "e1", "slug": "vrp_analysis", "label": "VRP Analysis"},
        ],
    },

    "earnings_events_table": {
        "title": "Earnings Events Table",
        "spec": (
            "Row-by-row audit trail of the historical earnings events "
            "used in the stats. Each row: earnDate, anncTod (BMO/AMC), "
            "impliedMovePct at that time, realizedMovePct that printed, "
            "breach flag, ratio of realized/EM, and whether that event "
            "is included in the current stats pool.\n"
            "Use this to eyeball recency: if the last 4 quarters all "
            "breached even though the 5-year history shows 20% breach, "
            "recent structural change is overwhelming the long sample — "
            "down-weight the headline number."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "breach_stats", "label": "Breach Statistics"},
            {"engine": "e1", "slug": "next_event_card", "label": "Next Event"},
            {"engine": "e1", "slug": "em_breach_summary", "label": "EM Breach Summary"},
        ],
    },

    "next_event_card": {
        "title": "Next Event",
        "spec": (
            "The upcoming earnings event this engine is staged against.\n"
            "- earnDateNext: projected next earnings date.\n"
            "- timingPlanned: BMO/AMC (from ORATS snapshot or inferred).\n"
            "- pricingExpiry: the Friday-expiry chain the EM-math uses.\n"
            "- confidence: HIGH (confirmed timing) / MED (inferred) / "
            "LOW (guessed from cadence).\n"
            "Any LOW confidence flag means the desk should manually "
            "verify the date before journalizing a trade."
        ),
        "related_cards": [
            {"engine": "e1",  "slug": "earnings_events_table", "label": "Historical Events"},
            {"engine": "e15", "slug": "e1_summary_strip", "label": "Jump to E15 Scenario"},
            {"engine": "e15", "slug": "planned_exit_timing", "label": "Planned Exit Timing"},
        ],
    },

    "go_no_go": {
        "title": "Go / No-Go Checklist",
        "spec": (
            "Hard-gate checklist the desk runs before a single-name "
            "earnings IC. Each leg is PASS / BLOCK / WARN:\n"
            "- IV Rank + z-score: liquid IV, cheap enough to pay "
            "edge?\n"
            "- Earnings sample size: ≥ GO_MIN_EARNINGS_N events?\n"
            "- Tail sample: enough breach events to estimate p90?\n"
            "- Correlation / beta: not dominated by index moves?\n"
            "- Option liquidity: OI, volume, spreads, quote coverage in "
            "the short-strike delta band?\n"
            "- RV jump control: no pre-event vol acceleration?\n"
            "- Forced flow: macro events colliding with the hold?\n"
            "A single BLOCK = skip the trade."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "desk_consensus", "label": "Desk Consensus"},
            {"engine": "e1", "slug": "entry_quality", "label": "Entry Quality"},
            {"engine": "e1", "slug": "event_risk", "label": "Event Risk"},
        ],
    },

    "event_risk": {
        "title": "Event Risk",
        "spec": (
            "Benzinga-powered event-risk score (0-1) that flags "
            "idiosyncratic headline risk around the earnings window: "
            "M&A rumor, regulatory action, analyst-day, FDA decision, "
            "product launch, etc.\n"
            "- HIGH (> BENZINGA_EVENT_RISK_HIGH_THRESHOLD): known binary "
            "event inside the hold → tails widen materially.\n"
            "- CAUTION (> BENZINGA_EVENT_RISK_CAUTION_THRESHOLD): "
            "elevated noise; size down.\n"
            "- None: clean window."
        ),
        "related_cards": [
            {"engine": "e1",  "slug": "go_no_go", "label": "Go / No-Go"},
            {"engine": "e1",  "slug": "desk_consensus", "label": "Desk Consensus"},
            {"engine": "e15", "slug": "conditioning_modifiers", "label": "E15 Conditioning"},
        ],
    },

    "ticker_dealer_gamma": {
        "title": "Ticker Dealer Gamma",
        "spec": (
            "Estimated dealer net gamma exposure for this specific "
            "ticker (net of customer open interest, delta-hedged, "
            "scaled to notional).\n"
            "- Positive gamma: dealers are long gamma — they buy dips / "
            "sell rips to stay hedged. Dampens realized vol. IC-friendly.\n"
            "- Negative gamma: dealers are short gamma — they sell dips / "
            "buy rips. Amplifies realized vol. IC-hostile.\n"
            "- Zero-gamma cross: the spot price where dealer positioning "
            "flips sign. When we sit near this line, regime is fragile."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "entry_quality", "label": "Entry Quality"},
            {"engine": "e1", "slug": "regime", "label": "Ticker Regime"},
            {"engine": "e1", "slug": "skew_overlay", "label": "Skew Overlay"},
        ],
    },

    "regime": {
        "title": "Ticker Regime",
        "spec": (
            "This ticker's current regime classification (separate from "
            "the market-wide Market Intelligence regime). Inputs: the "
            "name's own RV20, its beta-adjusted move vs SPX, IV regime, "
            "recent gap behavior.\n"
            "Regimes: Trend-Up / Range / Trend-Down / High-Vol / "
            "Mean-Revert. Each has a different baseline for breach — "
            "e.g. High-Vol earnings breach more often at a given EM, "
            "Range regimes breach less."
        ),
        "related_cards": [
            {"engine": "e1",  "slug": "ticker_dealer_gamma", "label": "Dealer Gamma"},
            {"engine": "e1",  "slug": "gap_vs_ctc", "label": "Gap vs Close-to-Close"},
            {"engine": "e13", "slug": "gap_regime_card", "label": "Market Gap Regime"},
        ],
    },

    "skew_overlay": {
        "title": "Skew Overlay",
        "spec": (
            "The ticker's current IV skew — how much more expensive OTM "
            "puts are than OTM calls at the pricing expiry. Measured in "
            "vol points or as a ratio.\n"
            "- Steep put skew: market pricing in downside tail → "
            "breach-at-puts probability rises, richer premium on the put "
            "wing.\n"
            "- Flat skew: symmetric tails → equal treatment of puts and "
            "calls.\n"
            "- Call skew (rare outside momentum names): upside tail "
            "premium exceeds put tail."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "vrp_analysis", "label": "VRP Analysis"},
            {"engine": "e1", "slug": "em_preference", "label": "EM Preference"},
            {"engine": "e1", "slug": "entry_quality", "label": "Entry Quality"},
        ],
    },

    "gap_vs_ctc": {
        "title": "Gap vs Close-to-Close",
        "spec": (
            "Compares the ticker's gap-move distribution (overnight "
            "earnings gap) to its close-to-close move distribution. If "
            "gaps dominate, most of the realized earnings move is "
            "unhedgeable — the whole move happens in the gap window, "
            "well before the desk can react.\n"
            "- gapShare > 80%: essentially all move is gap; your stop "
            "loss will fill on the open at the new level, not at the "
            "intraday limit you set.\n"
            "- gapShare < 50%: intraday follow-through is meaningful; "
            "tactical management can help."
        ),
        "related_cards": [
            {"engine": "e1",  "slug": "regime", "label": "Ticker Regime"},
            {"engine": "e1",  "slug": "breach_stats", "label": "Breach Statistics"},
            {"engine": "e13", "slug": "gap_regime_card", "label": "Market Gap Regime"},
        ],
    },

}
