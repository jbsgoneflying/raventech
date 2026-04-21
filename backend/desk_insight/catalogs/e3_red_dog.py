"""Desk Insight catalog — Engine 3 (Red Dog Mean-Reversion Scanner)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e3",
    "name":        "Engine 3 — Red Dog Mean-Reversion Scanner",
    "description": (
        "Scans liquid large-caps for short-term mean-reversion setups. "
        "Ranks by a 0-100 score combining over-extension, exhaustion "
        "signals, volume anomaly, and regime fit. A+ (≥ 75) are the "
        "headline candidates; 50-75 are watchlist."
    ),
    "asset_class": "liquid US large-cap equities",
}


CATALOG = {

    "scanner_result": {
        "title": "Scanner Result",
        "spec": (
            "Ranked list of mean-reversion candidates. Each row: ticker, "
            "setup grade (A+ / A / B / C), score (0-100), extension %, "
            "reason chips, and a sparkline.\n"
            "Grades are score-binned: A+ ≥ 75, A ≥ 65, B ≥ 50, C < 50. "
            "Only A and A+ are considered actionable by the desk; "
            "anything below B is for audit / thesis-check only."
        ),
        "related_cards": [
            {"engine": "e3", "slug": "setup_card", "label": "Setup Card"},
            {"engine": "e3", "slug": "regime_gate", "label": "Regime Gate"},
            {"engine": "e3", "slug": "score_breakdown", "label": "Score Breakdown"},
        ],
    },

    "setup_card": {
        "title": "Setup Card (per-ticker)",
        "spec": (
            "Per-ticker drill-down: current price, 5d move, 20d volume "
            "ratio, RSI, distance from 20/50/200 SMA, ATR, recent "
            "headline chip (if any).\n"
            "Reads as a checklist: is the name overextended? Is volume "
            "anomalous? Is there an exhaustion candle pattern? The "
            "card shows the specific evidence behind the score so you "
            "can trust-but-verify before risking size."
        ),
        "related_cards": [
            {"engine": "e3", "slug": "score_breakdown", "label": "Score Breakdown"},
            {"engine": "e3", "slug": "scanner_result", "label": "Scanner Result"},
        ],
    },

    "score_breakdown": {
        "title": "Score Breakdown",
        "spec": (
            "The 0-100 score decomposed into its contributors:\n"
            "- Extension (0-30): how stretched from moving averages.\n"
            "- Exhaustion (0-25): candle-pattern / momentum divergence.\n"
            "- Volume anomaly (0-20): today's volume vs 20d average.\n"
            "- Liquidity (0-15): avg daily volume & options quality.\n"
            "- Regime fit (0-10): current market regime vs historical "
            "win rates for this setup family.\n"
            "A score of 75 with 30 from extension but only 5 from regime "
            "fit = a technical A+ fighting a structural headwind."
        ),
        "related_cards": [
            {"engine": "e3", "slug": "setup_card", "label": "Setup Card"},
            {"engine": "e3", "slug": "regime_gate", "label": "Regime Gate"},
        ],
    },

    "regime_gate": {
        "title": "Regime Gate",
        "spec": (
            "Whether today's market regime permits Red Dog to fire.\n"
            "- Allowed regimes (GATE_RD_REGIME_ALLOW): Transitional, "
            "Stressed — mean reversion works best when vol is expanding.\n"
            "- Allowed vol states (GATE_RD_VOL_STATE_ALLOW): expanding, "
            "unstable, rising.\n"
            "- Macro proximity: blocks trades within "
            "GATE_RD_MACRO_PROXIMITY_DAYS of a high-impact event.\n"
            "When gated off, the scanner still runs but every card "
            "shows a GATE OFF chip and the desk should treat signals as "
            "shadow-mode only."
        ),
        "related_cards": [
            {"engine": "e3",           "slug": "scanner_result", "label": "Scanner Result"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Regime"},
        ],
    },

    "position_sizing": {
        "title": "Position Sizing",
        "spec": (
            "Suggested size for each A/A+ candidate based on ATR-implied "
            "risk + entry quality. Uses a fixed-fractional framework "
            "(risk = X% of equity per unit of ATR at the stop).\n"
            "Never overrides desk risk policy — this is a framing tool. "
            "If the candidate's score is ≥ 75 and regime is allowed, the "
            "suggested size is at full-unit; tighter regimes shrink to "
            "half-unit."
        ),
        "related_cards": [
            {"engine": "e3", "slug": "setup_card", "label": "Setup Card"},
            {"engine": "e3", "slug": "regime_gate", "label": "Regime Gate"},
        ],
    },

}
