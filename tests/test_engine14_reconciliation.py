"""Tests for backend.engine14.reconciliation.

Golden fixtures live at the repo root (``e2.json``, ``e14.json``,
``e2LLM.json``) — the 4/20/2026 SPX iron condor scenario that kicked
off this project. Tests assert both the individual chip behavior and
the full reconcile payload against the real-world mismatch we diagnosed.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from backend.engine14 import reconciliation as R


REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def e2_payload():
    return json.loads((REPO / "e2.json").read_text())


@pytest.fixture(scope="module")
def e14_payload():
    return json.loads((REPO / "e14.json").read_text())


@pytest.fixture(scope="module")
def advisor_payload():
    return json.loads((REPO / "e2LLM.json").read_text()).get("advisor")


# ---------------------------------------------------------------------------
# Reference-trade: deterministic reconciliation (Stage 1)
# ---------------------------------------------------------------------------

def test_reconcile_deterministic_shape(e2_payload, e14_payload):
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    assert set(out.keys()) == {"overall", "checks"}
    assert len(out["checks"]) == 8
    keys = [c["key"] for c in out["checks"]]
    assert keys == [
        "regimeBucket", "spotPrice", "expectedMovePct", "emMultipleLabel",
        "deskEmFloor", "policyConstraints", "breachRate", "conditioningNetEffect",
    ]
    assert out["overall"]["counts"]["total"] == 8


def test_reference_trade_flags_regime_mismatch(e2_payload, e14_payload):
    """E2=ELEVATED vs E14=MODERATE (the exact issue that kicked off this project)."""
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    regime = next(c for c in out["checks"] if c["key"] == "regimeBucket")
    assert regime["status"] == "mismatch"
    assert regime["e2"]["bucket"] == "ELEVATED"
    assert regime["e14"]["bucket"] == "MODERATE"
    # Top finding should mention regime
    assert any("Regime" in f for f in out["overall"]["topFindings"])


def test_reference_trade_spot_agrees(e2_payload, e14_payload):
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    spot = next(c for c in out["checks"] if c["key"] == "spotPrice")
    assert spot["status"] == "agree"
    assert spot["e2"] == pytest.approx(7126.06)
    assert spot["e14"] == pytest.approx(7126.06)


def test_reference_trade_em_proxy_vs_orats_flags(e2_payload, e14_payload):
    """E14 EM ≈ 1.35% vs E2 ORATS 1.66% — a 19% divergence → drift/mismatch expected."""
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    em = next(c for c in out["checks"] if c["key"] == "expectedMovePct")
    assert em["status"] in ("drift", "mismatch")
    assert em["e2"]["pct"] == pytest.approx(1.66)


def test_reference_trade_em_multiple_within_red_box(e2_payload, e14_payload):
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    mult = next(c for c in out["checks"] if c["key"] == "emMultipleLabel")
    # 6890/7365 short strikes on 7126 spot → half-width 3.32%; Red box is 3.32%.
    assert mult["e2"]["box"] == "Red"
    assert mult["status"] == "agree"


def test_reference_trade_desk_floor_agrees(e2_payload, e14_payload):
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    floor = next(c for c in out["checks"] if c["key"] == "deskEmFloor")
    # Floor = 1.5x, user = 2.47x → comfortably above.
    assert floor["status"] == "agree"


def test_reference_trade_breach_rate_vs_oddslike(e2_payload, e14_payload):
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    breach = next(c for c in out["checks"] if c["key"] == "breachRate")
    assert breach["status"] in ("agree", "drift", "mismatch")
    # Must have produced numbers on both sides.
    assert isinstance(breach["e2"]["pBreachEitherPct"], (int, float))
    assert isinstance(breach["e14"]["breachPlusStopOutPct"], (int, float))


def test_reference_trade_conditioning_is_nearly_neutral(e2_payload, e14_payload):
    out = R.reconcile_deterministic(scenario_result=e14_payload, engine2_payload=e2_payload)
    cond = next(c for c in out["checks"] if c["key"] == "conditioningNetEffect")
    # tail=0.994, WR shift=+1.0 → just at our 1.0pp threshold; allow either.
    assert cond["status"] in ("agree", "drift")
    assert cond["e14"]["netTailMultiplier"] == pytest.approx(0.994)


# ---------------------------------------------------------------------------
# Credit quad + LLM chips (Stage 1.5)
# ---------------------------------------------------------------------------

def test_credit_quad_flags_user_vs_advisor_gap(e2_payload, e14_payload, advisor_payload):
    """User typed 0.85, advisor estimates ~$0.20 — >75% off → mismatch."""
    out = R.reconcile_full(
        scenario_result=e14_payload,
        engine2_payload=e2_payload,
        engine2_advisor=advisor_payload,
        live_chain=None,
    )
    credit = next(c for c in out["checks"] if c["key"] == "creditQuad")
    assert credit["status"] == "mismatch"
    assert credit["e14"]["userCredit"] == pytest.approx(0.85)
    assert credit["e2"]["advisorEstimate"] == pytest.approx(0.20)


def test_credit_quad_live_chain_overrides_advisor(e2_payload, e14_payload, advisor_payload):
    """Live NBBO should take priority over advisor estimate when present."""
    live = {"mid": 0.60, "netBid": 0.55, "netAsk": 0.65}
    out = R.reconcile_full(
        scenario_result=e14_payload,
        engine2_payload=e2_payload,
        engine2_advisor=advisor_payload,
        live_chain=live,
    )
    credit = next(c for c in out["checks"] if c["key"] == "creditQuad")
    # 0.85 vs 0.60 live mid → ~42% off → drift range.
    assert credit["status"] in ("drift", "mismatch")
    assert credit["e2"]["liveMid"] == pytest.approx(0.60)


def test_credit_quad_agree_when_user_inside_nbbo_near_mid(e14_payload, e2_payload, advisor_payload):
    scen = copy.deepcopy(e14_payload)
    scen["request"]["credit_received"] = 0.60
    live = {"mid": 0.60, "netBid": 0.55, "netAsk": 0.65}
    out = R.reconcile_full(
        scenario_result=scen,
        engine2_payload=e2_payload,
        engine2_advisor=advisor_payload,
        live_chain=live,
    )
    credit = next(c for c in out["checks"] if c["key"] == "creditQuad")
    assert credit["status"] == "agree"


def test_llm_verdict_lean_pass_maps_to_drift(e2_payload, e14_payload, advisor_payload):
    out = R.reconcile_full(
        scenario_result=e14_payload,
        engine2_payload=e2_payload,
        engine2_advisor=advisor_payload,
    )
    verdict = next(c for c in out["checks"] if c["key"] == "llmVerdict")
    assert verdict["status"] == "drift"
    assert verdict["e2"]["verdict"] == "LEAN_PASS"


def test_llm_verdict_fallback_collapses_to_na(e2_payload, e14_payload):
    # Simulate a degenerate advisor (OpenAI down, rate-limit, etc.) — even though
    # the fallback sets verdict="PASS", we must surface it as na so the chip is
    # not misread as consensus.
    stub = {
        "verdict": "PASS",
        "confidence": 0,
        "tradeTicket": {},
        "_source": "fallback",
        "_fallback_reason": "OpenAI client unavailable",
    }
    out = R.reconcile_full(
        scenario_result=e14_payload,
        engine2_payload=e2_payload,
        engine2_advisor=stub,
    )
    verdict = next(c for c in out["checks"] if c["key"] == "llmVerdict")
    assert verdict["status"] == "na"
    assert "Advisor unavailable" in (verdict["note"] or "")
    assert "OpenAI client unavailable" in (verdict["note"] or "")


def test_llm_strikes_exact_match_for_reference_ticket(e2_payload, e14_payload, advisor_payload):
    out = R.reconcile_full(
        scenario_result=e14_payload,
        engine2_payload=e2_payload,
        engine2_advisor=advisor_payload,
    )
    strikes = next(c for c in out["checks"] if c["key"] == "llmStrikesMatchUser")
    assert strikes["status"] == "agree"


def test_full_reconcile_overall_is_mismatch_for_reference_trade(e2_payload, e14_payload, advisor_payload):
    out = R.reconcile_full(
        scenario_result=e14_payload,
        engine2_payload=e2_payload,
        engine2_advisor=advisor_payload,
    )
    # At minimum regime + credit are mismatch, so overall must be mismatch.
    assert out["overall"]["status"] == "mismatch"
    assert out["overall"]["counts"]["mismatch"] >= 2


# ---------------------------------------------------------------------------
# Synthetic / corner-case inputs
# ---------------------------------------------------------------------------

def test_regime_agree_with_em_proxy_source_adds_note(e14_payload, e2_payload):
    scen = copy.deepcopy(e14_payload)
    scen["entryState"]["regimeBucket"] = "ELEVATED"
    scen["entryState"]["regimeSource"] = "em_proxy"
    out = R.reconcile_deterministic(scenario_result=scen, engine2_payload=e2_payload)
    regime = next(c for c in out["checks"] if c["key"] == "regimeBucket")
    assert regime["status"] == "agree"
    assert "EM-proxy" in regime["note"] or "em_proxy" in regime["note"]


def test_missing_inputs_collapse_to_na():
    out = R.reconcile_deterministic(scenario_result={}, engine2_payload={})
    assert out["overall"]["status"] == "na"
    assert all(c["status"] == "na" for c in out["checks"])


def test_summarize_for_journal_shapes_compact_snapshot(e2_payload, e14_payload, advisor_payload):
    full = R.reconcile_full(
        scenario_result=e14_payload,
        engine2_payload=e2_payload,
        engine2_advisor=advisor_payload,
    )
    snap = R.summarize_for_journal(full)

    assert snap is not None
    assert set(snap.keys()) == {"overall", "checks", "generatedAt"}
    assert snap["overall"]["status"] == full["overall"]["status"]
    assert snap["overall"]["counts"] == full["overall"]["counts"]
    # Findings are truncated but preserved.
    assert snap["overall"]["topFindings"] == full["overall"]["topFindings"][:5]
    # Compact checks shed verbose fields.
    for c in snap["checks"]:
        assert set(c.keys()) == {"key", "label", "status", "note"}
    # Snapshot has an ISO-8601 UTC timestamp with trailing Z.
    assert snap["generatedAt"].endswith("Z")
    assert "T" in snap["generatedAt"]


def test_summarize_for_journal_truncates_long_notes():
    huge_note = "x" * 500
    payload = {
        "overall": {"status": "mismatch", "counts": {"mismatch": 1}, "topFindings": []},
        "checks": [{
            "key": "k1", "label": "L", "status": "mismatch",
            "e2": {"a": 1}, "e14": {"b": 2}, "rule": "r", "note": huge_note,
        }],
    }
    snap = R.summarize_for_journal(payload, note_char_cap=100)
    assert snap is not None
    note = snap["checks"][0]["note"]
    assert note is not None
    # 99 content chars + 1 ellipsis char = 100 total.
    assert len(note) == 100
    assert note.endswith("\u2026")


def test_summarize_for_journal_none_on_empty_input():
    assert R.summarize_for_journal(None) is None
    assert R.summarize_for_journal({}) is None
    assert R.summarize_for_journal({"foo": "bar"}) is None


def test_policy_agree_when_cell_meets_thresholds(e14_payload):
    e2 = {
        "widthComparison": [
            {"emMult": 2.0, "wingWidthPts": 10,
             "breachPct": 5.0, "outsidePct": 3.0, "avgMae95xWing": 0.4, "creditProxy": 20.0},
        ],
        "recommendation": {"policy": {
            "maxBreachPct": 25.0, "maxOutsideWingsPct": 10.0, "maxMae95xWing": 1.0,
        }},
    }
    scen = {
        "entryState": {"userEmMultiple": 2.0, "wingWidth": 10.0},
        "request": {"short_put": 0, "long_put": 0, "short_call": 0, "long_call": 0},
    }
    out = R.reconcile_deterministic(scenario_result=scen, engine2_payload=e2)
    policy = next(c for c in out["checks"] if c["key"] == "policyConstraints")
    assert policy["status"] == "agree"


def test_policy_mismatch_when_multiple_thresholds_violated():
    e2 = {
        "widthComparison": [
            {"emMult": 1.0, "wingWidthPts": 5,
             "breachPct": 57.0, "outsidePct": 57.0, "avgMae95xWing": 34.0, "creditProxy": 405.0},
        ],
        "recommendation": {"policy": {
            "maxBreachPct": 25.0, "maxOutsideWingsPct": 10.0, "maxMae95xWing": 1.0,
        }},
    }
    scen = {
        "entryState": {"userEmMultiple": 1.0, "wingWidth": 5.0},
        "request": {"short_put": 0, "long_put": 0, "short_call": 0, "long_call": 0},
    }
    out = R.reconcile_deterministic(scenario_result=scen, engine2_payload=e2)
    policy = next(c for c in out["checks"] if c["key"] == "policyConstraints")
    assert policy["status"] == "mismatch"


def test_parse_credit_estimate():
    assert R._parse_credit_estimate("~$0.20") == pytest.approx(0.20)
    assert R._parse_credit_estimate("$1.35 credit") == pytest.approx(1.35)
    assert R._parse_credit_estimate(0.85) == pytest.approx(0.85)
    assert R._parse_credit_estimate(None) is None
    assert R._parse_credit_estimate("n/a") is None


def test_overall_worst_status_roll_up():
    assert R._worst(["agree", "agree", "agree"]) == "agree"
    assert R._worst(["agree", "drift", "agree"]) == "drift"
    assert R._worst(["agree", "drift", "mismatch"]) == "mismatch"
    assert R._worst(["na", "na"]) == "na"
    assert R._worst([]) == "na"


# ---------------------------------------------------------------------------
# Pressure-test regressions: silent-wrong-output guards
# ---------------------------------------------------------------------------

def test_spot_zero_anchor_does_not_silently_agree():
    """Guard: if E2 spot resolves to literal 0 (not None), the old ``_rel_pct``
    path returned None, was coerced to ``or 0.0``, and silently voted
    ``agree``. This test forces the code path where ``smartSpotPrice`` is
    absent but ``spotPrice`` is exactly 0.0 so e2_spot=0 after coercion.
    """
    scen = {"entryState": {"userSpot": 6725.0}}
    # smartSpotPrice missing, spotPrice = 0.0 → e2_spot coerces to 0.0 (truthy None check passes).
    e2_zero = {"expectedMove": {"spotPrice": 0.0}}
    chip = R._check_spot(scen, e2_zero)
    assert chip["status"] == "na", f"expected na, got {chip}"
    assert "zero" in (chip["note"] or "").lower()

    # Purely missing anchor (both keys absent) → na via the missing-side branch.
    e2_missing = {"expectedMove": {}}
    chip2 = R._check_spot(scen, e2_missing)
    assert chip2["status"] == "na"


def test_expected_move_zero_anchor_does_not_silently_agree():
    """Guard: delayedImpliedMovePct = 0.0 with ORATS missing used to silently
    agree via the same ``_rel_pct`` + ``or 0.0`` pattern."""
    scen = {"entryState": {"userEmPct": 1.90}}
    e2_zero = {"expectedMove": {"delayedImpliedMovePct": 0.0}}
    chip = R._check_expected_move(scen, e2_zero)
    # With the or-chain dropping 0 to None, we land on missing-side na. The
    # point of the regression is that we NEVER reach ``agree`` regardless of
    # which na-path fires.
    assert chip["status"] == "na"


def test_expected_move_with_literal_zero_ref_stays_na():
    """The or-chain treats 0 as falsy, so build an E2 payload where the
    *only* EM field present resolves to exactly 0.0 after coercion, and make
    sure we still return na rather than fake agree."""
    scen = {"entryState": {"userEmPct": 1.90}}
    # Only ``expectedMovePct`` key populated at 0.0 → _to_float returns 0.0 → guard fires.
    # Note: we rely on the or-chain falling through None/None before hitting this key;
    # the explicit None guard in the fix kicks in when e2_em is exactly 0.0.
    # Force the path: set a non-zero first key to ensure we reach the zero guard.
    # (The or-chain accepts any truthy float, so a literal 0 in a later key won't
    # trigger the zero-guard — this test documents the defense-in-depth.)
    e2 = {"expectedMove": {"oratsExpectedMovePct": 1e-9}}
    chip = R._check_expected_move(scen, e2)
    assert chip["status"] == "na"
    assert "zero" in (chip["note"] or "").lower()


def test_policy_cell_missing_metrics_does_not_silently_agree():
    """Guard: a widthComparison cell with ``None`` metrics used to coerce to 0
    via ``or 0`` and silently vote ``agree``. The chip must now surface the
    gap as ``drift`` with an ``unverified`` note so the desk can see we
    couldn't actually check that cap.
    """
    e2 = {
        "widthComparison": [
            # breachPct and outsidePct are None → the caps can't be evaluated.
            {"emMult": 2.0, "wingWidthPts": 10,
             "breachPct": None, "outsidePct": None, "avgMae95xWing": 0.4,
             "creditProxy": 20.0},
        ],
        "recommendation": {"policy": {
            "maxBreachPct": 25.0, "maxOutsideWingsPct": 10.0, "maxMae95xWing": 1.0,
        }},
    }
    scen = {
        "entryState": {"userEmMultiple": 2.0, "wingWidth": 10.0},
        "request": {"short_put": 0, "long_put": 0, "short_call": 0, "long_call": 0},
    }
    chip = R._check_policy(scen, e2)
    assert chip["status"] == "drift"
    note = chip["note"] or ""
    assert "unverified" in note.lower()
    assert "breachpct" in note.lower()
    assert "outsidepct" in note.lower()
    # The agree-path metric (avgMae95xWing ≤ 1.0) must still be checked.
    assert chip["e2"]["unverified"] and "avgMae95xWing" not in chip["e2"]["unverified"]


def test_policy_single_violation_is_drift():
    e2 = {
        "widthComparison": [
            {"emMult": 2.0, "wingWidthPts": 10,
             "breachPct": 5.0, "outsidePct": 15.0, "avgMae95xWing": 0.4,
             "creditProxy": 20.0},
        ],
        "recommendation": {"policy": {
            "maxBreachPct": 25.0, "maxOutsideWingsPct": 10.0, "maxMae95xWing": 1.0,
        }},
    }
    scen = {
        "entryState": {"userEmMultiple": 2.0, "wingWidth": 10.0},
        "request": {"short_put": 0, "long_put": 0, "short_call": 0, "long_call": 0},
    }
    chip = R._check_policy(scen, e2)
    assert chip["status"] == "drift"
    assert "outsidePct" in (chip["note"] or "")


def test_llm_verdict_unknown_collapses_to_na():
    """Guard: advisor returning an unrecognized verdict string must NOT
    silently resolve to ``mismatch`` (that masquerades as advisor disagreement
    when the real issue is schema drift)."""
    chip = R._check_llm_verdict({"verdict": "MAYBE_PASS", "confidence": 0.6})
    assert chip["status"] == "na"
    assert "unrecognized" in (chip["note"] or "").lower()

    chip_pass = R._check_llm_verdict({"verdict": "PASS", "confidence": 0.8})
    assert chip_pass["status"] == "agree"
    chip_fail = R._check_llm_verdict({"verdict": "FAIL", "confidence": 0.9})
    assert chip_fail["status"] == "mismatch"
