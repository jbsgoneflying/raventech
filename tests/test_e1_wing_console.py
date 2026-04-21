"""Engine 1 v2 — Wing Console scoring engine tests."""
from __future__ import annotations

import pytest

from backend.engine1 import (
    DEFAULT_WEIGHTS,
    MAEDistribution,
    PlacementScore,
    WingConsoleWeights,
    build_wing_console,
    score_placements,
)


def _ev(signed, em, ctc=None):
    e = {"signedMovePct": signed, "impliedMovePct": em}
    if ctc is not None:
        e["ctcSignedMovePct"] = ctc
    return e


def _realistic_events(n=20):
    # Mix of breaching + non-breaching events (ratio ~0.6 mean)
    ratios = [0.3, 0.5, 0.7, 0.4, 0.6, 0.8, 1.2, 0.9, 0.5, 0.3,
              0.6, 1.4, 0.7, 0.4, 0.8, 0.5, 1.1, 0.6, 0.3, 0.7]
    return [_ev(r * 5.0, 5.0, ctc=r * 4.0) for r in ratios[:n]]


def test_score_placements_returns_full_grid_ranked():
    events = _realistic_events(20)
    placements, theta = score_placements(
        ticker="NVDA", spot=100.0, implied_move_pct=5.0, events=events,
    )
    # Default grid: 5 EM × 3 wings = 15
    assert len(placements) == 15

    # Ranked descending by composite
    scores = [p.composite_score for p in placements]
    assert scores == sorted(scores, reverse=True)

    # Top placement has reasonable composite
    assert placements[0].composite_score > 40.0


def test_score_placements_respects_custom_weights():
    events = _realistic_events(10)
    mae = MAEDistribution(n=10, p50=3.0, p75=5.0, p90=8.0, p95=10.0, max=12.0, source="daily_ohlc_proxy")
    base, _ = score_placements(
        ticker="AAPL", spot=200.0, implied_move_pct=4.0, events=events, mae=mae,
    )
    heavier_gap = WingConsoleWeights(gap=0.9, ctc=0.03, mae=0.03, theta=0.02, credit=0.02)
    reweighted, _ = score_placements(
        ticker="AAPL", spot=200.0, implied_move_pct=4.0, events=events, mae=mae,
        weights=heavier_gap,
    )
    assert len(base) == len(reweighted) == 15
    # Different weights → either different ranking or different top composite.
    base_sig = [(p.em_mult, p.wing_pts, p.composite_score) for p in base[:3]]
    reweight_sig = [(p.em_mult, p.wing_pts, p.composite_score) for p in reweighted[:3]]
    assert base_sig != reweight_sig


def test_score_placements_grid_override():
    events = _realistic_events(6)
    placements, _ = score_placements(
        ticker="TSLA", spot=250.0, implied_move_pct=6.0, events=events,
        em_mults=[1.0, 2.0], wing_pts=[5.0],
    )
    assert len(placements) == 2  # 2 × 1


def test_score_placements_guards_invalid_spot_or_im():
    placements, r = score_placements(
        ticker="X", spot=0.0, implied_move_pct=5.0, events=_realistic_events(5),
    )
    assert placements == []


def test_score_placements_composite_is_deterministic():
    events = _realistic_events(12)
    run1, _ = score_placements(ticker="X", spot=150, implied_move_pct=5, events=events)
    run2, _ = score_placements(ticker="X", spot=150, implied_move_pct=5, events=events)
    assert [(p.em_mult, p.wing_pts, p.composite_score) for p in run1] == \
           [(p.em_mult, p.wing_pts, p.composite_score) for p in run2]


def test_placement_score_has_strike_prices():
    events = _realistic_events(15)
    placements, _ = score_placements(
        ticker="META", spot=400.0, implied_move_pct=6.0, events=events,
    )
    p = placements[0]
    assert p.short_put_strike is not None and p.short_put_strike < 400.0
    assert p.short_call_strike is not None and p.short_call_strike > 400.0
    assert p.long_put_strike < p.short_put_strike
    assert p.long_call_strike > p.short_call_strike


def test_build_wing_console_with_full_payload():
    events = _realistic_events(15)
    payload = {
        "ticker": "AMZN",
        "current": {"stockPrice": 180.0, "impliedMovePct": 5.5},
        "nextEvent": {"impliedMovePctPlanned": 5.5, "earnDateNext": "2026-05-01"},
        "events": events,
        "tradeBuilder": {"totalCredit": 1.2},
    }
    console = build_wing_console(
        ticker="AMZN", event_date="2026-05-01", event_timing="AMC",
        payload=payload,
    )
    assert console.ticker == "AMZN"
    assert console.event_date == "2026-05-01"
    assert console.event_timing == "AMC"
    assert len(console.placements) == 15
    assert "gap" in console.weights_used
    assert console.cache_key  # non-empty


def test_build_wing_console_suppresses_when_spot_missing():
    payload = {
        "ticker": "X",
        "current": {"stockPrice": None, "impliedMovePct": 5.0},
        "nextEvent": {},
        "events": _realistic_events(5),
        "tradeBuilder": {},
    }
    console = build_wing_console(
        ticker="X", event_date="2026-05-01", event_timing="BMO",
        payload=payload,
    )
    assert console.placements == []
    assert any("spot" in w for w in console.warnings)


def test_build_wing_console_includes_mae():
    events = _realistic_events(15)
    mae = MAEDistribution(n=10, p50=3.0, p75=5.0, p90=8.0, p95=10.0, max=12.0, source="daily_ohlc_proxy")
    payload = {
        "ticker": "CRM",
        "current": {"stockPrice": 250.0, "impliedMovePct": 5.0},
        "nextEvent": {},
        "events": events,
        "tradeBuilder": {},
    }
    console = build_wing_console(
        ticker="CRM", event_date="2026-05-01", event_timing="AMC",
        payload=payload, mae_distribution=mae,
    )
    assert console.mae.get("n") == 10
    # MAE at tight placements should produce a non-zero mae_p95_pct
    first = console.placements[0]
    assert isinstance(first.mae_p95_pct, float)


def test_wing_console_weights_from_flags_defaults_match_defaults():
    class _Flags: pass
    flags = _Flags()
    w = WingConsoleWeights.from_flags(flags)
    assert w.gap == DEFAULT_WEIGHTS.gap
    assert w.ctc == DEFAULT_WEIGHTS.ctc
    assert w.mae == DEFAULT_WEIGHTS.mae
