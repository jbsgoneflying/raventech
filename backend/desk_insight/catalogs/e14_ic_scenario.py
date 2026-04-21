"""Desk Insight catalog — Engine 14 (IC Scenario Simulator).

Migrated from the legacy ``backend/engine14/card_explain.py`` with added
``quant_mechanics`` and ``related_cards`` cross-links.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "e14",
    "name":        "Engine 14 — IC Scenario Simulator",
    "description": (
        "SPX iron-condor scenario replay. Matches the user's proposed "
        "structure against historical SPX weekly analogues via KNN regime "
        "match, then evaluates outcome distribution, MTM path, exit "
        "optimization, greeks attribution, sizing, and conditioning "
        "adjustments."
    ),
    "asset_class": "SPX weekly iron condors",
}


CATALOG = {

    "entry_state": {
        "title": "Entry State",
        "spec": (
            "The Entry State strip summarizes the replay context at the "
            "trade's entry moment. Fields:\n"
            "- Analogues Used / Considered: how many historical IC replays "
            "survived the matcher filter. More = tighter estimate.\n"
            "- Regime Bucket: a label (low/mid/high RV20 percentile) "
            "classifying today's realized-vol regime; analogues are drawn "
            "from the same bucket.\n"
            "- Spot (Entry): SPX cash price used as the reference price. "
            "If the requested entry date has no printed bar, the card shows "
            "the most recent close with an amber 'market closed' stamp.\n"
            "- 1σ EM %: market-implied 1-standard-deviation expected move "
            "to expiry (from the ATM straddle).\n"
            "- Short PUT/CALL Dist: distance from spot to each short strike, "
            "in % and in EM multiples. Rule of thumb: <1.00× EM = inside "
            "the cone (red/amber); ≥1.00× = outside (blue/green).\n"
            "- Wing Width: smaller of put-wing or call-wing in points — "
            "the max-loss geometry.\n"
            "- Mean / Median P&L + Sharpe-proxy: structural quality across "
            "the analogue pool under the active exit rules."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "regime_match", "label": "Regime Match Quality"},
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
            {"engine": "e14", "slug": "position_sizing", "label": "Position Sizing"},
        ],
    },

    "regime_match": {
        "title": "Regime Match Quality",
        "spec": (
            "How the analogue pool was selected.\n"
            "- Match Source = KNN: multi-factor nearest-neighbor match "
            "over a feature store (RV20, term structure, skew, dealer "
            "gamma, etc.) weighted by covariance. Distances are "
            "weighted-L2; lower = closer.\n"
            "- Match Source = RV20 bucket: legacy fallback — match on "
            "the realized-vol percentile bucket only because the feature "
            "store was unavailable.\n"
            "- Distance (min / mean / max): spread of neighbor distances. "
            "Wide spread means the analogue pool isn't cohesive.\n"
            "- Feature Imputation: share of feature cells median-filled. "
            "High imputation = brittle match.\n"
            "- Admitted: how many analogues came from KNN vs legacy bucket "
            "fallback — fallback rows are lower-confidence."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "entry_state", "label": "Entry State"},
            {"engine": "e14", "slug": "matched_analogues", "label": "Matched Analogues"},
            {"engine": "e14", "slug": "conditioning_notes", "label": "Conditioning Notes"},
        ],
    },

    "outcome_distribution": {
        "title": "Outcome Distribution (NBBO)",
        "spec": (
            "Primary empirical outcome mix across all matched analogue "
            "replays under the active fill model. Five mutually-exclusive "
            "buckets summing to 100%:\n"
            "- Early Target: hit profit target (typically 50% of credit) "
            "early and closed for a clean win; MAE never approached stop.\n"
            "- Full Collect: rolled to expiry ended positive without "
            "hitting stop — a calm win.\n"
            "- White Knuckle: ended positive BUT intraday/EOD MAE reached "
            "stop territory during the hold. Functionally a win, stressful.\n"
            "- Stop Out: triggered loss stop OR rolled to expiry finished "
            "below zero without hitting stop rule — both are realized losses.\n"
            "- Breach: underlying closed beyond a short strike at expiry → "
            "assignment / max-loss if held.\n"
            "Per-bucket: pct, n, avg P&L, avg days, and (when shown) a 90% "
            "bootstrap CI — wider bands = thinner sample."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_adjusted", "label": "Adjusted Distribution"},
            {"engine": "e14", "slug": "outcome_mid", "label": "Legacy Mid-Fill"},
            {"engine": "e14", "slug": "matched_analogues", "label": "Matched Analogues"},
            {"engine": "e14", "slug": "mtm_timeline", "label": "MTM Timeline"},
        ],
    },

    "outcome_mid": {
        "title": "Legacy Mid-Fill Distribution",
        "spec": (
            "Same five-outcome mix as the primary distribution but computed "
            "under a pure mid-price fill model (no NBBO, no slippage). "
            "Shown only as a calibration reference — expect mid-only to "
            "overstate win rate vs NBBO because it doesn't pay the bid/ask "
            "spread to exit. Use the delta between mid and NBBO to see how "
            "much of the edge is spread-sensitive."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution (NBBO)"},
            {"engine": "e14", "slug": "conditioning_notes", "label": "Conditioning Notes"},
        ],
    },

    "outcome_adjusted": {
        "title": "Adjusted Distribution (Phase 2 conditioning)",
        "spec": (
            "Outcome distribution after applying the Conditioning Modifiers "
            "(macro calendar density, dealer-gamma regime, cross-asset "
            "stress, gap regime from Engine 13). Tail probabilities are "
            "multiplied by the net tail-multiplier and win-rate is shifted "
            "by the net win-rate shift. This is the distribution to trust "
            "when today's regime diverges from the raw analogue pool's "
            "regime."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_distribution", "label": "Empirical Distribution"},
            {"engine": "e14", "slug": "modifiers", "label": "Conditioning Modifiers"},
            {"engine": "e13", "slug": "fragility_score", "label": "Gap Fragility"},
        ],
    },

    "modifiers": {
        "title": "Conditioning Modifiers",
        "spec": (
            "Per-factor adjustments applied to the raw empirical "
            "distribution to get the Adjusted Distribution. Each card "
            "shows a severity label (none / low / moderate / elevated / "
            "extreme), a tail multiplier (scales breach+stop tails), a "
            "WR shift (percentage-point add-on to full-collect + "
            "early-target), and a reason.\n"
            "- Macro Calendar: high-impact events in the holding window "
            "(FOMC, CPI, NFP) — denser calendars fatten tails.\n"
            "- Dealer Gamma: SPX dealer net gamma. Positive = dealers damp "
            "moves (IC friendly). Negative = amplifies (IC hostile).\n"
            "- Cross-Asset Stress: HYG/LQD spreads, DXY, crude, gold, BTC "
            "composite — elevated stress raises breach tails.\n"
            "- Gap Regime (E13): current overnight-gap environment.\n"
            "- Net Adjustment: composite tail-mult × WR shift applied."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_adjusted", "label": "Adjusted Distribution"},
            {"engine": "e13", "slug": "gap_regime_card", "label": "Gap Regime"},
            {"engine": "e9",  "slug": "credit_stress_score", "label": "Credit Stress Drift"},
        ],
    },

    "mtm_timeline": {
        "title": "MTM Timeline (P10 / P50 / P90)",
        "spec": (
            "Mark-to-market P&L path through the life of the trade, as a "
            "% of credit received, at each day-to-expiry step.\n"
            "- P50 (median): the typical path — what you'd MTM on a normal "
            "analogue.\n"
            "- P10 / P90: the 10th and 90th percentile paths — the bad-tail "
            "and good-tail envelopes.\n"
            "A steep P10 dip early = analogues commonly got punched before "
            "recovering (path risk even if outcome was positive). A flat "
            "P50 that drifts up is the classic theta-decay glide. Wide "
            "P10-P90 fan means high path uncertainty; narrow fan = tight "
            "analogue cluster."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
            {"engine": "e14", "slug": "greeks_attribution", "label": "Greeks Attribution"},
            {"engine": "e14", "slug": "position_sizing", "label": "Position Sizing"},
        ],
    },

    "position_sizing": {
        "title": "Position Sizing",
        "spec": (
            "Four sizing recommendations as a fraction of equity to risk:\n"
            "- Consensus (min of three): the floor — the most conservative "
            "of the three methods. Defer to this unless you have a reason.\n"
            "- Kelly (½-Kelly): half-Kelly using empirical win probability "
            "and payoff ratio from the replay. Clamped to guard outliers.\n"
            "- Fixed-Fractional: standard risk-per-trade against the "
            "worst-case loss seen in the analogue pool.\n"
            "- Empirical Max-DD: sizing that would have capped historical "
            "drawdown to the target percentage given this structure's "
            "observed drawdown path."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
            {"engine": "e14", "slug": "mtm_timeline", "label": "MTM Timeline"},
            {"engine": "e14", "slug": "exit_optimization", "label": "Exit Rules"},
        ],
    },

    "greeks_attribution": {
        "title": "P&L Attribution (Greeks)",
        "spec": (
            "Average decomposition of per-analogue P&L across delta, "
            "gamma, theta, vega, and residual, using an entry-Taylor "
            "approximation (greeks × realized factor moves). Two numbers "
            "per greek:\n"
            "- Pct value: contribution to P&L in % of credit (signed).\n"
            "- Share of |P&L|: the greek's share of the total absolute-value "
            "bar.\n"
            "Residual absorbs unmodeled IV-path, second-order cross greeks, "
            "and fill slippage — a large residual is itself a signal that "
            "the Taylor proxy is missing something."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "mtm_timeline", "label": "MTM Timeline"},
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
        ],
    },

    "exit_optimization": {
        "title": "Exit-Rule Optimization",
        "spec": (
            "A grid search over profit-target and stop-loss levels across "
            "matched analogues, picking the PT/SL pair that maximizes "
            "average P&L subject to a minimum win-rate floor.\n"
            "- Recommended PT / SL: the best grid cell.\n"
            "- Δ Win Rate / Δ Avg P&L: change vs the defaults you "
            "submitted (green = improvement).\n"
            "If the recommendation matches your defaults, your rules are "
            "already near-optimal — don't chase small edges."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "exit_sensitivity", "label": "Exit-Rule Sensitivity"},
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
        ],
    },

    "exit_sensitivity": {
        "title": "Exit-Rule Sensitivity",
        "spec": (
            "Interactive sliders that scrub across the exit-rule grid to "
            "see win-rate + avg-P&L for any PT/SL combo without re-running "
            "the replay. Flat metrics across a wide region = sturdy rule; "
            "cliff = fragile. If only a narrow PT/SL band wins, the edge "
            "depends on the stop being exactly right and won't generalize."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "exit_optimization", "label": "Exit Optimization"},
        ],
    },

    "conditioning_notes": {
        "title": "Conditioning Notes",
        "spec": (
            "Plain-English bullets emitted when unusual conditions were "
            "detected during the replay: thin sample, feature-store outage, "
            "unusual calendar density, sparse chain cache, analogue-pool "
            "skew, etc. Treat these as sanity checks before leaning on the "
            "distribution — when two or more notes fire, down-weight the "
            "signal and verify manually."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "regime_match", "label": "Regime Match Quality"},
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
        ],
    },

    "matched_analogues": {
        "title": "Matched Analogues",
        "spec": (
            "Row-by-row view of the individual historical IC replays that "
            "informed the distribution. Each row shows the historical "
            "entry and expiry dates, the outcome bucket, the day the "
            "replay exited, realized P&L (% of credit), max adverse "
            "excursion (% of credit), the mapped strikes, and whether a "
            "short strike was breached at expiry. Use this to sanity-check "
            "the distribution against specific dates and to spot unusual "
            "rows that might deserve exclusion."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
            {"engine": "e14", "slug": "regime_match", "label": "Regime Match Quality"},
        ],
    },

    "post_trade_review": {
        "title": "Post-Trade Review",
        "spec": (
            "After a live trade is journaled and later closed, this panel "
            "compares the actual realized P&L and outcome vs the predicted "
            "mean / median / outcome-probability from the simulation at "
            "entry. The verdict banner summarizes whether the sim was "
            "within ±15pp of reality, and in which direction divergence "
            "went — a fast feedback loop for model calibration."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "outcome_distribution", "label": "Outcome Distribution"},
            {"engine": "e14", "slug": "actions", "label": "Actions"},
        ],
    },

    "actions": {
        "title": "Actions",
        "spec": (
            "Operational hand-offs after a run:\n"
            "- Save to Trade Log: persists scenario + entry context to "
            "the shared journal so Post-Trade Review can score it "
            "later. Includes the reconcile snapshot, regime tag, and "
            "modifier state at entry so the post-trade loop is "
            "apples-to-apples against the actual close.\n"
            "- Copy Chat Summary: builds a text summary and copies it "
            "to the clipboard so you can paste into Raven Chat for a "
            "human-in-the-loop discussion with the senior-quant advisor."
        ),
        "related_cards": [
            {"engine": "e14", "slug": "post_trade_review", "label": "Post-Trade Review"},
        ],
    },

}
