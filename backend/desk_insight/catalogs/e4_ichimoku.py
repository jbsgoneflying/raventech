"""Desk Insight catalog — Engine 4 (Ichimoku Cloud Continuation)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e4",
    "name":        "Engine 4 — Ichimoku Cloud Continuation Scanner",
    "description": (
        "Trend-continuation scanner using Ichimoku cloud architecture: "
        "tenkan-kijun cross, cloud-thickness, chikou confirmation. The "
        "mirror-image of Red Dog — fires in trending Risk-On regimes "
        "rather than stressed ones."
    ),
    "asset_class": "liquid US large-cap equities",
}


CATALOG = {

    "scanner_result": {
        "title": "Scanner Result",
        "spec": (
            "Ranked list of trend-continuation candidates. Each row: "
            "ticker, setup grade (A+ / A / B / C), score (0-100), cloud "
            "posture (above/inside/below), chikou confirmation, TK cross "
            "freshness (bars since).\n"
            "A+ ≥ 75, A ≥ 65. The desk treats A/A+ as actionable; B is "
            "audit-only; C is ignored. Fresh TK crosses (< 5 bars since) "
            "carry an extra momentum chip — they represent entries still "
            "in the classical 'follow-through window' rather than late-"
            "stage continuation trades where the easy move has already "
            "played out."
        ),
        "related_cards": [
            {"engine": "e4", "slug": "ichimoku_anatomy", "label": "Ichimoku Anatomy"},
            {"engine": "e4", "slug": "regime_gate", "label": "Regime Gate"},
            {"engine": "e4", "slug": "score_breakdown", "label": "Score Breakdown"},
        ],
    },

    "ichimoku_anatomy": {
        "title": "Ichimoku Anatomy",
        "spec": (
            "Visual breakdown of the five Ichimoku lines for the ticker:\n"
            "- Tenkan (9-period conversion line).\n"
            "- Kijun (26-period base line).\n"
            "- Senkou A / B (cloud boundaries).\n"
            "- Chikou (lagging span — price vs 26 bars back).\n"
            "The classic bullish setup: price above cloud, Tenkan above "
            "Kijun, Chikou above price-26-ago, cloud twist confirming "
            "upward bias. Partial confirmation = B-grade; full = A/A+."
        ),
        "related_cards": [
            {"engine": "e4", "slug": "score_breakdown", "label": "Score Breakdown"},
            {"engine": "e4", "slug": "scanner_result", "label": "Scanner Result"},
        ],
    },

    "score_breakdown": {
        "title": "Score Breakdown",
        "spec": (
            "Score decomposition:\n"
            "- Cloud posture (0-25): distance + direction above cloud.\n"
            "- TK alignment (0-20): Tenkan/Kijun cross + spread.\n"
            "- Chikou (0-15): lagging span confirmation.\n"
            "- Cloud thickness (0-15): thicker cloud = stronger support.\n"
            "- Trend quality (0-15): R² of a price regression.\n"
            "- Regime fit (0-10): current market regime vs trend-friendly."
        ),
        "related_cards": [
            {"engine": "e4", "slug": "ichimoku_anatomy", "label": "Ichimoku Anatomy"},
            {"engine": "e4", "slug": "regime_gate", "label": "Regime Gate"},
        ],
    },

    "regime_gate": {
        "title": "Regime Gate",
        "spec": (
            "Regime filter for trend-continuation:\n"
            "- Allowed regimes (GATE_ICH_REGIME_ALLOW): Risk-On, "
            "Transitional.\n"
            "- Allowed vol states (GATE_ICH_VOL_STATE_ALLOW): "
            "compressing, stable, flat, falling — trends thrive in "
            "steady-vol tapes.\n"
            "- Macro proximity: blocks within "
            "GATE_ICH_MACRO_PROXIMITY_DAYS of high-impact events.\n"
            "Note: Red Dog (E3) and Ichimoku (E4) are near-mutually-"
            "exclusive by design — when one is gated on, the other is "
            "usually gated off."
        ),
        "related_cards": [
            {"engine": "e4",           "slug": "scanner_result", "label": "Scanner Result"},
            {"engine": "e3",           "slug": "regime_gate", "label": "Red Dog Gate"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Regime"},
        ],
    },

    "position_sizing": {
        "title": "Position Sizing",
        "spec": (
            "ATR-scaled position sizing for trend entries. Uses cloud-"
            "bottom as a natural stop anchor; risk-per-unit = distance "
            "from entry to cloud-bottom times position size. Tight "
            "clouds favor bigger size at the same % risk; thick clouds "
            "shrink size because the stop is further away.\n"
            "Tie-breaker: if two candidates carry equal score but "
            "different cloud thickness, the thicker-cloud name is the "
            "better entry because the 'invalidation room' is more robust "
            "to noise. Pair this card with the Regime Gate card to "
            "confirm the broader tape is aligned."
        ),
        "related_cards": [
            {"engine": "e4", "slug": "ichimoku_anatomy", "label": "Ichimoku Anatomy"},
            {"engine": "e4", "slug": "scanner_result", "label": "Scanner Result"},
        ],
    },

}
