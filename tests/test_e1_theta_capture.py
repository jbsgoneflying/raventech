"""Engine 1 v2 — Theta-capture estimator unit tests."""
from __future__ import annotations

import pytest

from backend.engine1.theta_capture import (
    ThetaCaptureReading,
    estimate_theta_capture,
    expected_decay_capture,
)


def _event(signed_pct, implied_pct):
    return {"signedMovePct": signed_pct, "impliedMovePct": implied_pct}


def test_estimate_theta_capture_empty_pool():
    r = estimate_theta_capture([])
    assert r.n_events == 0
    assert r.decay_richness == 0.0
    assert any("no event ratios" in n for n in r.notes)


def test_estimate_theta_capture_rich_regime():
    # Market consistently over-prices the move; realized/implied is tiny
    events = [_event(1.0, 5.0) for _ in range(10)]   # ratio 0.2
    r = estimate_theta_capture(events)
    assert r.n_events == 10
    assert r.mean_move_ratio == pytest.approx(0.2)
    assert r.decay_richness == pytest.approx(0.8)
    assert any("over-priced" in n for n in r.notes)


def test_estimate_theta_capture_cheap_regime_floored():
    # Market under-prices the move; realized > implied
    events = [_event(20.0, 5.0) for _ in range(5)]    # ratio 4.0
    r = estimate_theta_capture(events)
    assert r.decay_richness == 0.10  # floored
    assert any("under-priced" in n for n in r.notes)


def test_estimate_theta_capture_handles_bad_values():
    events = [
        _event(None, 5.0),
        _event(2.0, None),
        _event(2.0, 0),
        _event(2.0, 5.0),       # only this one contributes
    ]
    r = estimate_theta_capture(events)
    assert r.n_events == 1
    assert r.mean_move_ratio == pytest.approx(0.4)


def test_expected_decay_capture_survival_times_richness():
    # 10 events, 8 survive at em_mult=1.0 (ratio<=1) → survival = 0.8
    events = [_event(r, 1.0) for r in [0.2, 0.3, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.2, 1.5]]
    r = estimate_theta_capture(events)
    out = expected_decay_capture(reading=r, events=events, em_multiple=1.0)
    # With richness and 80% survival, capture should sit in 0.3*0.8 to 0.95*0.8 range
    assert out["survival_rate"] == pytest.approx(0.8)
    assert 20.0 <= out["capture_pct"] <= 80.0


def test_expected_decay_capture_zero_em_is_zero():
    r = ThetaCaptureReading()
    out = expected_decay_capture(reading=r, events=[], em_multiple=0.0)
    assert out["capture_pct"] == 0.0
    assert out["survival_rate"] == 0.0


def test_expected_decay_capture_wider_placement_survives_more():
    events = [_event(r, 1.0) for r in [0.5, 0.9, 1.1, 1.4, 1.8]]
    r = estimate_theta_capture(events)

    at_1_0 = expected_decay_capture(reading=r, events=events, em_multiple=1.0)
    at_1_5 = expected_decay_capture(reading=r, events=events, em_multiple=1.5)
    at_2_0 = expected_decay_capture(reading=r, events=events, em_multiple=2.0)

    # Wider placement → strictly non-decreasing survival rate
    assert at_1_0["survival_rate"] <= at_1_5["survival_rate"] <= at_2_0["survival_rate"]
    # Therefore capture_pct rises (or stays) with em_multiple
    assert at_1_0["capture_pct"] <= at_1_5["capture_pct"] <= at_2_0["capture_pct"]
