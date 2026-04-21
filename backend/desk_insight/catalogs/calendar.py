"""Desk Insight catalog — Earnings Calendar.

Previously tooltip-less. Adds first-class coverage for the weekly
earnings calendar, transcripts, and condor-rank surfaces.
"""
from __future__ import annotations

ENGINE_META = {
    "id":          "calendar",
    "name":        "Earnings Calendar",
    "description": (
        "Weekly earnings calendar with volatility context: which names "
        "report, when (BMO/AMC), implied moves, historical breach "
        "tendency, optional transcript/condor-rank drill-downs for "
        "post-event research."
    ),
    "asset_class": "single-name equities — earnings cycle",
}


CATALOG = {

    "calendar_grid": {
        "title": "Calendar Grid",
        "spec": (
            "Week view of earnings announcements. Each row: ticker, "
            "date, timing (BMO/AMC), 1σ EM %, prior 4-quarter breach "
            "count, E1 Desk Consensus chip (Favorable/Neutral/Fade).\n"
            "Used as triage — scan for the handful of names where E1 is "
            "Favorable and EM is rich, then jump into those individually "
            "via the breach page."
        ),
        "related_cards": [
            {"engine": "calendar", "slug": "date_detail", "label": "Date Detail"},
            {"engine": "calendar", "slug": "condor_rank", "label": "Condor Rank"},
            {"engine": "e1",       "slug": "desk_consensus", "label": "E1 Desk Consensus"},
        ],
    },

    "date_detail": {
        "title": "Date Detail",
        "spec": (
            "Drill-down for a specific earnings date: all reporters, "
            "timing split (BMO cluster vs AMC cluster), aggregate EM, "
            "any macro events colliding with the session.\n"
            "Clusters matter — when 8 megacaps report AMC on the same "
            "day, the following morning's entire tape is hostage to "
            "their collective reaction. This card also surfaces the "
            "aggregate expected-move across the day's reporters, the "
            "heaviest-weighted name, and any macro release (FOMC, CPI) "
            "that shares the same 24-hour window — the 'double-catalyst' "
            "situations that mechanically widen tails for every IC "
            "position held overnight."
        ),
        "related_cards": [
            {"engine": "calendar", "slug": "calendar_grid", "label": "Calendar Grid"},
            {"engine": "e11",      "slug": "macro_calendar", "label": "Macro Calendar"},
        ],
    },

    "condor_rank": {
        "title": "Condor Rank",
        "spec": (
            "Ranks each earnings reporter by IC-friendliness this "
            "cycle: VRP score + breach history + option liquidity in "
            "the short-strike delta band. Higher rank = better "
            "premium-sale candidate.\n"
            "The top 10 of the week are the desk's typical universe — "
            "they get the full Engine 1 + Engine 15 workup before "
            "trade construction."
        ),
        "related_cards": [
            {"engine": "calendar", "slug": "calendar_grid", "label": "Calendar Grid"},
            {"engine": "e1",       "slug": "breach_stats", "label": "Breach Stats"},
            {"engine": "e1",       "slug": "vrp_analysis", "label": "VRP Analysis"},
        ],
    },

    "transcripts": {
        "title": "Transcripts",
        "spec": (
            "When available, links to earnings-call transcripts and "
            "summary highlights. Used for post-event research — if an "
            "E8 fade setup fires on a reporter, desk often reads the "
            "transcript to weigh management credibility before pulling "
            "the trigger. The transcript preview surfaces guidance "
            "direction (raised / maintained / lowered), analyst-tone "
            "snippets, and any forward-looking binary event (product "
            "launch, FDA timeline, litigation) that will drive vol in "
            "the weeks between this quarter's report and the next."
        ),
        "related_cards": [
            {"engine": "calendar", "slug": "date_detail", "label": "Date Detail"},
            {"engine": "e8",       "slug": "desk_notes", "label": "Post-Event Desk Notes"},
        ],
    },

    "macro_event_overlay": {
        "title": "Macro Event Overlay",
        "spec": (
            "Shows macro events (FOMC, CPI, NFP, OPEX, Treasury "
            "auctions, PCE, jobless claims) overlaid on the earnings "
            "calendar so the desk can spot double-risk sessions — e.g. "
            "a CPI print on the same morning as 5 megacaps reporting. "
            "Those days are structurally disadvantaged for premium-sale "
            "structures because both idiosyncratic (earnings gap) and "
            "systematic (macro jump) risk compound in the same "
            "intraday window.\n"
            "Highlighted clusters warn that selling vol into the "
            "composite event is mechanically overpaying theta for risk "
            "you can't isolate."
        ),
        "related_cards": [
            {"engine": "calendar", "slug": "calendar_grid", "label": "Calendar Grid"},
            {"engine": "e11",      "slug": "macro_calendar", "label": "Macro Calendar (E11)"},
            {"engine": "e11",      "slug": "event_density", "label": "Event Density"},
        ],
    },

}
