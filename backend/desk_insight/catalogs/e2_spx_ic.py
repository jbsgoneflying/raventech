"""Desk Insight catalog — Engine 2 (SPX Iron Condor Scanner)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e2",
    "name":        "Engine 2 — SPX Iron Condor Scanner",
    "description": (
        "Weekly SPX/SPY iron condor scanner. Proposes structures for "
        "current-week entry across EM-multiple × wing-width grids; "
        "evaluates breach, MAE, P&L, win-rate, regime + macro-proximity "
        "gating. The SPX-equivalent of Engine 1."
    ),
    "asset_class": "SPX/SPY weekly iron condors",
}


CATALOG = {

    "entry_state": {
        "title": "Entry State",
        "spec": (
            "SPX entry-state strip: current SPX cash, 1σ EM to Friday "
            "expiry, current regime bucket (Low/Moderate/Elevated/High "
            "based on the ENGINE2_REGIME_* thresholds), vol state "
            "(compressing/stable/expanding/unstable), macro-proximity "
            "multiplier, and short-wing distance metrics. This is what "
            "every IC decision is referenced against — if the entry "
            "strip flags elevated regime + macro multiplier > 1.5, the "
            "whole page downgrades."
        ),
        "related_cards": [
            {"engine": "e2",           "slug": "regime_card", "label": "Regime"},
            {"engine": "e2",           "slug": "macro_proximity", "label": "Macro Proximity"},
            {"engine": "e2",           "slug": "candidate_grid", "label": "Candidate Grid"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Intel Regime"},
        ],
    },

    "regime_card": {
        "title": "Regime",
        "spec": (
            "Regime score (0-100) driven by RV20, VIX term, breadth, "
            "dealer gamma, and a few cross-asset stress inputs. Labels "
            "are Low / Moderate / Elevated / High. Thresholds live in "
            "ENGINE2_REGIME_LOW_MAX / MODERATE_MAX / ELEVATED_MAX knobs.\n"
            "- Low (< 25): chill tape; tight wings safe.\n"
            "- Moderate (25-45): default state; standard wings.\n"
            "- Elevated (45-65): widen wings or skip.\n"
            "- High (>65): most IC structures fail policy gate."
        ),
        "related_cards": [
            {"engine": "e2",           "slug": "entry_state", "label": "Entry State"},
            {"engine": "e2",           "slug": "macro_proximity", "label": "Macro Proximity"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Intel Regime"},
        ],
    },

    "candidate_grid": {
        "title": "Candidate Grid",
        "spec": (
            "Grid of proposed IC structures across EM multiples × wing "
            "widths. Each cell shows: entry credit, breach %, MAE95 × "
            "wing, avg P&L, and a compliance chip (pass / warn / block "
            "against ENGINE2_POLICY_MAX_* knobs).\n"
            "Pass-cells are actionable structures. Warn-cells need a "
            "reason to touch (tiny size or explicit vol view). Block-"
            "cells fail policy and should be ignored unless you're "
            "overriding the gate deliberately."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "policy_gate", "label": "Policy Gate"},
            {"engine": "e2", "slug": "breach_table", "label": "Breach Table"},
            {"engine": "e2", "slug": "ai_advisor", "label": "AI Advisor"},
        ],
    },

    "policy_gate": {
        "title": "Policy Gate",
        "spec": (
            "Hard-coded policy checks on any proposed structure:\n"
            "- max breach %: cell must show breach ≤ ENGINE2_POLICY_"
            "MAX_BREACH_PCT.\n"
            "- max outside-wings %: cell's stop-out rate ≤ policy.\n"
            "- MAE95 × wing: 95th-percentile MAE can't exceed wing "
            "width multiplier.\n"
            "Any BLOCK means do not trade that cell. Warnings are "
            "coach-marks — you can proceed, but document the override."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "candidate_grid", "label": "Candidate Grid"},
            {"engine": "e2", "slug": "macro_proximity", "label": "Macro Proximity"},
        ],
    },

    "macro_proximity": {
        "title": "Macro Proximity",
        "spec": (
            "Multiplier (exp(-λ × days_to_event) weighted by event base) "
            "that inflates expected vol based on proximity to FOMC, "
            "CPI, NFP, OPEX, Treasury auctions, PCE, etc. Capped at "
            "ENGINE2_MACRO_MULTIPLIER_CAP.\n"
            "When the multiplier is > 2.0 the macro calendar is the "
            "dominant driver of the week's risk — IC positioning should "
            "respect the calendar, not just the regime score."
        ),
        "related_cards": [
            {"engine": "e2",           "slug": "regime_card", "label": "Regime"},
            {"engine": "e2",           "slug": "entry_state", "label": "Entry State"},
            {"engine": "e11",          "slug": "macro_calendar", "label": "Macro Calendar (E11)"},
        ],
    },

    "breach_table": {
        "title": "Breach Table (Recent Weeks)",
        "spec": (
            "Rolling table of the last N weekly IC cycles: entry date, "
            "proposed structure, actual realized move, whether a strike "
            "breached, realized P&L as % of credit. Calibration tool — "
            "if the table shows 40% realized breach rate over the last "
            "20 weeks but the current proposal says 10% expected, "
            "something is stale."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "candidate_grid", "label": "Candidate Grid"},
            {"engine": "e2", "slug": "entry_state", "label": "Entry State"},
        ],
    },

    "ai_advisor": {
        "title": "AI Trade Advisor",
        "spec": (
            "Narrative LLM advisor that reads the current grid + policy "
            "+ regime and proposes a shortlist of trade recommendations "
            "with GO / HOLD / PASS verdicts, confidence %, and key "
            "risks.\n"
            "Model is pinned via ENGINE2_ADVISOR_MODEL (default gpt-5.4). "
            "Advisor output is advisory — it never overrides the policy "
            "gate; a BLOCK-cell still blocks regardless of the advisor's "
            "enthusiasm."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "candidate_grid", "label": "Candidate Grid"},
            {"engine": "e2", "slug": "policy_gate", "label": "Policy Gate"},
            {"engine": "e2", "slug": "trade_log", "label": "Trade Log"},
        ],
    },

    "gamma_map": {
        "title": "Dealer Gamma Map (SPX)",
        "spec": (
            "SPX price chart with live level overlays showing put/call "
            "walls, open-interest clusters, and gamma concentration "
            "near spot. Pulled from ORATS live strike-gamma + OI; "
            "informational only — does not change backtest odds.\n"
            "- Put wall: heaviest single-strike put OI zone; usually a "
            "support magnet below spot.\n"
            "- Call wall: heaviest single-strike call OI zone; a "
            "ceiling magnet above spot.\n"
            "- Gamma peaks: strikes where dealer gamma is maximized, "
            "often pinning points into expiry.\n"
            "- Gamma flip: the spot level where dealer net gamma "
            "crosses zero — above = dampening regime, below = "
            "amplifying regime.\n"
            "Use these as an execution context overlay for intraday "
            "entries and exits, not as a standalone signal."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "gex_heatmap", "label": "Weekly Gamma Heat-Map"},
            {"engine": "e2", "slug": "regime_card", "label": "Regime"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Regime"},
        ],
    },

    "gex_heatmap": {
        "title": "Weekly Gamma Risk Heat-Map",
        "spec": (
            "Net dollar gamma (NET $GEX) by strike across upcoming "
            "SPX expiries, rendered as a color heat-map. Warm cells = "
            "positive net $GEX (dealer long gamma, dampening regime); "
            "cool cells = negative net $GEX (dealer short gamma, "
            "amplifying regime).\n"
            "- Distance to downside / upside gamma-flip: how far spot "
            "can move before the regime sign flips, expressed in "
            "points and EM-multiples.\n"
            "- Weekly stability chip: whether the overall GEX "
            "structure is stable, drifting, or dislocating.\n"
            "Treat pools as context for pinning vs acceleration zones, "
            "not as a hard entry trigger."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "gamma_map", "label": "Dealer Gamma Map"},
            {"engine": "e2", "slug": "regime_card", "label": "Regime"},
            {"engine": "e2", "slug": "entry_state", "label": "Entry State"},
        ],
    },

    "trade_log": {
        "title": "Trade Log",
        "spec": (
            "Journal of all SPX IC trades the desk has staged / entered "
            "through Engine 2, with entry context + follow-up outcome "
            "when closed. Used by the Post-Trade Review loop (shared "
            "with Engine 14) for calibration.\n"
            "- active trades: open positions with current MTM.\n"
            "- closed trades: historical outcomes with predicted vs "
            "realized P&L delta."
        ),
        "related_cards": [
            {"engine": "e2",  "slug": "ai_advisor", "label": "AI Advisor"},
            {"engine": "e14", "slug": "post_trade_review", "label": "Post-Trade Review (E14)"},
        ],
    },

}
