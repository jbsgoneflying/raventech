"""Desk Insight catalog — Multi-Ticker Compare.

Previously tooltip-less. Adds coverage for the side-by-side breach /
tiering comparison surface.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "compare",
    "name":        "Multi-Ticker Compare",
    "description": (
        "Side-by-side comparison of breach statistics, VRP, width "
        "comparison, and desk consensus across up to N tickers. The "
        "fast triage tool for 'which of these reporters is the best "
        "premium-sale setup this week'."
    ),
    "asset_class": "single-name equities — comparative view",
}


CATALOG = {

    "compare_table": {
        "title": "Compare Table",
        "spec": (
            "Side-by-side matrix: one column per ticker, rows for each "
            "E1 metric (breachPct, VRP score, IV rank, EM %, desk "
            "consensus, event risk, next earnings date).\n"
            "Color coding: green cells = advantageous for premium sale, "
            "amber = neutral, red = disadvantageous. Lets the desk "
            "scan for the best name at a glance — sort by any column "
            "to re-rank."
        ),
        "related_cards": [
            {"engine": "compare", "slug": "tier_ranking", "label": "Tier Ranking"},
            {"engine": "compare", "slug": "portfolio_advisor", "label": "Portfolio Advisor"},
            {"engine": "e1",      "slug": "desk_consensus", "label": "E1 Desk Consensus"},
        ],
    },

    "tier_ranking": {
        "title": "Tier Ranking",
        "spec": (
            "Aggregates the per-column rankings into a single tier "
            "(S / A / B / C) per ticker based on a weighted score:\n"
            "- VRP weight: 0.30 — premium richness above all.\n"
            "- Breach weight: 0.25 — historical safety.\n"
            "- IV rank: 0.15 — vol regime fit.\n"
            "- Entry quality: 0.15 — composite gates.\n"
            "- Event risk: 0.15 — idiosyncratic headline weight.\n"
            "S = premium setup (top 10% of universe), A = solid, "
            "B = marginal (ok in light size), C = avoid. Sort the table "
            "by this column to surface the best names first; tie-breaks "
            "fall to higher VRP and then closer earnings date."
        ),
        "related_cards": [
            {"engine": "compare", "slug": "compare_table", "label": "Compare Table"},
            {"engine": "compare", "slug": "portfolio_advisor", "label": "Portfolio Advisor"},
        ],
    },

    "portfolio_advisor": {
        "title": "Portfolio Advisor",
        "spec": (
            "LLM portfolio-level advisor (E10_ADVISOR_MODEL, default "
            "gpt-5.5) that reads the compare table and proposes a "
            "diversified shortlist: 'of these 12 reporters, prefer "
            "these 4 — different sectors, staggered dates, "
            "non-correlated by 20d beta.'\n"
            "Capped at E10_ADVISOR_MAX_CALLS_PER_MINUTE rate limit. "
            "Advisory only — never overrides the individual desk "
            "consensus chips."
        ),
        "related_cards": [
            {"engine": "compare", "slug": "compare_table", "label": "Compare Table"},
            {"engine": "compare", "slug": "tier_ranking", "label": "Tier Ranking"},
        ],
    },

    "correlation_check": {
        "title": "Correlation Check",
        "spec": (
            "Rolling 20d return correlations among the compared "
            "tickers. Highlights pairs whose correlation exceeds "
            "GO_CORR20_HIGH (typically 0.70) — these aren't "
            "diversified from each other.\n"
            "When building a multi-name premium book, correlated names "
            "double-count your risk; use this check to prune before "
            "sizing. The heatmap also flags beta-to-SPX — if all your "
            "compared names carry 1.3+ beta, the book is effectively a "
            "levered short-vol bet on SPX, regardless of the story "
            "you're telling yourself about diversification."
        ),
        "related_cards": [
            {"engine": "compare", "slug": "portfolio_advisor", "label": "Portfolio Advisor"},
            {"engine": "compare", "slug": "tier_ranking", "label": "Tier Ranking"},
        ],
    },

}
