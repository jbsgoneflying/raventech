"""Desk Insight catalog — Engine 2 (SPX Iron Condor Scanner)."""
from __future__ import annotations

ENGINE_META = {
    "id":          "e2",
    "name":        "Engine 2 v2 — SPX IC Command Deck",
    "description": (
        "SPX / SPY / QQQ weekly iron condor Command Deck. Ranks "
        "(EM-multiple × wing-width) placements by a deterministic "
        "composite score combining historical breach stats, Monte Carlo "
        "forward simulation conditioned on MI v2 regime + macro "
        "proximity, empirical intraweek MAE, theta capture, and credit "
        "richness. MI v2 regime is the single source of truth across "
        "the scan and the open-trade tracker (no more E5-snapshot "
        "drift). Advisor is on-demand, not gated on multi-wing."
    ),
    "asset_class": "SPX/SPY/QQQ weekly iron condors",
}


CATALOG = {

    "wing_console": {
        "title": "Wing Decision Console",
        "spec": (
            "Primary card on the /spx page. Scores the full "
            "(EM-mult × wing-width) grid and ranks placements by a "
            "deterministic composite score (0-100). Five inputs:\n"
            "- breach_close_prob: MC P(close at expiry outside shorts).\n"
            "- touch_intraweek_prob: MC P(spot touched short strike "
            "midweek). A weekly IC's real failure mode.\n"
            "- mae_p95 vs wing: empirical 95th-pct intraweek max "
            "adverse excursion expressed as a fraction of wing width.\n"
            "- theta_capture: expected fraction of entry credit "
            "retained by the planned exit (Black-Scholes approximation).\n"
            "- roc_est: credit / max-loss estimate on the structure.\n"
            "Weights default to close 25% / touch 20% / mae 25% / theta "
            "15% / credit 15% and are desk-tunable via "
            "E2_WING_SCORE_WEIGHT_* env knobs. Replaces the legacy "
            "widthComparison + deskConsensus surfaces: the desk sees "
            "one ranked table rather than a verdict string."
        ),
        "related_cards": [
            {"engine": "e2",           "slug": "placement_score", "label": "Placement Scorecard"},
            {"engine": "e2",           "slug": "mc_reading", "label": "MC Reading"},
            {"engine": "e2",           "slug": "mae_distribution", "label": "MAE Pool"},
            {"engine": "e2",           "slug": "regime_mi_v2", "label": "MI v2 Regime"},
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Intel Regime"},
        ],
    },

    "placement_score": {
        "title": "Placement Scorecard",
        "spec": (
            "Row-level drill-down of one candidate placement. Each "
            "row shows em_mult × wing_pts, absolute short + long "
            "strikes derived from today's spot + 1σ EM, breach_close_prob "
            "+ touch_intraweek_prob + mae_p95_vs_wing as the three risk "
            "terms, theta_capture_pct + credit_dollars + roc_est as the "
            "three reward terms, a confidence chip (high when MC pool "
            "is deep + conditioned; low when bootstrap fell back to "
            "unconditioned or historical-only), and the composite "
            "breakdown so the desk can see which term drove the score."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "wing_console", "label": "Wing Decision Console"},
            {"engine": "e2", "slug": "mc_reading", "label": "MC Reading"},
            {"engine": "e2", "slug": "mae_distribution", "label": "MAE Pool"},
        ],
    },

    "mc_reading": {
        "title": "Monte Carlo Reading",
        "spec": (
            "Bootstrap-based forward simulation of the weekly path. For "
            "each (em_mult, wing_pts) the scorer runs N=5000 sims "
            "(E2_MC_N_SIMS) drawn from the historical weekly pool, "
            "optionally conditioned on today's (regime_bucket, "
            "macro_proximity_bucket). Falls back to a GBM daily-step "
            "walk when the conditioning pool is thin (< E2_MC_MIN_POOL).\n"
            "- mode: 'bootstrap' | 'gbm' | 'unavailable'.\n"
            "- conditioning_used: 'regime+macro' | 'regime' | "
            "'unconditioned' (degraded).\n"
            "- pool_size_used / pool_size_total: how much of the pool "
            "survived the conditioning tier.\n"
            "- seed: deterministic from (ticker, as_of_date, n_sims, "
            "flags, conditioning_key), so cache hits are reproducible.\n"
            "The three scan-level probabilities the Command Deck shows "
            "(breach_close, touch_intraweek, outside_wings) come from "
            "this module; the historical expiry-close breach table "
            "stays around as a sanity cross-check."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "wing_console", "label": "Wing Decision Console"},
            {"engine": "e2", "slug": "placement_score", "label": "Placement Scorecard"},
            {"engine": "e2", "slug": "breach_table", "label": "Breach Table (historical)"},
        ],
    },

    "mae_distribution": {
        "title": "MAE Pool (intraweek)",
        "spec": (
            "Historical distribution of intraweek max adverse excursion "
            "(worst |spot - entry_close| across the hold window) across "
            "the weekly pool. For each past week we compute "
            "``max(|high - entry|, |entry - low|) / entry * 100`` and "
            "aggregate to p50/p75/p90/p95.\n"
            "The Command Deck uses p95 in the composite penalty term: "
            "if historically a chosen placement's p95 MAE exceeds the "
            "wing width, the breach_penalty saturates. Source chips:\n"
            "- 'daily_ohlc' (best): every hold day had high + low from "
            "EODHD / ORATS.\n"
            "- 'open_close_fallback': at least half the weeks lacked "
            "intraday extremes; p95 under-estimates.\n"
            "- 'mixed': partial coverage."
        ),
        "related_cards": [
            {"engine": "e2", "slug": "wing_console", "label": "Wing Decision Console"},
            {"engine": "e2", "slug": "mc_reading", "label": "MC Reading"},
        ],
    },

    "regime_mi_v2": {
        "title": "MI v2 Regime (HMM)",
        "spec": (
            "Market Intelligence v2 regime snapshot — the single "
            "regime source the scan + the open-trade tracker both "
            "consume in E2 v2. Replaces the legacy E5 snapshot that "
            "drove the tracker.\n"
            "- probabilities: 3-state HMM (Risk-On / Transitional / "
            "Stressed) with posterior probabilities summing to 1.\n"
            "- label: most-likely state.\n"
            "- vol_state: aligned to the HMM state for tracker parity.\n"
            "- source: 'v2_hmm' when the model is calibrated; "
            "'default_model' when MI v2 hasn't been fit yet.\n"
            "When MI v2 is disabled the tracker falls back to the "
            "Engine 5 snapshot, but the Command Deck always reports "
            "MI v2 first so scan + tracker stay synchronised."
        ),
        "related_cards": [
            {"engine": "market-intel", "slug": "regime_card", "label": "Market Intel Regime"},
            {"engine": "e2",           "slug": "regime_card", "label": "Engine 2 Regime Score"},
            {"engine": "e2",           "slug": "wing_console", "label": "Wing Decision Console"},
        ],
    },

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
        "title": "Regime (Engine 2 score)",
        "spec": (
            "Engine 2's internal 0-100 regime score, composed from:\n"
            "- Trend (SPX 20-day regression slope).\n"
            "- Vol: RV20, IV7/IV30 term slope, vol-of-vol.\n"
            "- Stress: range vs EM, gap-vs-EM, dispersion across "
            "sector ETFs (XLK/XLF/XLE/XLV/XLU/XLY).\n"
            "- Macro multiplier: Benzinga economics proximity decay "
            "weighted by event type (CPI / FOMC / NFP / OPEX etc).\n"
            "Labels: Low / Moderate / Elevated / High via "
            "ENGINE2_REGIME_LOW_MAX / MODERATE_MAX / ELEVATED_MAX.\n"
            "In v2 this score sits alongside the MI v2 HMM "
            "(regimeMiV2) rather than replacing it. The HMM gives "
            "the desk a market-wide probability distribution; the "
            "E2 score stays as the short-term SPX-tuned reading that "
            "drives the candidate grid's weight mixture."
        ),
        "related_cards": [
            {"engine": "e2",           "slug": "regime_mi_v2", "label": "MI v2 Regime (HMM)"},
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
