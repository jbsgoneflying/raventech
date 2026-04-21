"""Desk Insight catalog — Engine 9 (Credit Stress Drift Detection)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e9",
    "name":        "Engine 9 — Credit Stress Drift Detection",
    "description": (
        "Monitors credit-market stress indicators (HYG/LQD ratios, "
        "investment-grade vs high-yield spreads, Treasury term "
        "structure) and flags drift patterns that historically precede "
        "equity volatility regime shifts."
    ),
    "asset_class": "credit + cross-asset stress inputs",
}


CATALOG = {

    "credit_stress_score": {
        "title": "Credit Stress Score",
        "spec": (
            "Composite credit-stress index (0-100). Higher = more "
            "stress in credit markets.\n"
            "- Inputs: HYG/LQD ratio z-score, credit-vs-equity-vol "
            "divergence, Treasury term structure (10y-2y), LQD drawdown "
            "vs 200d.\n"
            "- Direction chip: rising / falling / stable (5d slope).\n"
            "Historically, credit leads equity vol by 1-3 weeks — when "
            "this score rises while SPX vol is compressing, the desk "
            "should lean defensive on premium-sale structures."
        ),
        "related_cards": [
            {"engine": "e9",           "slug": "drift_detection", "label": "Drift Detection"},
            {"engine": "e9",           "slug": "hyg_lqd_panel", "label": "HYG/LQD Panel"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Regime"},
        ],
    },

    "drift_detection": {
        "title": "Drift Detection",
        "spec": (
            "Alerts when the credit-stress score has moved ≥ X sigma "
            "over the last 5 / 10 / 20 sessions. Distinguishes:\n"
            "- Transient spike (single session) — typically noise.\n"
            "- Sustained drift (≥ 3 sessions in one direction) — the "
            "real signal; this is what precedes regime changes.\n"
            "- Reversal drift (direction change after a trend) — can "
            "signal early all-clear or false dawn.\n"
            "Drift state feeds into Engine 14 / Engine 15 conditioning "
            "modifiers."
        ),
        "related_cards": [
            {"engine": "e9",  "slug": "credit_stress_score", "label": "Credit Stress Score"},
            {"engine": "e14", "slug": "modifiers", "label": "E14 Conditioning Modifiers"},
            {"engine": "e15", "slug": "conditioning_modifiers", "label": "E15 Conditioning"},
        ],
    },

    "hyg_lqd_panel": {
        "title": "HYG / LQD Panel",
        "spec": (
            "Detailed cut of the HYG (high-yield) and LQD (investment "
            "grade) corporate-bond ETFs: 5d / 20d returns, 20d "
            "volatility, ratio vs SPX, optional option-IV overlay "
            "when available.\n"
            "The HYG/LQD ratio is the workhorse — when it drops (HY "
            "underperforming IG) while equities hold, it's the most "
            "reliable early-warning signal in the kit."
        ),
        "related_cards": [
            {"engine": "e9", "slug": "credit_stress_score", "label": "Credit Stress Score"},
            {"engine": "e9", "slug": "drift_detection", "label": "Drift Detection"},
        ],
    },

    "term_structure": {
        "title": "Term Structure",
        "spec": (
            "Treasury yield-curve snapshot focused on the 2y / 5y / 10y "
            "/ 30y nodes and the classic 10y-2y spread. Inverted curve "
            "(10y < 2y) is a historical recession precursor; steep "
            "re-flattening after inversion is usually the last bullish "
            "window before volatility expands.\n"
            "The card also shows the 5s30s slope and the real-yield "
            "band — when real yields push above 2% while the curve "
            "steepens from inversion, the macro regime typically "
            "supports short-vol; when nominal steepens but real yields "
            "fall, that's flight-to-quality and premium sellers should "
            "brace for tail expansion."
        ),
        "related_cards": [
            {"engine": "e9",  "slug": "credit_stress_score", "label": "Credit Stress Score"},
            {"engine": "e11", "slug": "macro_calendar", "label": "Macro Calendar"},
        ],
    },

}
