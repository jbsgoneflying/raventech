"""Engine 1 v2 — MAE proxy unit tests."""
from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Optional

import pytest

from backend.engine1.mae_proxy import (
    EventOHLC,
    MAEDistribution,
    _compute_single_event_mae,
    _percentile,
    compute_mae_distribution,
    mae_percentile_to_credit_pct,
)


@dataclass
class _HoldRiskEvent:
    """Mirror of backend.earnings_hold_risk.HoldRiskEvent for tests."""
    earn_date:  str = ""
    timing:     str = ""
    prior_close: Optional[float] = None
    earnings_day_open:  Optional[float] = None
    earnings_day_close: Optional[float] = None
    next_day_close:     Optional[float] = None
    expected_move_pct:  Optional[float] = None


@dataclass
class _DailyBar:
    """Mirror of price_service.DailyBar shape (with high/low)."""
    trade_date: str = ""
    open:  Optional[float] = None
    high:  Optional[float] = None
    low:   Optional[float] = None
    close: Optional[float] = None


def test_percentile_linear_interpolation():
    xs = [1.0, 2.0, 3.0, 4.0]
    assert _percentile(xs, 50) == pytest.approx(2.5)
    assert _percentile(xs, 0) == pytest.approx(1.0)
    assert _percentile(xs, 100) == pytest.approx(4.0)
    assert _percentile([], 50) == 0.0
    assert _percentile([7.5], 95) == pytest.approx(7.5)


def test_single_event_mae_prefers_high_low():
    bars = [EventOHLC(date="d1", open=100, high=112, low=99, close=110)]
    pct, direction, source = _compute_single_event_mae(100.0, bars)
    # 112 vs entry 100 → +12% up excursion
    assert pct == pytest.approx(12.0)
    assert direction == "up"
    assert source == "daily_ohlc_proxy"


def test_single_event_mae_down_wins():
    bars = [EventOHLC(date="d1", open=100, high=101, low=88, close=90)]
    pct, direction, source = _compute_single_event_mae(100.0, bars)
    # entry 100 vs low 88 → 12%
    assert pct == pytest.approx(12.0)
    assert direction == "down"


def test_single_event_mae_fallback_to_open_close():
    bars = [EventOHLC(date="d1", open=105, high=None, low=None, close=108)]
    pct, direction, source = _compute_single_event_mae(100.0, bars)
    assert source == "open_close_fallback"
    assert direction == "up"
    assert pct == pytest.approx(8.0)


def test_single_event_mae_flat_pool_returns_zero():
    bars = [EventOHLC(date="d1", open=100, high=100, low=100, close=100)]
    pct, direction, source = _compute_single_event_mae(100.0, bars)
    assert pct == pytest.approx(0.0)
    assert direction == "flat"


def test_single_event_mae_guards_invalid_entry():
    assert _compute_single_event_mae(0.0, [])[0] is None
    assert _compute_single_event_mae(100.0, [])[0] is None


def test_compute_mae_distribution_aggregates_percentiles():
    # Four AMC events with varying excursions
    hre = [
        _HoldRiskEvent(earn_date="2024-01-15", timing="AMC", prior_close=100),
        _HoldRiskEvent(earn_date="2024-04-15", timing="AMC", prior_close=100),
        _HoldRiskEvent(earn_date="2024-07-15", timing="AMC", prior_close=100),
        _HoldRiskEvent(earn_date="2024-10-15", timing="AMC", prior_close=100),
    ]
    excursions_pct = [2.0, 5.0, 8.0, 15.0]
    dailies = {}
    for event, pct in zip(hre, excursions_pct):
        # AMC: hold window starts one day after earn_date
        start = dt.date.fromisoformat(event.earn_date) + dt.timedelta(days=1)
        dailies[start.isoformat()] = _DailyBar(
            trade_date=start.isoformat(),
            open=100, high=100 + pct, low=99.5, close=100 + pct,
        )

    dist = compute_mae_distribution(
        hold_risk_events=hre,
        dailies_cache=dailies,
        hold_days=1,
    )

    assert dist.n == 4
    assert dist.p50 == pytest.approx(6.5)  # median of 2,5,8,15
    assert dist.p95 == pytest.approx(14.05, rel=0.05)  # linear-interp close to max
    assert dist.source == "daily_ohlc_proxy"


def test_compute_mae_distribution_handles_missing_events():
    dist = compute_mae_distribution(
        hold_risk_events=[],
        dailies_cache={},
    )
    assert dist.n == 0
    assert "no_events" in "".join(dist.notes)


def test_compute_mae_distribution_fallback_source_tag():
    hre = [_HoldRiskEvent(earn_date="2024-01-15", timing="AMC", prior_close=100)]
    start = dt.date(2024, 1, 16).isoformat()
    # No high/low → fallback path
    dailies = {start: _DailyBar(trade_date=start, open=100, high=None, low=None, close=105)}
    dist = compute_mae_distribution(hold_risk_events=hre, dailies_cache=dailies, hold_days=1)
    assert dist.n == 1
    assert "fallback" in dist.source or dist.source == "open_close_fallback"


def test_mae_percentile_to_credit_pct_saturates():
    # A 20% excursion against a 1.5× EM placement with 10pt wings on a $100 spot:
    # intrinsic past short = (20 - 1.5*5) = 12.5% of spot = $12.50
    # that's > 10pt wing → ratio should clamp at 1.5
    r = mae_percentile_to_credit_pct(
        mae_pct_move=20.0, em_multiple=1.5, implied_move_pct=5.0,
        wing_width_pts=10.0, underlying_spot=100.0,
    )
    assert 1.0 <= r <= 1.5


def test_mae_percentile_to_credit_pct_zero_when_inside_short():
    r = mae_percentile_to_credit_pct(
        mae_pct_move=3.0, em_multiple=1.0, implied_move_pct=5.0,
        wing_width_pts=10.0, underlying_spot=100.0,
    )
    assert r == 0.0  # 3% < 1*5% → never past short


def test_mae_percentile_to_credit_pct_guards_bad_inputs():
    assert mae_percentile_to_credit_pct(
        mae_pct_move=10, em_multiple=0, implied_move_pct=5,
        wing_width_pts=5, underlying_spot=100,
    ) == 0.0
    assert mae_percentile_to_credit_pct(
        mae_pct_move=10, em_multiple=1, implied_move_pct=5,
        wing_width_pts=0, underlying_spot=100,
    ) == 0.0
