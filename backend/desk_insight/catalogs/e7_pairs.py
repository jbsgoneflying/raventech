"""Desk Insight catalog — Engine 7 (Thematic Pairs Scanner)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e7",
    "name":        "Engine 7 — Thematic Pairs Scanner",
    "description": (
        "Thematic relative-value / pairs engine. Scans curated themes "
        "(semis, refiners, REITs, etc.) for dislocated ratio spreads "
        "suitable for mean-reversion or momentum pair trades. Uses "
        "rolling z-scores and IV overlay scoring for confirmation."
    ),
    "asset_class": "sector-paired US equities",
}


CATALOG = {

    "pair_card": {
        "title": "Pair Card",
        "spec": (
            "Each pair row shows the thematic context, current ratio, "
            "rolling z-score of the ratio (window = ENGINE7_Z_SCORE_"
            "WINDOW), entry threshold, direction (mean-rev or "
            "momentum), expected hold, and the pair's IV overlay score.\n"
            "|z| ≥ ENGINE7_Z_ENTRY_THRESHOLD fires mean-reversion; "
            "|z| ≥ ENGINE7_Z_MOMENTUM_THRESHOLD fires momentum. Sign "
            "tells direction; magnitude tells strength."
        ),
        "related_cards": [
            {"engine": "e7", "slug": "theme_context", "label": "Theme Context"},
            {"engine": "e7", "slug": "desk_view", "label": "Desk View"},
            {"engine": "e7", "slug": "overlap_risk", "label": "Overlap Risk"},
        ],
    },

    "theme_context": {
        "title": "Theme Context",
        "spec": (
            "Why these two names belong in the same pair. Engine 7 "
            "requires theme validation (INV-2) — no random "
            "high-correlation pairs. Themes are currated (semis, "
            "refiners, travel, cloud) and each pair must belong to one "
            "active theme, else it's rejected.\n"
            "Theme card shows: current theme regime (expanding / "
            "contracting / stalled), member breadth, and a news chip "
            "if a theme-level event is near."
        ),
        "related_cards": [
            {"engine": "e7", "slug": "pair_card", "label": "Pair Card"},
            {"engine": "e7", "slug": "desk_view", "label": "Desk View"},
        ],
    },

    "desk_view": {
        "title": "Desk View",
        "spec": (
            "LLM desk-view narrative for a specific pair: thesis, "
            "market context, entry trigger, exit triggers, scenario "
            "analysis, risks. This is richer than a tooltip — it's the "
            "full desk brief for the trade.\n"
            "Grounded in the pair's live z-score, ratio history, IV "
            "overlay, and theme context. Never recommends specific "
            "size. Serves as documentation you can paste into journal."
        ),
        "related_cards": [
            {"engine": "e7", "slug": "pair_card", "label": "Pair Card"},
            {"engine": "e7", "slug": "theme_context", "label": "Theme Context"},
            {"engine": "e7", "slug": "overlap_risk", "label": "Overlap Risk"},
        ],
    },

    "overlap_risk": {
        "title": "Overlap Risk (INV-3)",
        "spec": (
            "Prevents the desk from accidentally stacking correlated "
            "pairs (e.g. two variants of the same oil trade). Every new "
            "pair's ratio-return series is correlated against the "
            "existing open book; corr > ENGINE7_OVERLAP_CORR_THRESHOLD "
            "triggers a WARN chip.\n"
            "Also enforces ENGINE7_MAX_CONCURRENT_PAIRS (typically 5) — "
            "past that, new entries are blocked."
        ),
        "related_cards": [
            {"engine": "e7", "slug": "pair_card", "label": "Pair Card"},
            {"engine": "e7", "slug": "desk_view", "label": "Desk View"},
        ],
    },

    "iv_overlay": {
        "title": "IV Overlay",
        "spec": (
            "When ORATS data is available (ENGINE7_ENABLE_ORATS_VOL), "
            "each pair gets an IV-based confirmation score. Does the "
            "options market agree with the equity-level dislocation?\n"
            "- IV spread between the pair matches direction → high-"
            "confidence.\n"
            "- IV spread opposes the equity move → degraded score; the "
            "options market is fading the thesis."
        ),
        "related_cards": [
            {"engine": "e7", "slug": "pair_card", "label": "Pair Card"},
            {"engine": "e7", "slug": "desk_view", "label": "Desk View"},
        ],
    },

}
