"""Desk Insight catalog — Engine 12 (VIX Spike Fade)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e12",
    "name":        "Engine 12 — VIX Spike Fade / Vol Dislocation",
    "description": (
        "After a VIX spike, Engine 12 simulates forward vol paths using "
        "an Ornstein-Uhlenbeck model calibrated to 5 years of VIX "
        "history + a cross-asset stress score + dealer gamma "
        "amplification, and estimates the probability of containment vs "
        "secondary spike over the next 5-10 sessions."
    ),
    "asset_class": "VIX / volatility products",
}


CATALOG = {

    "spike_state": {
        "title": "Spike State",
        "spec": (
            "Current VIX regime and spike characterization:\n"
            "- VIX level + percentile rank.\n"
            "- Days since last close below X (freshness of the spike).\n"
            "- VVIX (vol-of-vol) regime.\n"
            "- Dealer gamma amplification factor (see "
            "ENGINE12_GAMMA_AMP_* knobs).\n"
            "This is the 'what is happening right now' snapshot before "
            "the simulator gives you forward odds."
        ),
        "related_cards": [
            {"engine": "e12", "slug": "mc_forecast", "label": "MC Forecast"},
            {"engine": "e12", "slug": "cross_asset_stress", "label": "Cross-Asset Stress"},
            {"engine": "e12", "slug": "playbook", "label": "Fade Playbook"},
        ],
    },

    "mc_forecast": {
        "title": "Monte Carlo Forecast",
        "spec": (
            "Forward-path distribution over the next 5-10 sessions, "
            "N = ENGINE12_MC_N_SIMS (typically 10,000) using an "
            "OU process calibrated to "
            "ENGINE12_OU_CALIBRATION_LOOKBACK_DAYS (default 1260 = 5y) "
            "of VIX history, then amplified by the current dealer-"
            "gamma factor.\n"
            "Outputs:\n"
            "- p_contained: probability VIX stays below X threshold.\n"
            "- p_secondary_spike: probability of a > 25% intraday "
            "jump in the window.\n"
            "- Fade verdict: AGGRESSIVE (p_contained > 0.60) / STANDARD "
            "(0.40-0.60) / GATED (p_secondary > 0.25)."
        ),
        "related_cards": [
            {"engine": "e12", "slug": "spike_state", "label": "Spike State"},
            {"engine": "e12", "slug": "cross_asset_stress", "label": "Cross-Asset Stress"},
            {"engine": "e12", "slug": "playbook", "label": "Fade Playbook"},
        ],
    },

    "cross_asset_stress": {
        "title": "Cross-Asset Stress",
        "spec": (
            "Composite stress read across crude (30%), gold (20%), "
            "HYG (20%), DXY (15%), TLT vol (15%) — weights from "
            "ENGINE12_STRESS_WEIGHT_* knobs.\n"
            "High composite stress means the VIX spike is "
            "corroborated by other asset classes — real regime "
            "transition, not a single-market blip. Low composite "
            "stress with high VIX = single-asset event; easier to fade."
        ),
        "related_cards": [
            {"engine": "e12", "slug": "spike_state", "label": "Spike State"},
            {"engine": "e9",  "slug": "credit_stress_score", "label": "Credit Stress (E9)"},
            {"engine": "e5",  "slug": "global_regime_score", "label": "Global Regime (E5)"},
        ],
    },

    "playbook": {
        "title": "Fade Playbook",
        "spec": (
            "Structured trade ideas for the current MC verdict:\n"
            "- AGGRESSIVE: short VIX call spreads, short futures, "
            "near-dated premium.\n"
            "- STANDARD: calendar spreads, partial short, wait-for-"
            "pullback entries.\n"
            "- GATED: stay flat until p_secondary drops; the secondary-"
            "spike risk dominates any fade edge.\n"
            "Each playbook entry includes invalidation criteria."
        ),
        "related_cards": [
            {"engine": "e12", "slug": "mc_forecast", "label": "MC Forecast"},
            {"engine": "e12", "slug": "spike_state", "label": "Spike State"},
        ],
    },

    "re_simulate_hint": {
        "title": "Re-Simulate Hint",
        "spec": (
            "The MC forecast uses cached inputs (5-min TTL per "
            "ENGINE12_CACHE_TTL_SCAN); after major tape events (FOMC "
            "decision, intraday 5% VIX move) the cache should be burst "
            "to pull a fresh simulation.\n"
            "This card surfaces 'when to re-run' heuristics: cache "
            "age, stale-input flags, and a one-click refresh button."
        ),
        "related_cards": [
            {"engine": "e12", "slug": "mc_forecast", "label": "MC Forecast"},
        ],
    },

}
