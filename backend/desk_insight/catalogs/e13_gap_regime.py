"""Desk Insight catalog — Engine 13 (Gap Regime Scanner).

Previously tooltip-less. First-class coverage for overnight-gap regime
classification and fragility scoring.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "e13",
    "name":        "Engine 13 — Gap Regime Scanner",
    "description": (
        "Classifies the current overnight-gap environment for SPX and "
        "correlated vehicles. Measures gap frequency, gap-vs-close "
        "follow-through, and gappy-vs-gappy-less day clustering to "
        "feed a composite fragility score used by E14/E15 conditioning."
    ),
    "asset_class": "SPX + cross-asset overnight-gap behavior",
}


CATALOG = {

    "gap_regime_card": {
        "title": "Gap Regime",
        "spec": (
            "Today's gap regime classification:\n"
            "- Sticky: gaps tend to extend (momentum favored).\n"
            "- Reversion: gaps tend to fade to yesterday's close.\n"
            "- Mixed: no clear bias — treat gaps as noise.\n"
            "- Fragile: large gaps clustering with high realized vol — "
            "everything breaks; premium-sale IC structures struggle.\n"
            "Regime is classified from the last 20 sessions' gap "
            "distribution and is refreshed daily. Fragile regimes fire "
            "a warn chip that flows into E14/E15 conditioning."
        ),
        "related_cards": [
            {"engine": "e13", "slug": "fragility_score", "label": "Fragility Score"},
            {"engine": "e13", "slug": "gap_history", "label": "Gap History"},
            {"engine": "e14", "slug": "modifiers", "label": "E14 Conditioning"},
        ],
    },

    "fragility_score": {
        "title": "Fragility Score",
        "spec": (
            "Composite 0-100 fragility score combining:\n"
            "- Options (30%): IV rank, skew, call/put ratio.\n"
            "- Cross-asset (25%): credit + FX + commodity stress.\n"
            "- Historical (20%): recent vol expansion rate.\n"
            "- Headline (15%): Benzinga severity in last 5 sessions.\n"
            "- Price action (10%): intraday range / gap persistence.\n"
            "Weights live in ENGINE13_FRAGILITY_W_* knobs. Above 65 = "
            "fragile regime; IC breach tails widen."
        ),
        "related_cards": [
            {"engine": "e13", "slug": "gap_regime_card", "label": "Gap Regime"},
            {"engine": "e14", "slug": "outcome_adjusted", "label": "E14 Adjusted Distribution"},
            {"engine": "e9",  "slug": "credit_stress_score", "label": "Credit Stress"},
        ],
    },

    "gap_history": {
        "title": "Gap History",
        "spec": (
            "Trailing 40-session gap distribution: each row is a gap "
            "event (|% change overnight| ≥ "
            "ENGINE13_GAP_THRESHOLD_PCT, typically 1.5%), with the "
            "gap direction, intraday follow-through, and whether the "
            "gap closed by EOD.\n"
            "Use this to sanity-check the regime label — if Fragile "
            "but the table shows 3 clean reversions in the last 10 "
            "gaps, the composite may be lagging recent behavior."
        ),
        "related_cards": [
            {"engine": "e13", "slug": "gap_regime_card", "label": "Gap Regime"},
            {"engine": "e13", "slug": "fragility_score", "label": "Fragility Score"},
        ],
    },

    "analogue_events": {
        "title": "Analogue Events",
        "spec": (
            "Historical 5-session windows that matched the current gap "
            "regime + fragility score profile. Each analogue shows "
            "the date range, SPX P&L over the window, VIX behavior, "
            "and what worked / what broke for common strategies.\n"
            "Treat as 'what happened last time we looked like this' — "
            "not a prediction, a reference set to calibrate expectations."
        ),
        "related_cards": [
            {"engine": "e13", "slug": "gap_regime_card", "label": "Gap Regime"},
            {"engine": "e13", "slug": "fragility_score", "label": "Fragility Score"},
        ],
    },

}
