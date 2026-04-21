"""Desk Insight catalog — Engine 11 (News / Macro Risk).

Previously tooltip-less. Adds first-class desk-insight coverage for the
macro calendar and headline-risk surface.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "e11",
    "name":        "Engine 11 — Macro Events & Headline Risk",
    "description": (
        "Macro calendar + headline-risk dashboard. Aggregates upcoming "
        "high-impact events (FOMC, CPI, NFP, OPEX, earnings megacaps) "
        "with Benzinga headline feed and a density score that proxies "
        "'how noisy is this week'."
    ),
    "asset_class": "macro + cross-asset event flow",
}


CATALOG = {

    "macro_calendar": {
        "title": "Macro Calendar",
        "spec": (
            "Forward-looking calendar of high-impact macro and market "
            "events over the next 14 sessions.\n"
            "- Event type chips: FOMC / CPI / NFP / PPI / PCE / GDP / "
            "OPEX / Treasury auction / earnings megacap.\n"
            "- Importance tier: 1-5 (5 = FOMC decision-day, 4 = CPI "
            "print).\n"
            "- Embargoed time (if any).\n"
            "- Macro multiplier contribution to Engine 2's proximity "
            "model.\n"
            "Use this to plan structure durations — an IC whose hold "
            "window spans two tier-5 events is structurally "
            "disadvantaged relative to one that spans zero."
        ),
        "related_cards": [
            {"engine": "e11", "slug": "event_density", "label": "Event Density"},
            {"engine": "e11", "slug": "headline_feed", "label": "Headline Feed"},
            {"engine": "e2",  "slug": "macro_proximity", "label": "SPX Macro Proximity"},
        ],
    },

    "event_density": {
        "title": "Event Density",
        "spec": (
            "Rolling event-density score = weighted sum of upcoming "
            "events in the next 5 / 10 / 20 sessions. High density "
            "weeks mechanically expand realized vol.\n"
            "- green: < 2 effective events — quiet week, premium sale "
            "tailwind.\n"
            "- amber: 2-4 events — standard caution.\n"
            "- red: 4+ tier-4-or-higher events — dense week; "
            "premium-selling structures should tighten or skip."
        ),
        "related_cards": [
            {"engine": "e11", "slug": "macro_calendar", "label": "Macro Calendar"},
            {"engine": "e2",  "slug": "macro_proximity", "label": "SPX Macro Proximity"},
            {"engine": "e14", "slug": "modifiers", "label": "E14 Conditioning Modifiers"},
        ],
    },

    "headline_feed": {
        "title": "Headline Feed",
        "spec": (
            "Benzinga-sourced headline feed (when ENABLE_BENZINGA=1), "
            "filtered to names in the trade universe and tagged for "
            "severity.\n"
            "- M&A rumor: elevates event-risk on the named stock.\n"
            "- Regulatory action: FDA, SEC, DOJ — high-severity.\n"
            "- Guidance / analyst-day: medium.\n"
            "- Product / general PR: low.\n"
            "Headlines that match a ticker currently in the journal "
            "trigger a live watchlist badge."
        ),
        "related_cards": [
            {"engine": "e11", "slug": "macro_calendar", "label": "Macro Calendar"},
            {"engine": "e1",  "slug": "event_risk", "label": "Event Risk (E1)"},
        ],
    },

    "deny_allow_filters": {
        "title": "Deny / Allow Filters",
        "spec": (
            "Legal/reg governance: LEGAL_REG_TICKER_DENYLIST, "
            "ALLOWLIST, and KEYWORDS filters applied to the headline "
            "feed. Denylisted tickers are fully suppressed; keyword "
            "filters hide entire topic threads.\n"
            "Useful as the first line of compliance — keeps the desk's "
            "view clean of names you can't trade, and reduces the "
            "attack surface for LLM-sourced information leaks."
        ),
        "related_cards": [
            {"engine": "e11", "slug": "headline_feed", "label": "Headline Feed"},
        ],
    },

}
