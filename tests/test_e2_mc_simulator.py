from __future__ import annotations

import random

from backend.engine2.mc_simulator import (
    MCResult,
    run_weekly_mc,
)


def _pool(rng: random.Random, regime: str = "LOW", macro: str = "NORMAL", n: int = 40):
    return [
        {
            "regime_bucket": regime,
            "macro_bucket":  macro,
            "daily_returns": [rng.gauss(0, 0.008) for _ in range(5)],
        }
        for _ in range(n)
    ]


def test_mc_runs_and_returns_per_placement_stats():
    pool = _pool(random.Random(1), "LOW", "NORMAL", n=50)
    r = run_weekly_mc(
        ticker="SPX", as_of_date="2026-04-21", spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=pool, placements=[(1.0, 10.0), (1.5, 10.0), (2.0, 10.0)],
        n_sims=1000, min_pool=10,
        want_regime_bucket="LOW", want_macro_bucket="NORMAL",
    )
    assert isinstance(r, MCResult)
    assert r.n_sims == 1000
    assert r.mode == "bootstrap"
    assert r.conditioning_used in ("regime+macro", "regime", "unconditioned")
    assert len(r.placements) == 3
    # wider EM should give lower breach probability
    breaches = [p.breach_close_prob for p in r.placements]
    assert breaches[0] >= breaches[1] >= breaches[2]


def test_mc_deterministic_for_fixed_inputs():
    pool = _pool(random.Random(7), "LOW", "NORMAL", n=30)
    kw = dict(
        ticker="SPX", as_of_date="2026-04-21", spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=pool, placements=[(1.0, 10.0), (1.5, 10.0)],
        n_sims=800, min_pool=10,
        want_regime_bucket="LOW", want_macro_bucket="NORMAL",
    )
    r1 = run_weekly_mc(**kw)
    r2 = run_weekly_mc(**kw)
    assert r1.seed == r2.seed
    for p1, p2 in zip(r1.placements, r2.placements):
        assert p1.breach_close_prob == p2.breach_close_prob
        assert p1.touch_intraweek_prob == p2.touch_intraweek_prob


def test_mc_conditioning_degrades_when_pool_thin():
    pool = _pool(random.Random(2), "LOW", "NORMAL", n=40)
    # Request a regime/macro pairing that doesn't match any row.
    r = run_weekly_mc(
        ticker="SPX", as_of_date="2026-04-21", spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=pool, placements=[(1.0, 10.0)],
        n_sims=200, min_pool=10,
        want_regime_bucket="ELEVATED", want_macro_bucket="MACRO",
    )
    # No rows match -> degrades to unconditioned and notes it.
    assert r.conditioning_used == "unconditioned"
    assert any("conditioning_degraded" in n for n in r.notes)


def test_mc_empty_pool_returns_unavailable():
    r = run_weekly_mc(
        ticker="SPX", as_of_date="2026-04-21", spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=[], placements=[(1.0, 10.0)], n_sims=100,
    )
    assert r.mode == "unavailable"
    assert r.n_sims == 0


def test_mc_touch_prob_gte_close_prob():
    # A weekly path that touches a strike midweek might close back inside;
    # touch should always be >= close breach.
    pool = _pool(random.Random(3), "LOW", "NORMAL", n=50)
    r = run_weekly_mc(
        ticker="SPX", as_of_date="2026-04-21", spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=pool, placements=[(1.5, 10.0)], n_sims=1000, min_pool=10,
    )
    for p in r.placements:
        assert p.touch_intraweek_prob + 1e-9 >= p.breach_close_prob
