"""Engine 2 / SPX live-review v2 — evidence + recommendation shape tests.

The legacy renderer surfaced just three percentile tiles. The new
panel is decision-grade: phase chip, verdict + status, evidence tiles
(spot/IV/regime/history-breaker/time-decay), replay projection grid,
key points / risks / desk note, and a probability-banded action
ladder. These tests pin the payload shape the frontend depends on.
"""
from __future__ import annotations

import datetime as dt

import pytest


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

def _trade(*, mode: str = "live", entry_overrides=None, ctx_overrides=None):
    today = dt.date.today()
    entry = {
        "underlying": "SPX",
        "entryDate": today.isoformat(),
        "expiryDate": (today + dt.timedelta(days=2)).isoformat(),
        "shortPutStrike": 7250.0,
        "longPutStrike":  7225.0,
        "shortCallStrike": 7600.0,
        "longCallStrike": 7625.0,
        "entryCredit": 1.30,
        "wingWidth": 25.0,
        "spotAtEntry": 7444.50,
    }
    if entry_overrides:
        entry.update(entry_overrides)
    ctx = {
        "regimeBucket": "MODERATE",
        "regimeScore": 55.0,
        "volPressureState": "neutral",
        "historyBreakerRisk": {
            "score": 18.0,
            "level": "low",
            "gate": "OK",
            "drivers": ["Conditioned breach risk remains above comfort range."],
            "signals": {},
            "policy": "warn_only",
        },
    }
    if ctx_overrides:
        ctx.update(ctx_overrides)
    return {
        "tradeId": "t-test",
        "mode": mode,
        "entry": entry,
        "entryContext": ctx,
        "loggedAt": today.isoformat() + "T13:00:00Z",
        "checkIns": [],
    }


def _fake_scenario():
    """Replay payload that exercises both the legacy and E1-shape keys."""
    timeline = []
    for dte in (2, 1, 0):
        timeline.append({
            "dte": dte,
            "p10": 20.0 + dte * 5,
            "p50": 60.0 + dte * 5,
            "p90": 92.0 + dte * 2,
            "n": 41,
            "pBreach": 0.02 if dte > 0 else 0.0,
            "pStopHit": 0.0,
        })
    return {
        "engine": 14,
        "version": "2.0.0",
        "analoguesUsed": 41,
        "mtmTimeline": timeline,
        "outcomeDistribution": {
            "earlyTarget":  {"pct": 18.0, "avgDays": 1.4},
            "fullCollect":  {"pct": 80.0},
            "whiteKnuckle": {"pct": 12.0, "maxAdverseExcursionPct": 35.0},
            "stopOut":      {"pct": 0.0},
            "breach":       {"pct": 2.0},
        },
        "expectedValue": {
            "meanPnlPct": 62.5,
            "medianPnlPct": 68.0,
            "sharpeProxy": 1.4,
        },
        "exitRulesOptimization": {
            "recommendedProfitTarget": 50.0,
            "recommendedStopLoss": 275.0,
            "recommendedTimeStopDays": 1,
        },
        "conditioningSummary": "Path-conditioned dispersion matches the empirical replay window.",
    }


@pytest.fixture
def patched_review(monkeypatch):
    """Patch the heavy run_scenario + LLM call so tests are deterministic."""
    monkeypatch.setattr(
        "backend.e2_live_review.run_scenario",
        lambda req, **kw: _fake_scenario(),
    )
    monkeypatch.setattr(
        "backend.e2_live_review.generate_checkin_analysis",
        lambda **kw: {
            "_source": "stub",
            "status": "on_track",
            "headline": "SPX centered within 7250/7600 with low breach proximity.",
            "spotAnalysis": "Spot drifted -0.15% since entry. Distance is symmetric.",
            "regimeDrift": "Regime stable in MODERATE bucket.",
            "riskUpdate": "Tail risk minimal with 2% breach in replay.",
            "recommendation": "Hold the iron condor through expiry; replay favors patience.",
            "adjustmentIfNeeded": {"action": None, "detail": None},
            "deskNote": "Stay disciplined; let theta finish the job.",
        },
    )
    monkeypatch.setattr("backend.e2_live_review.get_client", lambda: None)
    monkeypatch.setattr("backend.e2_live_review.get_benzinga_client_optional", lambda: None)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_phase_auto_detects_via_dte():
    from backend.e2_live_review import _auto_phase
    today = dt.date.today()

    def t(days):
        return {"entry": {"expiryDate": (today + dt.timedelta(days=days)).isoformat()}}

    assert _auto_phase(t(5)) == "pre_event"
    assert _auto_phase(t(1)) == "pre_open"
    assert _auto_phase(t(0)) == "post_open"
    assert _auto_phase(t(-1)) == "post_open"


def test_summarize_replay_emits_legacy_and_e1_keys():
    from backend.e2_live_review import _summarize_replay
    summary = _summarize_replay(_fake_scenario())

    # Legacy keys still present for any older mounted clients. The
    # summary reports the END row of mtmTimeline (DTE=0 in the fixture),
    # so the p10 there is the lowest in the curve.
    assert summary["analoguesUsed"] == 41
    assert summary["p10"] == 20.0
    assert summary["fullCollectRate"] == 80.0

    # E1-shape additions for the new renderer.
    assert summary["available"] is True
    assert summary["pathsCount"] == 41
    assert summary["p10PnlPct"] == 20.0
    assert summary["p50PnlPct"] == 60.0
    assert summary["p90PnlPct"] == 92.0
    assert summary["fullCollectRateFrac"] == pytest.approx(0.80, abs=1e-3)
    assert summary["breachRateFrac"] == pytest.approx(0.02, abs=1e-3)
    assert summary["meanPnlPct"] == 62.5
    assert summary["medianPnlPct"] == 68.0
    assert summary["exitRulesRec"]["profitTarget"] == 50.0
    assert summary["exitRulesRec"]["stopLoss"] == 275.0
    assert summary["medianMaePct"] == 35.0
    assert summary["daysToEarlyExit"] == 1.4
    assert summary["conditioningSummary"].startswith("Path-conditioned")


def test_run_review_returns_e1_shape_evidence_and_recommendation(patched_review):
    from backend.e2_live_review import run_e2_live_review

    trade = _trade()
    review = run_e2_live_review(
        trade=trade,
        current_spot=7432.97,
        current_regime={"bucket": "MODERATE", "score": 54.0},
        current_vol="neutral",
        phase="pre_open",
    )

    # Phase + back-compat fields.
    assert review["phase"] == "pre_open"
    assert review["mode"] == "live"
    assert "tracking" in review
    assert "projection" in review
    assert "historyBreaker" in review
    assert "actionLadder" in review

    # Status chip mapping.
    assert review["statusChip"] == "on_track"

    # Evidence assembly.
    ev = review["evidence"]
    spot = ev["spot"]
    assert spot["now"] == 7432.97
    assert spot["atEntry"] == 7444.50
    assert spot["putDistPct"] is not None
    assert spot["callDistPct"] is not None
    assert spot["nearestShortPct"] == min(spot["putDistPct"], spot["callDistPct"])
    assert spot["moveSinceEntryPct"] is not None
    assert ev["regime"]["available"] is True
    assert ev["regime"]["now"] == "MODERATE"
    assert ev["regime"]["atEntry"] == "MODERATE"
    assert ev["timeDecay"]["dte"] is not None
    assert ev["historyBreaker"]["score"] == 18.0

    # Replay block.
    rp = ev["replay"]
    assert rp["available"] is True
    assert rp["fullCollectRateFrac"] == pytest.approx(0.80, abs=1e-3)
    assert rp["pathsCount"] == 41
    assert isinstance(rp["mtmCurve"], list) and len(rp["mtmCurve"]) >= 2

    # Recommendation block.
    rec = review["recommendation"]
    assert rec["verdict"] in {"HOLD", "ADJUST", "CUT"}
    assert isinstance(rec["confidence"], float) and 0.5 < rec["confidence"] <= 1.0
    assert rec["narrative"]
    assert isinstance(rec["keyPoints"], list)
    assert isinstance(rec["riskFactors"], list)
    assert rec["deskNote"]

    # Action ladder shape — must carry probWin + expectedPnl/p10/p90 for HOLD
    # so the prob-bar + range render light up.
    ladder = rec["actionLadder"]
    assert isinstance(ladder, list) and len(ladder) >= 2
    hold = next(row for row in ladder if row["action"] == "HOLD")
    assert hold["probWin"] is not None
    # The HOLD probWin must be 1 − breach − stopOut, NOT just the strict
    # fullCollect bucket; otherwise a clearly winning trade can show 0%.
    breach_frac = ev["replay"]["breachRateFrac"] or 0.0
    stop_frac = ev["replay"].get("stopOutRateFrac") or 0.0
    expected_prob = 1.0 - breach_frac - stop_frac
    assert abs(hold["probWin"] - expected_prob) < 1e-3
    assert hold["expectedPnlPct"] is not None
    assert hold["p10PnlPct"] is not None
    assert hold["p90PnlPct"] is not None
    actions = {row["action"] for row in ladder}
    assert "HOLD" in actions
    # Either CUT_NOW or ADJUST must be present as the defensive row.
    assert ({"CUT_NOW", "ADJUST"} & actions)


def test_review_verdict_lifts_to_adjust_on_breach_status(patched_review, monkeypatch):
    """When the deterministic tracker says 'adjust', the ladder + verdict
    must escalate so the desk doesn't see a stale HOLD on a tested trade."""
    from backend.e2_live_review import run_e2_live_review

    # Force tracking into "adjust" by making put-side breach proximity high.
    monkeypatch.setattr(
        "backend.e2_live_review.compute_trade_tracking",
        lambda **kw: {
            "currentSpot": 7260.0,
            "distPutPts": 10.0,
            "distCallPts": 340.0,
            "distPutPct": 0.14,
            "distCallPct": 4.68,
            "breachProxPut": 95.0,
            "breachProxCall": 5.0,
            "regimeDriftScore": -2.0,
            "regimeDriftBucket": "ELEVATED",
            "volShift": "neutral -> elevated",
            "dte": 1,
            "timeDecayProgress": 0.5,
            "deterministicStatus": "exit",
        },
    )

    review = run_e2_live_review(
        trade=_trade(),
        current_spot=7260.0,
        current_regime={"bucket": "ELEVATED", "score": 35.0},
        current_vol="elevated",
        phase="pre_open",
    )
    assert review["statusChip"] == "breached"
    assert review["recommendation"]["verdict"] in {"CUT", "ADJUST"}
    # Risks must surface the put-side proximity signal.
    risks_text = " ".join(review["recommendation"]["riskFactors"]).lower()
    assert "put" in risks_text or "breach" in risks_text


def test_iv_evidence_handles_dict_vol_state(patched_review):
    """MI v2's regime_snapshot returns vol_state as a structured dict —
    the live-review payload must never spill that dict into the UI."""
    from backend.e2_live_review import run_e2_live_review

    review = run_e2_live_review(
        trade=_trade(),
        current_spot=7432.97,
        current_regime={"bucket": "MODERATE", "score": 54.0},
        current_vol={
            "level": 10.44,
            "term_structure": "flat",
            "skew": "neutral",
            "source": "market_intel.canonical_vol_state",
        },
        phase="pre_open",
    )
    iv = review["evidence"]["iv"]
    # The structured fields must be plain primitives, never a dict.
    assert iv["volPressureNow"] == "flat" or iv["volPressureNow"] == "neutral" or isinstance(iv["volPressureNow"], str)
    assert iv["volStructure"] == "flat"
    assert iv["volSkew"] == "neutral"
    # The vol_state level becomes the synthesised IV reading when no
    # explicit ivNow was logged at entry.
    assert iv["now"] == 10.44
    # The shift field must NEVER stringify a dict (would produce a
    # Python dict repr in the UI).
    if iv.get("shift") is not None:
        assert "{" not in iv["shift"]
        assert "}" not in iv["shift"]


def test_conditioning_summary_extracts_human_string():
    from backend.e2_live_review import _summarize_replay

    scenario = _fake_scenario()
    scenario["conditioningSummary"] = {
        "material": True,
        "direction": "tailwind",
        "humanSummary": "Modifiers cancel out; replay reads as the empirical analogue window.",
    }
    summary = _summarize_replay(scenario)
    assert isinstance(summary["conditioningSummary"], str)
    assert "Modifiers cancel out" in summary["conditioningSummary"]


def test_conditioning_summary_passthrough_string_and_none():
    from backend.e2_live_review import _summarize_replay
    scenario = _fake_scenario()
    scenario["conditioningSummary"] = "Plain-string summary."
    assert _summarize_replay(scenario)["conditioningSummary"] == "Plain-string summary."
    scenario["conditioningSummary"] = None
    assert _summarize_replay(scenario)["conditioningSummary"] is None
    scenario["conditioningSummary"] = {"material": False}  # no string fields
    assert _summarize_replay(scenario)["conditioningSummary"] is None


def test_sentence_splitter_preserves_decimals():
    """The key-points bulletizer used to cut '7432.97' into two sentences."""
    from backend.e2_live_review import _split_sentences
    out = _split_sentences("Current spot is 7432.97, between the 7250 short put and 7600 short call.", limit=2)
    assert len(out) == 1
    assert "7432.97" in out[0]
    assert "7250" in out[0]


def test_review_legacy_actionladder_rows_are_chartable(patched_review):
    from backend.e2_live_review import run_e2_live_review
    review = run_e2_live_review(
        trade=_trade(),
        current_spot=7432.97,
        current_regime={"bucket": "MODERATE", "score": 54.0},
        current_vol="neutral",
        phase="pre_open",
    )
    legacy = review["actionLadder"]
    assert legacy["preVerdict"] in {"HOLD", "ADJUST", "CUT"}
    assert isinstance(legacy["rows"], list) and len(legacy["rows"]) >= 2
    for row in legacy["rows"]:
        assert "action" in row
        assert "probability" in row
        assert 0 <= int(row["probability"]) <= 100
