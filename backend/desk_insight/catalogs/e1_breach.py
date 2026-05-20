"""Desk Insight catalog — Engine 1 (Earnings Breach / single-name earnings).

The workhorse. Every single-name IC trade starts here: breach stats, VRP,
width comparison, entry quality, desk consensus.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "e1",
    "name":        "Engine 1 v2 — Wing Decision Console",
    "description": (
        "Single-name earnings iron-condor wing console. The desk is "
        "assumed to be taking the trade; E1 v2 tells them WHERE to "
        "place wings for maximum theta capture without breach-gap, "
        "breach-CTC, or White-Knuckle (MAE) risk forcing an early "
        "exit. Scores a 15-point (EM multiple × wing width) grid "
        "deterministically from historical breach + MAE + IV-crush "
        "patterns. MC runs per placement; MI v2 provides the "
        "canonical regime. Earnings date + timing are required."
    ),
    "asset_class": "single-name equities — earnings cycle",
}


CATALOG = {

    # NOTE: The "wing_console" slug was retired 2026-05-20 alongside
    # the Wing Decision Console UI removal. The placement/MAE/theta
    # primitives still exist server-side because Engine 15's simulator
    # uses them for the E1 cross-check, but they have no /breach
    # surface anymore.

    "placement_score": {
        "title": "Placement Scorecard",
        "spec": (
            "Row-level drill-down of one candidate wing placement. Each "
            "row shows:\n"
            "- em_mult × wing_pts: distance past the implied move and "
            "wing width (in points).\n"
            "- short/long put + call strikes: absolute prices given "
            "the current spot + EM.\n"
            "- breach_gap_prob / breach_ctc_prob: the two empirical "
            "breach probabilities.\n"
            "- mae_p95_pct: MAE as % of wing-width at this placement; "
            "> 100% means 'historically hit max loss at this placement'.\n"
            "- theta_capture_pct: expected kept-credit fraction if held.\n"
            "- credit_est: per-contract credit (in points; × 100 = $).\n"
            "- composite_score (0-100): weighted roll-up. The rank-1 "
            "row is the scoring engine's best guess; the desk can then "
            "pull the sliders to explore alternatives."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "mae_distribution", "label": "MAE Pool"},
            {"engine": "e1", "slug": "theta_capture", "label": "Theta Capture"},
        ],
    },

    "mae_distribution": {
        "title": "MAE (White-Knuckle) Pool",
        "spec": (
            "Max Adverse Excursion distribution across the historical "
            "earnings event pool. For each past event we compute "
            "max(|high - entry|, |entry - low|) across the 1–2 trading "
            "day hold window from daily OHLC bars. The pool is "
            "aggregated into p50 / p75 / p90 / p95 percentiles in % "
            "price-move terms.\n"
            "At scoring time, mae_p95 is converted into 'distance past "
            "the short strike' and then 'fraction of wing width', which "
            "is what the composite penalty term uses.\n"
            "Source tags:\n"
            "- daily_ohlc_proxy: all events carried high + low (best).\n"
            "- open_close_fallback: many events missing high/low; the "
            "pool conservatively under-estimates true intraday MAE.\n"
            "- mixed_proxy: partial coverage.\n"
            "High mae_p95 + tight wings = historically forced into "
            "panic-close territory. That's the signal the desk needs."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "placement_score", "label": "Placement Scorecard"},
            {"engine": "e1", "slug": "gap_vs_ctc", "label": "Gap vs Close-to-Close"},
        ],
    },

    "theta_capture": {
        "title": "Theta Capture Estimate",
        "spec": (
            "Expected fraction of entry credit retained at a planned "
            "post-event exit. Built from the ticker's historical "
            "realized / implied move ratio:\n"
            "- decay_richness = clamp(1 - mean(|signed| / EM), 0.10, "
            "0.95) — how over-priced was the pre-event premium on "
            "average.\n"
            "- survival_rate(em_multiple) — % of past events where "
            "|signed_move| ≤ em_mult × EM; this placement would have "
            "survived the event unbreached.\n"
            "- capture_pct ≈ survival × (0.30 + 0.65 × richness).\n"
            "Think of capture_pct as 'what the desk would historically "
            "have walked away with'. Above 60% is a rich setup; below "
            "40% is thin and argues for wider wings or skipping.\n"
            "Drives the theta term of the composite score."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "vrp_analysis", "label": "VRP Analysis"},
            {"engine": "e1", "slug": "placement_score", "label": "Placement Scorecard"},
        ],
    },

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
        "title": "Desk Consensus (legacy)",
        "spec": (
            "Legacy aggregate verdict from combining VRP, entry "
            "quality, regime, gap risk, and event risk into a single "
            "Favorable/Neutral/Fade label. In E1 v2 this card is "
            "hidden from the primary view and kept only as a "
            "drill-down — the desk's actual decision is driven by the "
            "Wing Decision Console, which is deterministic and "
            "cacheable. `compute_e1_desk_consensus` still ships the "
            "field for the LLM Advisor narrative; the frontend no "
            "longer renders it above-the-fold."
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
            "- timingPlanned: BMO/AMC (from user override > ORATS "
            "snapshot > Benzinga > cadence estimate).\n"
            "- override_source: 'user_override' | 'orats_cores' | "
            "'benzinga' | 'cadence_estimate' — the canonical source "
            "chip shown next to the date in the UI.\n"
            "- pricingExpiry: the Friday-expiry chain the EM-math uses.\n"
            "- confidence: HIGH (confirmed) / MED (inferred) / LOW "
            "(guessed from cadence).\n"
            "In E1 v2 the desk is required to confirm or enter the "
            "date + timing before Calculate can run — a user override "
            "always flips override_source to user_override with "
            "HIGH confidence."
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
            "Hard-gate checklist run before a single-name earnings IC. "
            "Each leg is PASS / BLOCK / WARN / MISSING.\n"
            "In E1 v2 the options-liquidity family "
            "(SN_OPT_SPREAD_*, SN_OPT_OI_*, SN_OPT_VOL_*, band-coverage "
            "checks) is gated OFF by default — it reports MISSING with "
            "note 'SN_OPT_GATE_DISABLED'. The underlying $-volume gate "
            "(SN_LIQ_UNDERLYING_TOO_LOW) and the legal/reg denylist "
            "(SN_LEGAL_REG) remain active.\n"
            "Legs still evaluated:\n"
            "- Earnings sample size: ≥ GO_MIN_EARNINGS_N events.\n"
            "- Tail sample: enough breach events to estimate p90.\n"
            "- Underlying avg $-vol (SN_LIQ_UNDERLYING_TOO_LOW).\n"
            "- Legal / reg denylist + keyword trigger.\n"
            "- RV jump control, forced-flow windows.\n"
            "Re-enable options-liquidity checks with "
            "ENABLE_E1_OPTIONS_LIQUIDITY_GATE=1 if the desk's data feed "
            "gets reliable coverage of the delta band."
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

    "mc_earnings_risk": {
        "title": "Simulated Earnings Risk (MC)",
        "spec": (
            "Empirical Monte Carlo simulation of the earnings gap at "
            "open (close → open only; no intraday path modeled). Pulls "
            "from the ticker's historical gap distribution, "
            "resamples ENGINE1_MC_N_SIMS (5000 by default) paths, and "
            "produces:\n"
            "- Chance of breach: probability the gap finishes beyond "
            "your short strikes.\n"
            "- Expected loss at open: mean intrinsic loss across sims.\n"
            "- CVaR95: average loss in the worst 5% of simulated gaps "
            "— the tail the desk actually cares about.\n"
            "- Wing optimization: suggested wing-width shift (if "
            "MC_ENABLE_WING_OPTIMIZATION is on) that minimizes CVaR95 "
            "within the MC_OPT_MAX_MULT_DELTA budget.\n"
            "Treat this as 'open-gap tail risk' — the risk that blows "
            "through short IC wings before you can manage intraday."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "breach_stats", "label": "Breach Statistics"},
            {"engine": "e1", "slug": "em_breach_summary", "label": "EM Breach Summary"},
            {"engine": "e1", "slug": "gap_vs_ctc", "label": "Gap vs Close-to-Close"},
            {"engine": "e1", "slug": "event_risk", "label": "Event Risk"},
        ],
    },

    "quarter_seasonality": {
        "title": "Quarter Seasonality",
        "spec": (
            "Quarter-by-quarter breakdown of this ticker's earnings "
            "behavior (Q1/Q2/Q3/Q4). Each quarter card shows:\n"
            "- breach Δ: the breach-rate delta vs the ticker's overall "
            "baseline (in percentage points).\n"
            "- max realized / implied ratio: how wildly the move "
            "overshot the EM on the worst quarter in the sample.\n"
            "- sample size: N historical events in that quarter. "
            "Quarters with N < 3 are marked low-confidence.\n"
            "Use this as a simple calendar conditioning tool for "
            "strike distance and sizing, not as a directional signal. "
            "Tickers with strong Q1 or Q4 guidance cycles often show "
            "the widest quarter-level dispersion."
        ),
        "related_cards": [
            {"engine": "e1", "slug": "earnings_events_table", "label": "Earnings Events"},
            {"engine": "e1", "slug": "em_breach_summary", "label": "EM Breach Summary"},
            {"engine": "e1", "slug": "regime", "label": "Ticker Regime"},
        ],
    },

}
