"""Unit tests for backend.market_intel.factors."""
from __future__ import annotations

import datetime as dt

import pytest

from backend.market_intel.factors import (
    FACTOR_KEYS,
    FactorReading,
    FactorSnapshot,
    MISSING,
    OK,
    STALE,
    _pairwise_corr,
    _parse_bar_closes,
    _pct_returns,
    _quality_for,
    _realized_vol_annualized,
    _rolling_z,
    build_factor_matrix,
    build_factor_snapshot,
)


def test_factor_keys_are_stable_and_unique():
    assert len(FACTOR_KEYS) == 8
    assert len(set(FACTOR_KEYS)) == 8
    # Specific ordering matters for HMM consumers — pin it.
    assert FACTOR_KEYS[0] == "rv_spx_20d"
    assert FACTOR_KEYS[-1] == "breadth_proxy"


def test_rolling_z_zero_on_thin_data():
    assert _rolling_z([]) == 0.0
    assert _rolling_z([1.0, 2.0]) == 0.0


def test_rolling_z_known_distribution():
    # 100 values from N(0,1)-ish: known mu/sigma → z of last point.
    base = [float(i % 10) for i in range(100)]  # mean ~ 4.5, stdev ~ 2.87
    last = 12.0
    z = _rolling_z(base + [last])
    assert z > 1.0  # last value is above mean
    # cap is +/-4
    assert -4.0 <= z <= 4.0


def test_rolling_z_capped_at_4():
    base = [0.0] * 200
    z = _rolling_z(base + [1000.0])
    assert z <= 4.0
    z = _rolling_z(base + [-1000.0])
    assert z >= -4.0


def test_pct_returns_drops_zero_priors():
    # _pct_returns guards against prior <= 0; (10→0) is allowed (prev>0)
    # but (0→10) is dropped (prev not > 0).
    closes = [10.0, 0.0, 10.0, 11.0, 12.1]
    rets = _pct_returns(closes)
    # 10→0 = -1.0, 0→10 dropped, 10→11 = 0.1, 11→12.1 = 0.1
    assert len(rets) == 3
    assert pytest.approx(rets[0], rel=1e-6) == -1.0
    assert pytest.approx(rets[-1], rel=1e-6) == 0.1


def test_pct_returns_drops_negative_priors_too():
    closes = [-5.0, 10.0, 11.0]
    rets = _pct_returns(closes)
    # Negative prior → first pair dropped, only 10→11 kept.
    assert len(rets) == 1
    assert pytest.approx(rets[0], rel=1e-6) == 0.1


def test_realized_vol_annualized_known_input():
    # Constant returns of 1% → daily stdev 0 → 0 annualized.
    closes = [100.0 * (1.01 ** i) for i in range(30)]
    rv = _realized_vol_annualized(closes, window=20)
    # Stdev of constant returns is 0
    assert rv == pytest.approx(0.0, abs=1e-6)


def test_realized_vol_annualized_alternating():
    # Alternating up-down 1% has nonzero stdev.
    closes = [100.0]
    for i in range(30):
        closes.append(closes[-1] * (1.01 if i % 2 == 0 else 0.99))
    rv = _realized_vol_annualized(closes, window=20)
    assert rv > 0
    # Should be roughly 1% × √252 × 100% ≈ 15.9%
    assert 10 < rv < 30


def test_pairwise_corr_perfect():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [10.0, 20.0, 30.0, 40.0, 50.0]
    assert pytest.approx(_pairwise_corr(a, b), abs=1e-6) == 1.0


def test_pairwise_corr_inverse():
    a = [1.0, 2.0, 3.0, 4.0, 5.0]
    b = [50.0, 40.0, 30.0, 20.0, 10.0]
    assert pytest.approx(_pairwise_corr(a, b), abs=1e-6) == -1.0


def test_pairwise_corr_uncorrelated_zero():
    assert _pairwise_corr([1.0], [2.0]) == 0.0
    assert _pairwise_corr([], []) == 0.0


def test_parse_bar_closes_orders_and_filters():
    rows = [
        {"date": "2026-04-21", "close": 100.0},
        {"date": "2026-04-19", "close": 95.0},
        {"date": "2026-04-20", "close": -5.0},  # invalid
        {"date": "2026-04-22", "adjusted_close": 101.0, "close": 100.5},
    ]
    closes, dates = _parse_bar_closes(rows)
    assert dates == ["2026-04-19", "2026-04-21", "2026-04-22"]
    assert closes == [95.0, 100.0, 101.0]


def test_quality_for_recent_is_ok():
    today = dt.date.today().isoformat()
    quality, as_of = _quality_for([1.0], [today], stale_days=1)
    assert quality == OK
    assert as_of == today


def test_quality_for_old_is_stale():
    old = (dt.date.today() - dt.timedelta(days=14)).isoformat()
    quality, as_of = _quality_for([1.0], [old], stale_days=1)
    assert quality == STALE


def test_quality_for_empty_is_missing():
    quality, as_of = _quality_for([], [], stale_days=1)
    assert quality == MISSING


def test_build_factor_snapshot_no_clients_returns_all_missing():
    snap = build_factor_snapshot(eodhd_client=None, gamma_context=None)
    assert isinstance(snap, FactorSnapshot)
    assert len(snap.readings) == 8
    # All factors should be MISSING when no clients are wired.
    for key, reading in snap.readings.items():
        assert reading.quality == MISSING, f"{key} should be MISSING"
    assert len(snap.missing) == 8
    assert snap.ok == []
    assert snap.stale == []


def test_factor_snapshot_vector_is_canonical_order():
    snap = build_factor_snapshot(eodhd_client=None, gamma_context=None)
    vec = snap.vector
    assert len(vec) == 8
    # Missing factors → 0.0
    assert all(v == 0.0 for v in vec)


def test_build_factor_matrix_stacks_correctly():
    s1 = build_factor_snapshot(eodhd_client=None, gamma_context=None)
    s2 = build_factor_snapshot(eodhd_client=None, gamma_context=None)
    matrix = build_factor_matrix([s1, s2])
    assert len(matrix) == 2
    assert all(len(row) == 8 for row in matrix)


def test_dealer_gamma_factor_with_context():
    from backend.market_intel.factors import _dealer_gamma
    # Negative gamma → positive z (stress).
    r = _dealer_gamma({"sign": "negative", "magnitude_z": 1.5})
    assert r.quality == OK
    assert r.z > 0
    # Positive gamma → negative z.
    r = _dealer_gamma({"sign": "positive", "magnitude_z": 1.5})
    assert r.z < 0
    # Missing context → MISSING quality.
    r = _dealer_gamma(None)
    assert r.quality == MISSING
