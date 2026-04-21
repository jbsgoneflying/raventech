"""Desk Insight catalog — Engine 5 (Global Lead-Lag Regime)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e5",
    "name":        "Engine 5 — Global Lead-Lag Regime",
    "description": (
        "Cross-region / cross-asset lead-lag detector. Identifies which "
        "markets are leading and which are following based on rolling "
        "correlations, z-scores, and time-shifted coherence. Surfaces "
        "a weekly idea set and a global regime score."
    ),
    "asset_class": "global cross-asset (ES, NDX, Nikkei, HSI, DAX, bonds, FX, credit, vol)",
}


CATALOG = {

    "global_regime_score": {
        "title": "Global Regime Score",
        "spec": (
            "Composite score (0-100) summarizing cross-asset stress.\n"
            "- Higher = more stress. Labels: Risk-On (< 30), "
            "Transitional (30-55), Risk-Off (55-75), Stressed (> 75). "
            "Thresholds in ENGINE5_REGIME_*_THRESHOLD.\n"
            "- Inputs: cross-asset correlation breakdown, flight-to-"
            "quality flows (bonds vs equities), credit-vol divergence, "
            "FX DXY drift, global IV percentile.\n"
            "- Direction chip: 'rising' / 'falling' / 'steady' based on "
            "5-day slope.\n"
            "This is the topmost read of 'are we risk-on or risk-off "
            "globally' — more stable than any single-market signal."
        ),
        "related_cards": [
            {"engine": "e5",           "slug": "lead_lag_matrix", "label": "Lead-Lag Matrix"},
            {"engine": "e5",           "slug": "vol_leadlag", "label": "Vol Lead-Lag"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Regime"},
        ],
    },

    "lead_lag_matrix": {
        "title": "Lead-Lag Matrix",
        "spec": (
            "Pairwise time-shifted correlation heatmap. Cells show which "
            "market leads which by how many days, with what correlation "
            "strength. Green = strong positive leader, red = strong "
            "negative leader.\n"
            "When ES leads NDX by 1 day with corr > 0.8, the overnight "
            "ES print is a high-signal read on NDX. When the usual "
            "leader/follower relationship inverts (NDX starts leading "
            "ES), something structural has shifted."
        ),
        "related_cards": [
            {"engine": "e5", "slug": "global_regime_score", "label": "Global Regime"},
            {"engine": "e5", "slug": "weekly_ideas", "label": "Weekly Ideas"},
        ],
    },

    "vol_leadlag": {
        "title": "Vol Lead-Lag",
        "spec": (
            "Sub-module tracking global IV movement: Global Vol Score, "
            "US IV rank, Asian IV rank, European IV rank, vol regime "
            "state (RISING / FALLING / NORMAL per "
            "ENGINE5_GLOBAL_VOL_* thresholds).\n"
            "When US IV is LOW but Global Vol Score is RISING, foreign "
            "vol is signaling that US vol will catch up — premium "
            "sellers should tighten wings pre-emptively."
        ),
        "related_cards": [
            {"engine": "e5",           "slug": "global_regime_score", "label": "Global Regime"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Regime"},
            {"engine": "e2",           "slug": "regime_card", "label": "SPX Regime"},
        ],
    },

    "weekly_ideas": {
        "title": "Weekly Ideas",
        "spec": (
            "LLM-curated weekly idea set derived from the lead-lag matrix "
            "and current regime: 'vol is leading down in EUR — fade "
            "German equity vol', 'BTC decoupling from NDX — paired "
            "short-gamma', etc.\n"
            "Each idea shows confidence, thesis, and a risk note. "
            "Shadow-mode only — the desk should treat these as "
            "brainstorming seeds, not trade instructions."
        ),
        "related_cards": [
            {"engine": "e5", "slug": "lead_lag_matrix", "label": "Lead-Lag Matrix"},
            {"engine": "e5", "slug": "global_regime_score", "label": "Global Regime"},
        ],
    },

    "snapshot_archive": {
        "title": "Snapshot Archive",
        "spec": (
            "Immutable historical snapshots of the lead-lag matrix + "
            "regime score, persisted in Redis for up to "
            "ENGINE5_SNAPSHOT_TTL_S seconds (14 days by default). Use "
            "this to replay 'what did we see on date X' when doing "
            "trade post-mortems or calibrating our regime thresholds "
            "to prior stress events. Each snapshot also stores the vol "
            "lead-lag state, so you can step back through vol-regime "
            "history independently of the cross-asset correlation "
            "matrix."
        ),
        "related_cards": [
            {"engine": "e5", "slug": "global_regime_score", "label": "Global Regime"},
            {"engine": "e5", "slug": "lead_lag_matrix", "label": "Lead-Lag Matrix"},
        ],
    },

}
