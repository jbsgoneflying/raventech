"""Desk Insight catalog — Market Intelligence (home / front layer).

Market Intelligence is the desk's first-thing-in-the-morning surface:
cross-asset regime read, overnight-to-open diff, macro calendar, pattern
recognition, and per-asset drill-downs. These tooltips teach a new desk
hire what each tile is actually telling them and how it should shape
today's plan.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "market-intel",
    "name":        "Market Intelligence",
    "description": (
        "The desk's cross-asset morning surface. Synthesizes regime state "
        "(VIX, term structure, breadth, dealer gamma), overnight-to-open "
        "diff, macro event density, pattern detection, and per-asset "
        "stress cards into one pre-open brief."
    ),
    "asset_class": "cross-asset (SPX, vol, rates, FX, credit, crypto, commodities)",
}


CATALOG = {

    "regime_card": {
        "title": "Regime State",
        "spec": (
            "Today's cross-asset regime read, distilled into a single "
            "label plus the inputs that drove it:\n"
            "- Regime label: Risk-On / Transitional / Risk-Off / Stressed "
            "— the Monte-Carlo ensemble over {VIX term structure, SPX "
            "breadth, dealer gamma sign, credit-vol stress, FX DXY drift}.\n"
            "- Score (0-100): higher = more defensive. Thresholds live in "
            "config.ENGINE2_REGIME_* knobs.\n"
            "- Vol State: compressing / stable / expanding / unstable — "
            "driven by RV5 vs RV20 acceleration.\n"
            "- Change vs yesterday: arrow + delta shows whether we drifted "
            "toward risk-off or back to neutral overnight.\n"
            "The label gates which engines are allowed to fire (see "
            "ENABLE_GATING): Red Dog wants Transitional/Stressed, "
            "Ichimoku wants Risk-On/Transitional."
        ),
        "related_cards": [
            {"engine": "market-intel", "slug": "morning_brief", "label": "Morning Brief"},
            {"engine": "market-intel", "slug": "diff_card", "label": "Overnight Diff"},
            {"engine": "market-intel", "slug": "cross_asset_matrix", "label": "Cross-Asset Matrix"},
            {"engine": "e5",           "slug": "global_regime_score", "label": "Global Lead/Lag Regime"},
        ],
    },

    "morning_brief": {
        "title": "Morning Brief",
        "spec": (
            "A three-line LLM narrative summarizing the current market "
            "state, the weekly bias, and the top risks. Produced by "
            "backend.llm_client.generate_desk_brief from the same inputs "
            "the Regime card sees (regime, vol state, sequencer events, "
            "macro calendar).\n"
            "- market_state: one sentence describing where we are.\n"
            "- weekly_bias: what is likely to work this week.\n"
            "- top_risks: what could break the plan.\n"
            "Each line is capped at ~30 words; never mentions specific "
            "prices, trade ideas, or tickers — it's a framing tool, not "
            "a signal."
        ),
        "related_cards": [
            {"engine": "market-intel", "slug": "regime_card", "label": "Regime State"},
            {"engine": "market-intel", "slug": "patterns_card", "label": "Patterns"},
            {"engine": "e11",          "slug": "macro_calendar", "label": "Macro Calendar (E11)"},
        ],
    },

    "patterns_card": {
        "title": "Pattern Detection",
        "spec": (
            "Rolling cross-asset pattern scanner — looks for recognized "
            "setups over the last 14 sessions. Pattern family examples:\n"
            "- vol_compression_at_support: SPX near prior support with "
            "RV20 bottoming. Historically precedes a grind-up.\n"
            "- credit_decoupling: HYG/LQD cracking while SPX holds — "
            "regime-transition warning.\n"
            "- dealer_gamma_flip: SPX crossing the zero-gamma line — "
            "volatility regime shift.\n"
            "- breadth_thrust: ≥90% of Russell 3000 up on the session — "
            "bullish thrust bias for days/weeks out.\n"
            "Each pattern shows confidence (0-100) and the lookback "
            "matches that seeded it."
        ),
        "related_cards": [
            {"engine": "market-intel", "slug": "regime_card", "label": "Regime State"},
            {"engine": "market-intel", "slug": "asset_tile", "label": "Asset Tiles"},
            {"engine": "e5",           "slug": "vol_leadlag", "label": "Vol Lead-Lag"},
        ],
    },

    "diff_card": {
        "title": "Overnight to Open Diff",
        "spec": (
            "Day-over-day delta across the tracked universe since "
            "yesterday's close — points you at what actually changed.\n"
            "- Top movers: largest absolute % moves across the asset "
            "universe (SPX futures, VIX, DXY, crude, gold, BTC, rates).\n"
            "- Correlation breaks: pairs whose 20d correlation drifted "
            "> 0.3 overnight.\n"
            "- Regime proximity: how close we are to crossing a regime "
            "threshold today (if green was 62 yesterday, threshold is "
            "65, we're inside 3 pts).\n"
            "If no meaningful overnight change, card says 'Quiet tape' — "
            "use that to calibrate urgency."
        ),
        "related_cards": [
            {"engine": "market-intel", "slug": "regime_card", "label": "Regime State"},
            {"engine": "market-intel", "slug": "morning_brief", "label": "Morning Brief"},
            {"engine": "market-intel", "slug": "cross_asset_matrix", "label": "Cross-Asset Matrix"},
        ],
    },

    "asset_tile": {
        "title": "Asset Tile",
        "spec": (
            "Per-asset drill-down card (SPX, VIX, TLT, HYG, DXY, crude, "
            "gold, BTC, etc.). Each tile shows:\n"
            "- spot + 1d / 5d / 20d % change.\n"
            "- IV rank (for optioned assets) and RV20 percentile.\n"
            "- Stress flags: elevated vol, cross-asset correlation "
            "anomaly, macro-event proximity.\n"
            "- asset-insight body: LLM narrative grounded in the tile's "
            "numbers (what the move means for the rest of the desk).\n"
            "Scoped to the single asset — use the Cross-Asset Matrix card "
            "for inter-asset context."
        ),
        "related_cards": [
            {"engine": "market-intel", "slug": "cross_asset_matrix", "label": "Cross-Asset Matrix"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Regime State"},
            {"engine": "e5",           "slug": "vol_leadlag", "label": "Vol Lead-Lag"},
        ],
    },

    "cross_asset_matrix": {
        "title": "Cross-Asset Matrix",
        "spec": (
            "Heatmap of rolling correlations between the tracked asset "
            "universe (typically 20-day Pearson). Cells color from red "
            "(strong negative) through white (zero) to green (strong "
            "positive). Brackets flag anomaly cells where today's corr "
            "diverges from the 60-day mean by > 1 sigma.\n"
            "This is the at-a-glance map of 'what's moving together' — "
            "when a normally-green cell goes white, something has "
            "decoupled, and that's usually the most important information "
            "in the whole morning pack."
        ),
        "related_cards": [
            {"engine": "market-intel", "slug": "regime_card", "label": "Regime State"},
            {"engine": "market-intel", "slug": "asset_tile", "label": "Asset Tile"},
            {"engine": "e5",           "slug": "global_regime_score", "label": "Global Lead/Lag Regime"},
        ],
    },

}
