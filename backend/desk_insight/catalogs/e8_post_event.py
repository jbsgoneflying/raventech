"""Desk Insight catalog — Engine 8 (Post-Event Extension Evaluator)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e8",
    "name":        "Engine 8 — Post-Event Extension Evaluator",
    "description": (
        "After an earnings event or binary catalyst, Engine 8 evaluates "
        "whether the initial move is likely to EXTEND (continuation) or "
        "REVERT (fade) in the following days. Combines similar-event "
        "history, EM-ratio, ATR regime, and an LLM event classifier."
    ),
    "asset_class": "post-event single-name equities",
}


CATALOG = {

    "activation_scan": {
        "title": "Activation Scan",
        "spec": (
            "Scans recent post-event names for actionable continuation "
            "or fade setups. Each candidate shows:\n"
            "- verdict: CONTINUE / FADE / PASS.\n"
            "- confidence score (0-100) — must exceed "
            "ENGINE8_CONFIDENCE_THRESHOLD to surface.\n"
            "- historical sample size: similar events in the last "
            "ENGINE8_LOOKBACK_EVENTS.\n"
            "- probability: estimated continuation or reversion prob "
            "given the sample.\n"
            "Confidence floors for CONTINUE (65) and FADE (70) are "
            "asymmetric — fading a move requires a higher bar."
        ),
        "related_cards": [
            {"engine": "e8", "slug": "setup_detail", "label": "Setup Detail"},
            {"engine": "e8", "slug": "desk_notes", "label": "Desk Notes"},
            {"engine": "e8", "slug": "playbook", "label": "Playbook"},
        ],
    },

    "setup_detail": {
        "title": "Setup Detail (per-candidate)",
        "spec": (
            "Per-candidate drill-down: event type (earnings / guidance "
            "/ FDA / M&A), move-vs-EM ratio (ENGINE8_EM_RATIO_OVER vs "
            "EXTREME thresholds), ATR regime (ELEVATED vs EXTREME), "
            "price action chip, recent news summary.\n"
            "Move-vs-EM > 1.5 (EXTREME) is the classic fade-setup "
            "flag — the market priced-in less than what actually "
            "happened, so some reversion is common."
        ),
        "related_cards": [
            {"engine": "e8", "slug": "activation_scan", "label": "Activation Scan"},
            {"engine": "e8", "slug": "similar_events", "label": "Similar Events"},
        ],
    },

    "similar_events": {
        "title": "Similar Events",
        "spec": (
            "Historical audit of similar-event cases used to score the "
            "current candidate. Each row shows the prior date, the "
            "move-vs-EM on day 0, the 1d / 3d / 5d follow-through, and "
            "which verdict the history supports.\n"
            "If the sample is below ENGINE8_MIN_HISTORICAL_SAMPLE "
            "(typically 8), Engine 8 tries relaxed matching; if still "
            "sparse, confidence is capped."
        ),
        "related_cards": [
            {"engine": "e8", "slug": "setup_detail", "label": "Setup Detail"},
            {"engine": "e8", "slug": "activation_scan", "label": "Activation Scan"},
        ],
    },

    "desk_notes": {
        "title": "Desk Notes",
        "spec": (
            "LLM narrative brief for the current scan — what the "
            "historical pattern says, what to watch for in the next 1-5 "
            "sessions, and the risk management framing (holding period, "
            "size, trail stops).\n"
            "Never a trade recommendation. Serves as the 'what the desk "
            "would say to a junior' version of the page. The brief is "
            "grounded in the Activation Scan verdict + Similar Events "
            "table, so it can't hallucinate a thesis that contradicts "
            "the historical record beneath it."
        ),
        "related_cards": [
            {"engine": "e8", "slug": "activation_scan", "label": "Activation Scan"},
            {"engine": "e8", "slug": "playbook", "label": "Playbook"},
        ],
    },

    "playbook": {
        "title": "Playbook",
        "spec": (
            "Standard operating procedure for the setup family that "
            "fired today. Example: 'EM-ratio > 1.5 on earnings gap "
            "down → fade with call spread; expected hold 2-3 days; "
            "invalidation = new lows on volume.'\n"
            "Playbooks are curated by the desk and pinned here — they "
            "ARE the knowledge base a new hire learns from. Each one "
            "has entry rules, exit rules, and a documented failure "
            "mode."
        ),
        "related_cards": [
            {"engine": "e8", "slug": "desk_notes", "label": "Desk Notes"},
            {"engine": "e8", "slug": "similar_events", "label": "Similar Events"},
        ],
    },

}
