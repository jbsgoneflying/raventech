"""Engine 15 — per-card LLM tooltip explainer.

Lightweight wrapper around :mod:`backend.engine14.card_explain`'s LLM
plumbing, customized with an Engine-15-specific card catalog so the
narrative references earnings semantics (planned exit, VRP, anncTod)
rather than SPX-weekly ones.

The catalog keys match the UI-card identifiers on
``static/earnings-ic.html`` / ``earnings-ic.js``.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from backend.engine14 import card_explain as e14_card

LOG = logging.getLogger("engine15.card_explain")


__all__ = ["CARD_CATALOG", "supported_card_types", "generate_card_explanation"]


CARD_CATALOG: Dict[str, Dict[str, str]] = {
    "e1_summary_strip": {
        "title": "Engine 1 Summary",
        "spec": (
            "The Engine 1 summary strip rolls up the single-name earnings "
            "context for the ticker: current spot, implied move %, VRP score, "
            "desk consensus (go / no-go verdict), the next earnings date and "
            "its anncTod (BMO/AMC), plus a one-line IV-elevation stamp.\n"
            "- spot: current close used as the entry-anchor price.\n"
            "- 1σ EM%: ATM-straddle 1σ expected move to expiry.\n"
            "- VRP score (-100..+100): positive = IV pricier than realized "
            "(tailwind for premium sellers); negative = IV cheap vs realized "
            "(headwind).\n"
            "- anncTod: BMO means announcement before the Tuesday open (desk "
            "enters Monday, exits Tuesday AM); AMC means announcement after "
            "Monday close (desk enters Monday, exits Tuesday AM or PM).\n"
            "- Desk consensus: a qualitative verdict (e.g. 'Favorable', "
            "'Neutral', 'Fade') produced by Engine 1 from VRP + entry "
            "quality + regime + gap risk."
        ),
    },
    "event_analogue_row": {
        "title": "Historical Event Row",
        "spec": (
            "One row per prior earnings event used in the replay pool. "
            "Columns:\n"
            "- earnDate: the historical announcement date.\n"
            "- anncTod: BMO/AMC at the time of that historical event.\n"
            "- mapped strikes: the user's strikes translated into the "
            "analogue's strike space by preserving EM-distance.\n"
            "- outcome: earlyTarget / fullCollect / whiteKnuckle / stopOut / "
            "breach — see outcome bucket card.\n"
            "- pnlPct: P&L at the planned-exit boundary as a % of credit.\n"
            "- MAE: worst drawdown during the hold, % of credit.\n"
            "- realizedMovePct: how far the underlying moved between the "
            "pre-earnings close and the post-earnings session.\n"
            "- breached: short-strike taken out at planned exit."
        ),
    },
    "vrp_crush_verdict": {
        "title": "VRP / Vol Crush Verdict",
        "spec": (
            "Reads Engine 1's VRP analysis and combines it with the planned-"
            "exit fidelity note to tell the desk whether the IV crush from "
            "earnings is likely to materialize favorably during the hold.\n"
            "- tailwind: high positive VRP + confirmed anncTod → crush is "
            "likely meaningful; winnability is inflated in the adjusted "
            "distribution.\n"
            "- headwind: negative VRP (IV cheap vs realized) → crush may be "
            "shallow; the adjusted distribution's WR is lowered.\n"
            "- neutral: VRP within ±20pts; empirical distribution dominates."
        ),
    },
    "planned_exit_outcome": {
        "title": "Planned Exit Outcome",
        "spec": (
            "The core result block. Five buckets summing to 100%:\n"
            "- earlyTarget: PT hit before planned exit; P&L banked.\n"
            "- fullCollect: held to planned exit with positive P&L.\n"
            "- whiteKnuckle: eventually profitable but MAE reached stop "
            "territory during the hold.\n"
            "- stopOut: SL hit OR planned exit with negative P&L.\n"
            "- breach: short strike breached at exit with P&L ≤ -50%.\n"
            "Adjacent bucket shows the adjustedOutcomeDistribution — same "
            "buckets but reweighted by conditioning modifiers (VRP, "
            "anncConfidence, calendar, guidance risk). If the net "
            "conditioning effect is material, the UI highlights the "
            "adjusted bars; otherwise both views are near-identical."
        ),
    },
    "entry_state": {
        "title": "Entry State",
        "spec": (
            "The entry-state strip for Engine 15:\n"
            "- userSpot: close at or near request.entryDate, used to map "
            "strikes into analogue space.\n"
            "- 1σ EM%: market-implied 1σ expected move over (entry → "
            "expiry); sourced from a cached chain IV when available, else "
            "from E1 currentImpliedMovePct or a 30% IV fallback.\n"
            "- wingWidth: narrowest wing in points — used by sizing/risk.\n"
            "- eventsUsed / eventsConsidered: analogues that priced vs "
            "the admitted pool. A gap indicates cache thinness — run the "
            "backfill admin endpoint."
        ),
    },
    "planned_exit_timing": {
        "title": "Planned Exit Timing",
        "spec": (
            "Summarizes the hard time-stop the replay obeys:\n"
            "- plannedExitDate: calendar date the desk intends to flatten.\n"
            "- hours after open: 1-4 hours is typical for BMO vol crush.\n"
            "- holdBizDays: biz-day gap from entry to planned exit (≥0).\n"
            "- intradayCrushFactor: ORATS historical is EOD, so we "
            "approximate an AM exit by blending the close-to-close move "
            "by this factor toward the entry-day P&L. 0.80 means ~80% of "
            "the full day's crush has played out by morning.\n"
            "- fidelityCaveat: plain-English explanation of the "
            "approximation, shown in the UI as a chip."
        ),
    },
    "conditioning_summary": {
        "title": "Conditioning Summary",
        "spec": (
            "One-line verdict on whether the conditioning modifiers "
            "materially change the empirical distribution. Components:\n"
            "- vrpTilt: direction + size driven by E1 VRP score.\n"
            "- anncConfidence: 0 when timing confirmed; penalizes mixed "
            "pool for UNK or mismatched anncTod.\n"
            "- calendar: FOMC/CPI/macro proximity in [entry, plannedExit].\n"
            "- guidanceRisk: E1 eventRisk-score shim.\n"
            "netTailMultiplier and netWinRateShiftPct are the aggregate "
            "tail widening and WR shift applied to the adjusted view."
        ),
    },
    "exit_rules_card": {
        "title": "Exit Rules (Planned Hold)",
        "spec": (
            "Recommended PT/SL inside the planned hold window. Because "
            "the time stop is hard-capped at plannedExitDate, the grid "
            "only explores the profit-target and stop-loss axes (per-DTE "
            "targets and trailing stops are suppressed). deltaFromDefault "
            "shows the WR / avgPnl improvement vs the user's entered "
            "PT/SL at the time of the scan."
        ),
    },
    "outcome_distribution_empirical": {
        "title": "Outcome Distribution (Empirical)",
        "spec": (
            "The base outcome distribution from the historical replay "
            "pool BEFORE any earnings-conditioning modifiers are applied. "
            "Each analogue's planned-exit P&L is bucketed using the "
            "user's PT/SL and assigned to one of five mutually-exclusive "
            "outcomes that sum to 100%:\n"
            "- earlyTarget: profit target hit before planned exit; "
            "banked.\n"
            "- fullCollect: held to planned exit, finished positive.\n"
            "- whiteKnuckle: ended positive but max adverse excursion "
            "dipped into stop-loss territory during the hold (path-aware "
            "footgun — flags trades that worked but were uncomfortable).\n"
            "- stopOut: stop-loss tripped OR planned exit closed "
            "negative without breaching a wing.\n"
            "- breach: short strike actually breached at exit (P&L "
            "≤ -50%).\n"
            "Each bucket also reports n (event count) and avg P&L per "
            "outcome, so the desk can read expected payoff conditional "
            "on each scenario landing.\n"
            "fillModel determines whether replays use NBBO (conservative, "
            "default) or mid (legacy / aspirational); the bucket "
            "boundaries are the same either way, only the per-event "
            "P&L shifts."
        ),
    },
    "adjusted_distribution": {
        "title": "Adjusted Distribution (after conditioning)",
        "spec": (
            "Same five buckets as the empirical distribution, but "
            "REWEIGHTED by Engine 15's conditioning modifiers — VRP / "
            "vol-crush score, anncTod (BMO/AMC) confidence, calendar "
            "proximity (FOMC / CPI / macro inside the hold window), and "
            "the guidance-risk shim from Engine 1.\n"
            "- netTailMultiplier > 1: tails widened (more breach + "
            "stopOut).\n"
            "- netWinRateShiftPct > 0: WR pulled UP (more fullCollect / "
            "earlyTarget, fewer stopOuts).\n"
            "If the empirical and adjusted views are within ~3pp of each "
            "other on every bucket, conditioning is materially neutral "
            "and the desk should treat the empirical view as "
            "authoritative. When they diverge, lean on the adjusted view "
            "because it incorporates today's IV regime, not just the "
            "historical mix."
        ),
    },
    "conditioning_modifiers": {
        "title": "Conditioning Modifiers",
        "spec": (
            "Per-driver breakdown of HOW Engine 15 tilted the empirical "
            "distribution into the adjusted distribution. Each card "
            "shows the modifier name, the tail-widening multiplier "
            "(×N tails), the WR shift in pp, and a human-readable "
            "reason.\n"
            "- VRP score (E1): positive = IV pricier than realized; "
            "tail narrows and WR ticks up.\n"
            "- anncConfidence: 0 when announcement timing (BMO/AMC) is "
            "confirmed; positive penalty when the analogue pool mixes "
            "UNK or mismatched anncTod events.\n"
            "- calendar: macro events (FOMC / CPI) inside [entry, "
            "plannedExit] widen tails.\n"
            "- guidanceRisk: E1 eventRisk score shim — high = company "
            "guidance volatility risk.\n"
            "These multipliers compose multiplicatively into the "
            "netTail and netWR adjustments displayed on the Adjusted "
            "Distribution card. Modifiers with magnitude under ±2pp WR "
            "are dropped from this list — the absence of a card means "
            "that driver was negligible today."
        ),
    },
    "mtm_timeline": {
        "title": "MTM Timeline (P10 / P50 / P90)",
        "spec": (
            "Cross-sectional distribution of mark-to-market P&L (as a % "
            "of credit) across all replayed analogue paths, plotted at "
            "each business day from entry (D0) to planned exit. At each "
            "day we take all paths still alive, sort their MTM P&L, and "
            "plot three percentiles:\n"
            "- P50 (blue, median): the typical analogue outcome path; "
            "half of analogues did better, half worse at this point.\n"
            "- P10 (red, lower decile): only 10% of analogues fared "
            "worse; the 'if it goes wrong' envelope.\n"
            "- P90 (green, upper decile): only 10% fared better; the "
            "'if it works beautifully' envelope.\n"
            "The vertical gap between P90 and P10 is the dispersion at "
            "that day — it should fan out monotonically through the "
            "hold. If the gap is unusually wide on D0 the analogue pool "
            "is heterogeneous (tighten the matcher); a flat fan means "
            "your replays are tightly clustered.\n"
            "A P50 that stays positive throughout is the bullish read; "
            "a P10 that dives below -100% means the worst decile of "
            "analogues is essentially a full loss — line that up "
            "against stopOut + breach % from the Outcome Distribution."
        ),
    },
    "expected_value": {
        "title": "Expected Value",
        "spec": (
            "Summary statistics across every replayed path's planned-"
            "exit P&L (% of credit):\n"
            "- Mean P&L: arithmetic average across analogues.\n"
            "- Median P&L: 50th-percentile path; less skewed by tails.\n"
            "- Sharpe-proxy: mean / stdev across analogues — a crude "
            "structure-quality score (>0.5 = strong, 0.0–0.3 = noisy, "
            "<0 = expected loser).\n"
            "- FullCollect 90% CI: bootstrap confidence interval on "
            "the FullCollect bucket %. Tight CI = many analogues + "
            "consistent outcomes; wide CI = the headline % is fragile.\n"
            "Compare Mean vs Median: if Mean << Median, a few large "
            "losers are dragging the average — sizing should respect "
            "that left skew, not the median's optimism. If Mean > "
            "Median, a few outsized winners are flattering the average."
        ),
    },
    "matched_events": {
        "title": "Matched Events (Replay Pool)",
        "spec": (
            "One row per historical earnings event used in the replay "
            "pool. This table is the AUDIT TRAIL behind every percentage "
            "on this page — each row's outcome, exitDay, P&L, MAE, "
            "breached flag, and implied-vs-realized move feed directly "
            "into the buckets above.\n"
            "Columns:\n"
            "- Earn Date / Timing: historical event date + BMO/AMC.\n"
            "- Entry / Exit / Expiry: the dates the analogue replay "
            "used for this event.\n"
            "- Outcome: which bucket this row landed in.\n"
            "- Exit Day: business days from entry to actual exit.\n"
            "- P&L: % of credit at the planned-exit boundary "
            "(green = win, red = loss).\n"
            "- MAE: max adverse excursion during the hold (worst "
            "intraday drawdown the desk would have seen).\n"
            "- Breached: short strike taken out at exit.\n"
            "- EM% / Realized%: implied move at the analogue's entry "
            "vs the move that actually printed; EM > Realized → vol-"
            "crush tailwind played out.\n"
            "- Analogue Credit: the analogue's mapped entry credit; "
            "compare against your credit on the Credit Richness chip."
        ),
    },
    "dropped_events": {
        "title": "Dropped Events",
        "spec": (
            "Earnings events the matcher CONSIDERED but excluded from "
            "the replay pool, with the drop reason. Common reasons: "
            "chain-cache miss for the relevant strikes/expiries, season "
            "filter (when 'Same quarter only' is on), bad-print "
            "outliers, anncTod mismatch, or no usable underlying bar at "
            "the analogue's entry/exit dates.\n"
            "When this list is long relative to Matched Events the "
            "pool is thin — consider running the chain backfill (admin "
            "button) or loosening the season filter before leaning on "
            "the result. A dropped count > matched count is a yellow "
            "flag; > 2× matched is a red flag."
        ),
    },
    "notes_caveats": {
        "title": "Notes & Caveats",
        "spec": (
            "Free-form annotations the simulator emits when a replay "
            "had non-trivial assumptions or fallbacks: chain-cache "
            "thinness, intraday-crush approximations, missing strikes "
            "mapped to nearest, credit-richness verdict, anncTod "
            "imputed, EM source = fallback, etc.\n"
            "Treat anything in this list as a footnote on confidence — "
            "the result is still usable but the desk should weight it "
            "down. If more than two notes fire, flag the trade as "
            "advisory only and verify the live fill manually before "
            "committing capital."
        ),
    },
    "actions_panel": {
        "title": "Actions",
        "spec": (
            "Three desk actions on the result panel:\n"
            "- Log to Journal: persists this scenario into the shared "
            "E1/E15 trade journal so the /review endpoint can compare "
            "predicted vs realized P&L after the trade closes.\n"
            "- Run LLM Advisor: invokes the Engine 15 narrative LLM "
            "(/api/earnings-ic/advisor) which produces a verdict (GO / "
            "HOLD / PASS), confidence %, key points, risks, suggested "
            "adjustments, and a planned-exit note grounded in the "
            "replay payload + Engine 1 context.\n"
            "- Reconcile vs Live: cross-checks the user's credit "
            "against the live NBBO mid/bid/ask for the entered strikes "
            "and expiry; surfaces a credit chip (match / drift / "
            "mismatch) so the desk can sanity-check the entered "
            "premium against the current market before firing."
        ),
    },
    "credit_richness": {
        "title": "Credit Richness",
        "spec": (
            "Compares the user's entered credit to the historical mean "
            "and median entry credit of the matched analogue pool.\n"
            "- user_rich (Δ ≥ +15%): you're getting paid richly vs "
            "history; verify the fill is realistic before celebrating "
            "(pre-market NBBO can mislead).\n"
            "- user_cheap (Δ ≤ -15%): you're selling cheap; wait for "
            "the open to tighten the NBBO or move strikes closer to "
            "raise premium.\n"
            "- user_fair (within ±15%): in-line with typical analogue "
            "placement; the entry premium is not a differentiating "
            "factor for this trade."
        ),
    },
}


def supported_card_types() -> List[str]:
    return sorted(CARD_CATALOG.keys())


def generate_card_explanation(
    *,
    card_type: str,
    card_data: Any,
    scenario_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Produce an LLM-backed tooltip for an Engine 15 card.

    We compose the Engine 14 plumbing with our local CARD_CATALOG by
    temporarily monkey-patching the module-level catalog reference. The
    E14 helper caches on (card_type, card_data, context) so repeated
    opens of the same card are free.
    """
    original = getattr(e14_card, "CARD_CATALOG", {})
    merged = {**original, **CARD_CATALOG}
    try:
        e14_card.CARD_CATALOG = merged  # type: ignore[attr-defined]
        result = e14_card.generate_card_explanation(
            card_type=card_type,
            card_data=card_data,
            scenario_context=scenario_context or {},
        )
        if isinstance(result, dict):
            result["_engine"] = 15
        return result
    finally:
        e14_card.CARD_CATALOG = original  # type: ignore[attr-defined]
