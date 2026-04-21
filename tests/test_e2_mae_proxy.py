from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest

from backend.engine2.mae_proxy import (
    MAEDistribution,
    compute_mae_distribution,
    mae_p95_vs_wing_ratio,
)


@dataclass
class _Bar:
    open:  Optional[float]
    high:  Optional[float]
    low:   Optional[float]
    close: Optional[float]


def test_compute_mae_distribution_empty_windows_returns_zero_n():
    dist = compute_mae_distribution(windows=[], bars_by_date={})
    assert isinstance(dist, MAEDistribution)
    assert dist.n == 0
    assert "mae_pool_empty" in dist.notes[0]


def test_compute_mae_distribution_picks_worst_excursion():
    windows = [{"entry_date": "2026-01-06", "expiry_date": "2026-01-10", "entry_close": 100.0}]
    bars_by_date = {
        "2026-01-07": _Bar(open=100, high=101,  low=99,  close=100),
        "2026-01-08": _Bar(open=100, high=105,  low=99,  close=101),    # +5% up
        "2026-01-09": _Bar(open=101, high=103,  low=95,  close=97),     # -5% dn
        "2026-01-10": _Bar(open=97,  high=98,   low=96,  close=97),
    }
    dist = compute_mae_distribution(windows=windows, bars_by_date=bars_by_date)
    assert dist.n == 1
    # worst is 5% (tie broken to whichever side was larger; here abs equal)
    assert dist.p95 >= 4.99
    assert dist.source == "daily_ohlc"


def test_compute_mae_distribution_falls_back_without_high_low():
    windows = [{"entry_date": "2026-01-06", "expiry_date": "2026-01-10", "entry_close": 100.0}]
    bars_by_date = {
        "2026-01-07": _Bar(open=100, high=None, low=None, close=103),  # +3%
        "2026-01-08": _Bar(open=100, high=None, low=None, close=101),
    }
    dist = compute_mae_distribution(windows=windows, bars_by_date=bars_by_date)
    assert dist.n == 1
    assert dist.source == "open_close_fallback"
    assert dist.p95 >= 2.99


def test_compute_mae_distribution_percentiles_monotone():
    windows = [
        {"entry_date": f"2026-01-0{i}", "expiry_date": f"2026-01-0{i+2}", "entry_close": 100.0}
        for i in range(1, 6)
    ]
    bars_by_date = {}
    moves = [1.0, 2.0, 3.0, 4.0, 5.0]
    for i, m in enumerate(moves):
        bars_by_date[f"2026-01-0{i+2}"] = _Bar(open=100, high=100*(1+m/100), low=99, close=100)
    dist = compute_mae_distribution(windows=windows, bars_by_date=bars_by_date)
    assert dist.n == 5
    assert dist.p50 <= dist.p75 <= dist.p90 <= dist.p95 <= dist.max


def test_mae_p95_vs_wing_ratio_penalizes_deep_moves():
    # Spot 5000, EM% 1.5, em_mult 1.0 -> short at 1.5% move
    # Wing width 15 pts = 0.3% of spot. p95 MAE = 3% -> 1.5% past shorts
    # 1.5% of 5000 = 75 pts; 75/15 = 5.0 but clamped at 1.5
    ratio = mae_p95_vs_wing_ratio(
        mae_p95_pct=3.0, em_multiple=1.0, implied_move_pct=1.5,
        wing_width_pts=15.0, spot=5000.0,
    )
    assert 1.49 <= ratio <= 1.5


def test_mae_p95_vs_wing_ratio_zero_when_inside_shorts():
    ratio = mae_p95_vs_wing_ratio(
        mae_p95_pct=1.0, em_multiple=1.0, implied_move_pct=1.5,
        wing_width_pts=15.0, spot=5000.0,
    )
    assert ratio == 0.0


def test_mae_p95_vs_wing_ratio_guards_invalid_inputs():
    for bad in [
        dict(mae_p95_pct=1.0, em_multiple=0, implied_move_pct=1.5, wing_width_pts=15, spot=5000),
        dict(mae_p95_pct=1.0, em_multiple=1, implied_move_pct=0, wing_width_pts=15, spot=5000),
        dict(mae_p95_pct=1.0, em_multiple=1, implied_move_pct=1.5, wing_width_pts=0, spot=5000),
        dict(mae_p95_pct=1.0, em_multiple=1, implied_move_pct=1.5, wing_width_pts=15, spot=0),
    ]:
        assert mae_p95_vs_wing_ratio(**bad) == 0.0
