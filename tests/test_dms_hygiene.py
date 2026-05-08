"""Tests for the v1 DMS hygiene hotfix.

Verifies:
  - ``_stress_score_from_probs`` maps cluster probs to the 0-100 scale
  - ``_is_skeleton_default`` correctly fingerprints empty DMS docs
  - These are isolated unit tests on the helpers; the full
    ``build_dms_v2`` integration is exercised by an existing test in
    backend/tests on the v1 side.
"""

from __future__ import annotations

from types import SimpleNamespace

from backend.market_intel.dms_builder import (
    _is_skeleton_default,
    _stress_score_from_probs,
)


# ── _stress_score_from_probs ───────────────────────────────


def test_stress_score_pure_risk_on() -> None:
    score = _stress_score_from_probs({"risk_on": 1.0, "transitional": 0.0, "stressed": 0.0})
    assert score == 0.0


def test_stress_score_pure_stressed() -> None:
    score = _stress_score_from_probs({"risk_on": 0.0, "transitional": 0.0, "stressed": 1.0})
    assert score == 100.0


def test_stress_score_pure_transitional() -> None:
    score = _stress_score_from_probs({"risk_on": 0.0, "transitional": 1.0, "stressed": 0.0})
    assert score == 50.0


def test_stress_score_typical_risk_on_distribution() -> None:
    # 0.80 risk-on / 0.18 transitional / 0.02 stressed should land near 11.
    score = _stress_score_from_probs(
        {"risk_on": 0.80, "transitional": 0.18, "stressed": 0.02}
    )
    assert 8.0 <= score <= 14.0


def test_stress_score_typical_stressed_distribution() -> None:
    score = _stress_score_from_probs(
        {"risk_on": 0.02, "transitional": 0.18, "stressed": 0.80}
    )
    assert 86.0 <= score <= 92.0


def test_stress_score_handles_empty_dict() -> None:
    assert _stress_score_from_probs({}) == 50.0


def test_stress_score_handles_none_values() -> None:
    score = _stress_score_from_probs(
        {"risk_on": None, "transitional": None, "stressed": None}
    )
    assert score == 50.0


def test_stress_score_normalizes_unnormalized_probs() -> None:
    # 0.4/0.4/0.4 (sum=1.2) should still produce a sensible score.
    score = _stress_score_from_probs(
        {"risk_on": 0.4, "transitional": 0.4, "stressed": 0.4}
    )
    assert score == 50.0  # equal mass after normalization → mid


# ── _is_skeleton_default ───────────────────────────────────


def _skeleton_dms() -> dict:
    return {
        "regime": {"state": "Transitional", "score": 50.0, "drivers": []},
        "vol_state": {"level": 25.0, "term_structure": "flat", "skew": "neutral"},
        "news_risk": {"today": "low", "week_ahead": []},
    }


def test_skeleton_default_detected_with_empty_source() -> None:
    mi = SimpleNamespace(source="")
    assert _is_skeleton_default(_skeleton_dms(), mi) is True


def test_skeleton_default_detected_with_legacy_fallback() -> None:
    mi = SimpleNamespace(source="legacy_fallback")
    assert _is_skeleton_default(_skeleton_dms(), mi) is True


def test_real_signal_not_skeleton_even_if_neutral() -> None:
    mi = SimpleNamespace(source="v2_hmm")
    assert _is_skeleton_default(_skeleton_dms(), mi) is False


def test_non_default_score_not_skeleton() -> None:
    mi = SimpleNamespace(source="legacy_fallback")
    doc = _skeleton_dms()
    doc["regime"]["score"] = 72.0
    assert _is_skeleton_default(doc, mi) is False


def test_non_default_vol_not_skeleton() -> None:
    mi = SimpleNamespace(source="legacy_fallback")
    doc = _skeleton_dms()
    doc["vol_state"]["term_structure"] = "backwardation"
    assert _is_skeleton_default(doc, mi) is False


def test_non_default_news_not_skeleton() -> None:
    mi = SimpleNamespace(source="legacy_fallback")
    doc = _skeleton_dms()
    doc["news_risk"]["today"] = "high"
    assert _is_skeleton_default(doc, mi) is False


def test_partial_doc_not_skeleton() -> None:
    """A doc missing required sections doesn't match the skeleton fingerprint."""
    mi = SimpleNamespace(source="legacy_fallback")
    assert _is_skeleton_default({}, mi) is False
    assert _is_skeleton_default({"regime": {}}, mi) is False
