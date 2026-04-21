from __future__ import annotations

import random
from dataclasses import replace

import pytest

from backend.config import FeatureFlags, get_flags
from backend.engine2 import (
    DEFAULT_WEIGHTS,
    MAEDistribution,
    PlacementScore,
    WingConsoleWeights,
    build_wing_console,
    run_weekly_mc,
    score_placements,
    score_single_placement,
)
from backend.engine2.scoring_context import (
    ScoringContext,
    clear_scoring_cache,
    get_scoring_context,
)


def _synthetic_pool(n: int = 40):
    rng = random.Random(42)
    return [
        {
            "regime_bucket": "LOW",
            "macro_bucket":  "NORMAL",
            "daily_returns": [rng.gauss(0, 0.008) for _ in range(5)],
            "signed_move_pct": 0.0,
        }
        for _ in range(n)
    ]


def test_score_placements_returns_ranked_grid():
    pool = _synthetic_pool(50)
    mc = run_weekly_mc(
        ticker="SPX", as_of_date="2026-04-21", spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=pool,
        placements=[(1.0, 10.0), (1.5, 10.0), (2.0, 10.0)],
        n_sims=600, min_pool=10,
    )
    mae = MAEDistribution(n=20, p50=0.5, p75=1.0, p90=1.4, p95=1.8, source="daily_ohlc")
    plc = score_placements(
        underlying="SPX", spot=5000.0, em_pct=1.5, hold_days=5, dte_calendar_days=5,
        historical_events=pool, mae=mae, mc_result=mc,
        em_mults=[1.0, 1.5, 2.0], wing_pts=[10.0],
    )
    assert len(plc) == 3
    # Strictly ranked by composite (descending).
    scores = [p.composite_score for p in plc]
    assert scores == sorted(scores, reverse=True)
    for p in plc:
        assert isinstance(p, PlacementScore)
        assert p.short_put_strike < 5000.0 < p.short_call_strike
        assert p.long_put_strike == p.short_put_strike - 10.0
        assert p.long_call_strike == p.short_call_strike + 10.0


def test_score_placements_respects_custom_weights():
    pool = _synthetic_pool(40)
    mc = run_weekly_mc(
        ticker="SPX", as_of_date="2026-04-21", spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=pool, placements=[(1.0, 10.0), (2.0, 10.0)],
        n_sims=500, min_pool=10,
    )
    # High MAE p95 so the 1.0× placement blows past shorts; 2.0× stays inside.
    mae = MAEDistribution(n=20, p50=0.5, p75=1.0, p90=1.4, p95=2.2, source="daily_ohlc")
    # Weight heavily on close-breach avoidance -> wider (2.0) should win.
    safety_heavy = WingConsoleWeights(close=0.80, touch=0.05, mae=0.05, theta=0.05, credit=0.05)
    plc_safe = score_placements(
        underlying="SPX", spot=5000.0, em_pct=1.5, hold_days=5, dte_calendar_days=5,
        historical_events=pool, mae=mae, mc_result=mc,
        em_mults=[1.0, 2.0], wing_pts=[10.0], weights=safety_heavy,
    )
    by_em_safe = {p.em_mult: p for p in plc_safe}
    assert by_em_safe[2.0].composite_score >= by_em_safe[1.0].composite_score

    # Scores move when weights move — a sanity check that custom weights
    # feed through the composite formula at all.
    plc_default = score_placements(
        underlying="SPX", spot=5000.0, em_pct=1.5, hold_days=5, dte_calendar_days=5,
        historical_events=pool, mae=mae, mc_result=mc,
        em_mults=[1.0, 2.0], wing_pts=[10.0], weights=DEFAULT_WEIGHTS,
    )
    by_em_default = {p.em_mult: p for p in plc_default}
    assert by_em_safe[1.0].composite_score != by_em_default[1.0].composite_score
    assert by_em_safe[2.0].composite_score != by_em_default[2.0].composite_score


def test_score_placements_guards_invalid_spot_or_em():
    assert score_placements(
        underlying="SPX", spot=0, em_pct=1.5, hold_days=5, dte_calendar_days=5,
        historical_events=[], em_mults=[1.0], wing_pts=[10.0],
    ) == []


def test_build_wing_console_emits_scoring_context():
    clear_scoring_cache()
    pool = _synthetic_pool(30)
    # Wrap as engine-style payload
    weeks = [
        {
            "entryDate": f"2026-01-{(i % 28) + 1:02d}",
            "expiryDate": f"2026-01-{((i+4) % 28) + 1:02d}",
            "entryPx":    5000.0,
            "signedMovePct": p["signed_move_pct"],
            "dailyReturns":  p["daily_returns"],
            "bucket":        p["regime_bucket"],
            "mb":            p["macro_bucket"],
        }
        for i, p in enumerate(pool)
    ]
    spx_payload = {
        "asOfDate":   "2026-04-21",
        "current":    {"stockPrice": 5000.0, "regime": {"label": "MODERATE", "bucket": "MODERATE"}, "macro": {"bucket": "NORMAL"}},
        "expectedMove": {"expectedMovePct": 1.5, "dte": 5, "smartSpotPrice": 5000.0},
        "weeks":      weeks,
    }
    flags = get_flags()
    console = build_wing_console(
        underlying="SPX", entry_day="mon", as_of_date="2026-04-21",
        spx_payload=spx_payload, flags=flags,
    )
    assert console.underlying == "SPX"
    assert console.n_historical == len(pool)
    assert len(console.placements) > 0

    ctx = get_scoring_context("SPX", "mon", "2026-04-21")
    assert isinstance(ctx, ScoringContext)
    assert ctx.spot == 5000.0
    assert ctx.em_pct == 1.5


def test_score_single_placement_against_cached_context():
    clear_scoring_cache()
    pool = _synthetic_pool(40)
    ctx = ScoringContext(
        underlying="SPX", entry_day="mon", as_of_date="2026-04-21",
        spot=5000.0, em_pct=1.5, hold_days=5,
        weekly_pool=pool,
        mae_dist={"n": 20, "p50": 0.5, "p75": 1.0, "p90": 1.4, "p95": 1.8, "source": "daily_ohlc"},
        regime_bucket="LOW", macro_bucket="NORMAL",
        weights=DEFAULT_WEIGHTS.as_dict(),
    )
    from backend.engine2.scoring_context import store_scoring_context
    store_scoring_context(ctx)
    retrieved = get_scoring_context("SPX", "mon", "2026-04-21")
    assert retrieved is ctx

    # Now score an arbitrary off-grid point.
    placement = score_single_placement(
        context=retrieved, em_mult=1.35, wing_pts=12.5,
    )
    assert isinstance(placement, PlacementScore)
    assert placement.em_mult == pytest.approx(1.35, abs=1e-4)
    assert placement.wing_pts == pytest.approx(12.5, abs=1e-3)
    assert 0.0 <= placement.composite_score <= 100.0


def test_wing_console_weights_from_flags_match_defaults():
    flags = get_flags()
    w = WingConsoleWeights.from_flags(flags)
    # Default config mirrors the dataclass defaults.
    assert w.close == DEFAULT_WEIGHTS.close
    assert w.touch == DEFAULT_WEIGHTS.touch
    assert w.mae == DEFAULT_WEIGHTS.mae
    assert w.theta == DEFAULT_WEIGHTS.theta
    assert w.credit == DEFAULT_WEIGHTS.credit
