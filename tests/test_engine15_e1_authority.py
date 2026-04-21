"""Engine 15 — E1 authority guardrails.

Regression tests for the "desk has committed" contract:

  1. ``engine15.simulator._summarize_engine1`` must NOT re-surface E1's
     ``deskConsensus`` verdict (GO / LEAN_PASS / PASS / etc.) nor E1's
     ``nextEvent.earnDate`` / ``anncTod``. The authoritative earnings date
     and AMC/BMO timing live in ``EarningsIcRequest`` on the scenario
     payload — those two leaks would re-introduce either a GO/PASS vote
     or a competing date and confuse the advisor.

  2. ``e15_earnings_scenario_advisor._compact_engine1`` must NOT serialize
     those same fields into the LLM context. Numeric drivers (vrpScore,
     ivElevation, emPct, emBreachPct, entryQuality*, regime, historyN)
     must still be present so the advisor has full analytical substrate.

These tests are offline / pure-python — no ORATS, no LLM.
"""
from __future__ import annotations

from typing import Any, Dict

from backend.e15_earnings_scenario_advisor import _compact_engine1
from backend.engine15.simulator import _summarize_engine1


def _lean_pass_e1_fixture() -> Dict[str, Any]:
    """Synthetic E1 payload shaped like a real compute_breach_stats output
    where the deterministic consensus came back as LEAN_PASS."""
    return {
        "ticker": "TSLA",
        "current": {
            "stockPrice": 250.0,
            "asOfDate": "2026-04-17",
            "impliedMovePct": 7.5,
            "delayedImpliedMovePct": 7.3,
        },
        "vrpAnalysis": {
            "vrpScore": 38.0,
            "meanRatio": 0.92,
            "stdRatio": 0.15,
            "ivElevation": 1.05,
            "sampleSize": 12,
            "confidence": 0.6,
        },
        "deskConsensus": {
            "verdict": "LEAN_PASS",
            "consensus": "LEAN_PASS",
            "score": 0.35,
            "reasons": ["Soft VRP", "IV elev thin", "breach elevated"],
        },
        "emBreachSummary": {
            "breachRatePct": 42.0,
            "breachPct": 42.0,
            "n": 12,
            "1.0": 42.0,
            "1.5": 18.0,
            "2.0": 4.0,
        },
        "nextEvent": {
            # Deliberately divergent from what the desk would enter — this
            # is exactly the "stale E1 calendar" shape we want suppressed.
            "earnDate": "2026-04-22",
            "anncTod": "BMO",
            "timing": "BMO",
            "source": "orats_snapshot",
            "pricingExpiry": "2026-04-24",
            "confidence": "medium",
        },
        "entryQuality": {
            "entryQuality": 42.0,
            "flags": ["vrpSoft"],
        },
        "regime": {
            "regime": "choppy",
            "bucket": "choppy",
            "label": "CHOPPY",
            "tailMultiplier": 1.15,
        },
        "expectedMove": {
            "expectedMovePct": 7.8,
            "expectedMoveDollars": 19.5,
            "expiry": "2026-04-24",
            "source": "orats",
            "dte": 5,
            "spotPrice": 250.0,
        },
        "strikeTargets": {
            "whitePct": 7.5, "bluePct": 11.25, "redPct": 15.0,
            "whitePts": 18.75, "bluePts": 28.1, "redPts": 37.5,
            "emSource": "orats",
            "basedOnEmPct": 7.5, "basedOnSpot": 250.0,
        },
        "eventRisk": {"label": "MEDIUM"},
        "summary": {"events_used": 12, "events_found": 14},
        "events": [{"earnDate": f"2025-0{i}-15"} for i in range(1, 5)],
    }


# ---------------------------------------------------------------------------
# _summarize_engine1
# ---------------------------------------------------------------------------

def test_summarize_engine1_omits_desk_consensus_even_on_lean_pass():
    """E1 returned LEAN_PASS — engine1Summary must not re-surface it."""
    out = _summarize_engine1(_lean_pass_e1_fixture())
    assert "deskConsensus" not in out, (
        f"deskConsensus leaked into engine1Summary with value "
        f"{out.get('deskConsensus')!r} — this would pull the E15 LLM back "
        f"into a GO/PASS vote it is no longer asked to cast."
    )
    assert "deskConsensusScore" not in out
    # The raw numerics that drove the verdict must remain — those are the
    # inputs the advisor reasons over.
    assert out["vrpScore"] == 38.0
    assert out["ivElevation"] == 1.05
    assert out["emBreachRate1xPct"] == 42.0
    assert out["emBreachRate15xPct"] == 18.0
    assert out["emBreachRate2xPct"] == 4.0


def test_summarize_engine1_omits_next_event_date_and_timing():
    """E1's nextEvent date / AMC-BMO must not propagate — the desk's
    EarningsIcRequest is the single source of truth for those."""
    out = _summarize_engine1(_lean_pass_e1_fixture())
    for key in (
        "nextEventDate",
        "anncTod",
        "nextEventPricingExpiry",
        "nextEventSource",
        "nextEventConfidence",
    ):
        assert key not in out, (
            f"{key!r} leaked into engine1Summary — this would compete with "
            f"scenario.request.earnings_date / earnings_timing and confuse "
            f"the LLM about which date is authoritative."
        )


def test_summarize_engine1_preserves_analytical_substrate():
    """Confirm the non-verdict numeric anchors survive the purge."""
    out = _summarize_engine1(_lean_pass_e1_fixture())
    # Core anchors
    assert out["ticker"] == "TSLA"
    assert out["stockPrice"] == 250.0
    assert out["historyN"] == 4
    # EM fields
    assert out["oratsEmPct"] == 7.5
    assert out["delayedEmPct"] == 7.3
    assert out["emPct"] == 7.5
    # Straddle EM block
    assert out["straddleEmPct"] == 7.8
    assert out["straddleExpiry"] == "2026-04-24"
    # Strike targets
    assert out["strikeTargets"]["whitePct"] == 7.5
    # Regime chip
    assert out["regimeLabel"] == "CHOPPY"
    assert out["eventRiskLabel"] == "MEDIUM"


def test_summarize_engine1_empty_input():
    assert _summarize_engine1(None) == {}
    assert _summarize_engine1({}) == {}


# ---------------------------------------------------------------------------
# _compact_engine1  (fallback advisor context path)
# ---------------------------------------------------------------------------

def test_compact_engine1_strips_desk_consensus_and_next_event_fields():
    out = _compact_engine1(_lean_pass_e1_fixture())
    for key in (
        "deskConsensus",
        "deskConsensusScore",
        "deskConsensusReasons",
        "anncTod",
        "nextEventDate",
    ):
        assert key not in out, (
            f"{key!r} leaked into the advisor's compact E1 context — the "
            f"LLM would see it and either re-litigate the entry decision "
            f"(for deskConsensus*) or treat E1's date as competing with "
            f"the desk's (for anncTod / nextEventDate)."
        )


def test_compact_engine1_preserves_numeric_substrate():
    out = _compact_engine1(_lean_pass_e1_fixture())
    assert out["vrpScore"] == 38.0
    assert out["ivElevation"] == 1.05
    assert out["emPct"] == 7.5
    assert out["stockPrice"] == 250.0
    assert out["emBreachPct"] == 42.0
    assert out["emBreachN"] == 12
    assert out["entryQualityScore"] == 42.0
    assert out["entryQualityFlags"] == ["vrpSoft"]
    assert out["regimeBucket"] == "choppy"
    assert out["historyN"] == 4


def test_compact_engine1_empty_input():
    assert _compact_engine1(None) == {}
    assert _compact_engine1({}) == {}
