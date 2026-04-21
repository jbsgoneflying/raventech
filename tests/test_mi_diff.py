"""Tests for backend.market_intel.diff."""
from __future__ import annotations

import pytest

from backend.market_intel import compute_market_diff


def _dms(date, *, probs=None, factor_z=None, gates=None, loadings=None):
    out = {"date": date, "regime": {}, "engine_gates": gates or {}}
    if probs is not None:
        out["regime"]["probs"] = probs
    if factor_z is not None:
        out["regime"]["factor_readings"] = {
            k: {"z": v, "label": k.upper()} for k, v in factor_z.items()
        }
    if loadings is not None:
        out["cross_asset_stress"] = {"per_asset_loadings": loadings}
    return out


def test_quiet_tape_no_changes():
    today = _dms("2026-04-22", probs={"risk_on": 0.5, "transitional": 0.4, "stressed": 0.1})
    yest = _dms("2026-04-21", probs={"risk_on": 0.5, "transitional": 0.4, "stressed": 0.1})
    d = compute_market_diff(today_dms=today, yesterday_dms=yest)
    assert not d.has_changes
    assert d.regime_flip_delta == 0.0
    assert d.regime_flip_is_material is False
    assert d.headline_summary == "Quiet tape"


def test_material_flip_flagged():
    today = _dms("2026-04-22", probs={"risk_on": 0.1, "transitional": 0.2, "stressed": 0.7})
    yest = _dms("2026-04-21", probs={"risk_on": 0.5, "transitional": 0.3, "stressed": 0.2})
    d = compute_market_diff(today_dms=today, yesterday_dms=yest)
    assert d.regime_flip_delta == pytest.approx(0.5, abs=1e-3)
    assert d.regime_flip_is_material is True
    assert "P(stressed)" in d.headline_summary


def test_top_factor_moves_ranked_by_abs_delta():
    today = _dms("2026-04-22", factor_z={"a": 2.0, "b": 0.0, "c": 1.0})
    yest  = _dms("2026-04-21", factor_z={"a": 0.0, "b": 0.0, "c": 0.5})
    d = compute_market_diff(today_dms=today, yesterday_dms=yest, top_n_factors=2)
    assert len(d.top_factor_moves) == 2
    assert d.top_factor_moves[0]["key"] == "a"  # |Δz=2.0| largest
    assert d.top_factor_moves[1]["key"] == "c"  # |Δz=0.5|


def test_engine_gate_changes_detected():
    today = _dms("2026-04-22", gates={"earnings": "suppressed", "red_dog": "allowed"})
    yest  = _dms("2026-04-21", gates={"earnings": "allowed", "red_dog": "allowed"})
    d = compute_market_diff(today_dms=today, yesterday_dms=yest)
    assert len(d.engine_gate_changes) == 1
    assert d.engine_gate_changes[0]["engine"] == "earnings"
    assert d.engine_gate_changes[0]["from_state"] == "allowed"
    assert d.engine_gate_changes[0]["to_state"] == "suppressed"
    assert "earnings" in d.headline_summary


def test_threshold_proximity_with_v2_probs():
    today = _dms("2026-04-22", probs={"risk_on": 0.2, "transitional": 0.3, "stressed": 0.48})
    yest  = _dms("2026-04-21", probs={"risk_on": 0.2, "transitional": 0.3, "stressed": 0.42})
    d = compute_market_diff(today_dms=today, yesterday_dms=yest)
    prox = d.regime_threshold_proximity
    assert "p_stressed_today" in prox
    assert prox["p_stressed_today"] == pytest.approx(0.48, abs=1e-3)
    assert prox["distance_to_flip"] == pytest.approx(0.02, abs=1e-3)
    assert prox["crossed_flip"] is False


def test_threshold_proximity_crossed_flip():
    today = _dms("2026-04-22", probs={"risk_on": 0.1, "transitional": 0.2, "stressed": 0.7})
    yest  = _dms("2026-04-21", probs={"risk_on": 0.3, "transitional": 0.3, "stressed": 0.4})
    d = compute_market_diff(today_dms=today, yesterday_dms=yest)
    assert d.regime_threshold_proximity["crossed_flip"] is True


def test_correlation_breaks_from_loadings():
    today = _dms("2026-04-22", loadings={"HYG": 1.5, "TLT": 0.3})
    yest  = _dms("2026-04-21", loadings={"HYG": 0.2, "TLT": 0.2})
    d = compute_market_diff(today_dms=today, yesterday_dms=yest)
    # |HYG delta = 1.3| >= 1.0 trigger
    assets = [b["asset_a"] for b in d.correlation_breaks]
    assert "HYG" in assets


def test_v1_legacy_dms_falls_back_to_label_proximity():
    today = {"date": "2026-04-22", "regime": {"state": "Stressed"}}
    yest  = {"date": "2026-04-21", "regime": {"state": "Risk-On"}}
    d = compute_market_diff(today_dms=today, yesterday_dms=yest)
    assert d.regime_flip_is_material  # label changed
    assert d.regime_threshold_proximity["regime_changed"] is True
