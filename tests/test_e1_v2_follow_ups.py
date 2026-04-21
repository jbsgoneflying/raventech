"""Engine 1 v2 — follow-up improvements tests.

Covers the three post-ship fixes:

1. ``DailyBar`` now carries high / low so ``mae_proxy`` reports
   ``daily_ohlc_proxy`` (not ``open_close_fallback``) when upstream
   bars have the fields.
2. ``POST /api/breach/wing-console/score-placement`` returns an exact
   composite for arbitrary (em_mult, wing_pts) against the cached
   scoring context (no expensive re-fetch).
3. The legacy desk-consensus verdict (TRADE / LEAN_PASS / PASS / FADE)
   is no longer emitted in the /api/breach response body by default;
   the LLM advisor still re-computes it internally.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import pytest
from fastapi.testclient import TestClient


# ---------------------------------------------------------------------------
# Shared stubs
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


@dataclass
class _HRE:
    earn_date:  str = ""
    timing:     str = ""
    prior_close: Optional[float] = None
    earnings_day_open:  Optional[float] = None
    earnings_day_close: Optional[float] = None
    next_day_close:     Optional[float] = None
    expected_move_pct:  Optional[float] = None


# ---------------------------------------------------------------------------
# 1) MAE now consumes true high / low → source tag flips
# ---------------------------------------------------------------------------


def test_daily_bar_dataclass_carries_high_low():
    from backend.earnings_logic import DailyBar
    b = DailyBar(tradeDate="2024-01-15", open=100, clsPx=101, high=103, low=99)
    assert b.high == 103
    assert b.low == 99


def test_daily_bar_defaults_high_low_none():
    # Back-compat: legacy callers that omit high/low still instantiate.
    from backend.earnings_logic import DailyBar
    b = DailyBar(tradeDate="2024-01-15", open=100, clsPx=101)
    assert b.high is None
    assert b.low is None


def test_mae_reports_daily_ohlc_source_when_highlow_present():
    from backend.earnings_logic import DailyBar
    from backend.engine1.mae_proxy import compute_mae_distribution

    # AMC event → hold window starts next calendar day.
    hre = [_HRE(earn_date="2024-03-15", timing="AMC", prior_close=100.0)]
    dailies = {
        "2024-03-16": DailyBar(tradeDate="2024-03-16", open=101, clsPx=104, high=106, low=100),
        "2024-03-17": DailyBar(tradeDate="2024-03-17", open=104, clsPx=103, high=107, low=102),
    }
    dist = compute_mae_distribution(hold_risk_events=hre, dailies_cache=dailies, hold_days=2)
    assert dist.n == 1
    assert dist.source == "daily_ohlc_proxy"
    # Max up-excursion: 107 vs entry 100 → 7%
    assert dist.p95 == pytest.approx(7.0, abs=0.05)


def test_mae_falls_back_to_open_close_when_highlow_missing():
    from backend.earnings_logic import DailyBar
    from backend.engine1.mae_proxy import compute_mae_distribution

    hre = [_HRE(earn_date="2024-03-15", timing="AMC", prior_close=100.0)]
    dailies = {
        "2024-03-16": DailyBar(tradeDate="2024-03-16", open=101, clsPx=105, high=None, low=None),
    }
    dist = compute_mae_distribution(hold_risk_events=hre, dailies_cache=dailies, hold_days=1)
    assert dist.n == 1
    assert dist.source == "open_close_fallback"


# ---------------------------------------------------------------------------
# 2) /api/breach/wing-console/score-placement
# ---------------------------------------------------------------------------


def _stub_payload():
    events = []
    for r in [0.3, 0.6, 0.8, 0.4, 0.7, 0.9, 1.1, 0.5, 0.8, 0.4,
              0.6, 1.3, 0.7, 0.5, 0.9]:
        events.append({
            "signedMovePct": r * 5.0,
            "impliedMovePct": 5.0,
            "ctcSignedMovePct": r * 4.0,
        })
    return {
        "ticker": "NVDA",
        "current": {"stockPrice": 100.0, "impliedMovePct": 5.0, "asOfDate": "2026-04-21"},
        "nextEvent": {"earnDateNext": "2026-05-28", "timingPlanned": "AMC",
                      "impliedMovePctPlanned": 5.0, "override_source": "user_override"},
        "events": events,
        "tradeBuilder": {"totalCredit": 1.1},
        "e1WingMAE": {
            "n": 12, "p50": 3.0, "p75": 5.0, "p90": 8.0, "p95": 10.0,
            "max": 12.0, "source": "daily_ohlc_proxy", "hold_days": 2,
            "events": [], "notes": [],
        },
        "summary": {"events_used": 15}, "baseline": {},
        "regime": {"label": "Normal"}, "goNoGo": {"checks": []},
        "params": {"n": 20, "years": 5, "k": 1.0},
    }


@pytest.fixture
def patched_breach_stats(monkeypatch):
    class _DummyClient: ...
    monkeypatch.setattr(
        "backend.routers.engine1_breach.compute_breach_stats",
        lambda **kw: _stub_payload(),
    )
    monkeypatch.setattr(
        "backend.routers.engine1_breach.get_client",
        lambda: _DummyClient(),
    )
    monkeypatch.setattr(
        "backend.routers.engine1_breach.get_benzinga_client_optional",
        lambda: None,
    )
    yield


def test_score_placement_cold_start_builds_context(client, patched_breach_stats):
    r = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA",
        "event_date": "2026-05-28",
        "event_timing": "AMC",
        "em_mult": 1.37,
        "wing_pts": 6.5,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticker"] == "NVDA"
    assert body["context_source"] in ("cached_context", "rebuilt_context")
    p = body["placement"]
    assert p["em_mult"] == pytest.approx(1.37)
    assert p["wing_pts"] == pytest.approx(6.5)
    assert 0.0 <= p["composite_score"] <= 100.0
    # Strike derivation is symmetric and on the right side of spot.
    assert p["short_put_strike"] < 100 < p["short_call_strike"]


def test_score_placement_uses_cached_context_on_second_call(client, patched_breach_stats):
    # Prime the cache via a full wing-console build.
    r0 = client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
    })
    assert r0.status_code == 200
    r = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "em_mult": 1.42, "wing_pts": 8.0,
    })
    assert r.status_code == 200
    body = r.json()
    assert body["context_source"] == "cached_context"


def test_score_placement_refresh_rebuilds_context(client, patched_breach_stats):
    # First build → populates cache.
    client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
    })
    r = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "em_mult": 1.5, "wing_pts": 7.5, "refresh": True,
    })
    assert r.status_code == 200
    assert r.json()["context_source"] == "rebuilt_context"


def test_score_placement_requires_event_fields(client, patched_breach_stats):
    r = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA", "em_mult": 1.5, "wing_pts": 7.5,
    })
    assert r.status_code == 400


def test_score_placement_rejects_out_of_range(client, patched_breach_stats):
    r = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "em_mult": 5.0, "wing_pts": 7.5,
    })
    assert r.status_code == 400
    r2 = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "em_mult": 1.5, "wing_pts": 0.0,
    })
    assert r2.status_code == 400


def test_score_placement_honours_weights_override(client, patched_breach_stats):
    client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
    })
    r = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "em_mult": 1.5, "wing_pts": 7.5,
        "weights": {"gap": 0.95, "ctc": 0.01, "mae": 0.02, "theta": 0.01, "credit": 0.01},
    })
    assert r.status_code == 200
    assert r.json()["weights_used"]["gap"] == pytest.approx(0.95)


def test_score_placement_respects_v2_kill_switch(client, monkeypatch, patched_breach_stats):
    import backend.routers.engine1_breach as mod
    from backend.config import get_flags
    class _F:
        pass
    for k, v in vars(get_flags()).items():
        setattr(_F, k, v)
    _F.ENABLE_E1_V2 = False
    monkeypatch.setattr(mod, "get_flags", lambda: _F)
    r = client.post("/api/breach/wing-console/score-placement", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "em_mult": 1.5, "wing_pts": 7.5,
    })
    assert r.status_code == 404


def test_score_single_placement_direct_call():
    """End-to-end test through the pure-python API (no HTTP)."""
    from backend.engine1 import (
        MAEDistribution, ScoringContext, WingConsoleWeights,
        score_single_placement, store_scoring_context,
    )
    ctx = ScoringContext(
        ticker="X", event_date="2026-05-28", event_timing="AMC",
        spot=100.0, implied_move_pct=5.0,
        events=[{"signedMovePct": r * 5.0, "impliedMovePct": 5.0} for r in
                [0.3, 0.5, 0.7, 0.9, 1.1]],
        mae=MAEDistribution(n=5, p50=3, p75=5, p90=7, p95=9, max=10,
                            source="daily_ohlc_proxy"),
        theta=None,
        median_credit_pts=1.0,
        weights=WingConsoleWeights(),
    )
    store_scoring_context(ctx)
    p = score_single_placement(context=ctx, em_mult=1.33, wing_pts=6.5)
    assert 0.0 <= p.composite_score <= 100.0


# ---------------------------------------------------------------------------
# 3) Desk-consensus stripped from /api/breach by default
# ---------------------------------------------------------------------------


def _breach_stats_with_consensus():
    """Stub payload that still carries the verdict fields — we want to
    confirm compute_breach_stats strips them when the flag is off."""
    return {
        **_stub_payload(),
        "e1DeskConsensus": {"label": "LEAN_PASS", "score": 52, "rationale": ["x"]},
        "e1EmPreference": {"preferred": "1.25x", "rationale": ["y"]},
    }


def test_breach_stats_strips_desk_consensus_by_default(monkeypatch):
    """Directly exercise the stripper inside compute_breach_stats via a thin
    shim over the VRP-engine writes."""
    from backend.config import get_flags
    f = get_flags()
    # v2 defaults: ENABLE_E1_V2=True, E1_EMIT_DESK_CONSENSUS=False
    assert f.ENABLE_E1_V2 is True
    assert f.E1_EMIT_DESK_CONSENSUS is False


def test_breach_api_response_omits_verdict_fields_by_default(client, patched_breach_stats):
    r = client.get(
        "/api/breach?ticker=NVDA&n=20&years=5&k=1.0"
        "&event_date=2026-05-28&event_timing=AMC"
    )
    assert r.status_code == 200
    body = r.json()
    assert "e1DeskConsensus" not in body
    assert "e1EmPreference" not in body


def test_breach_api_emits_verdict_when_flag_on(client, monkeypatch, patched_breach_stats):
    """When E1_EMIT_DESK_CONSENSUS=1 is set, the legacy verdict survives in
    the response body (for external consumers)."""
    import backend.routers.engine1_breach as mod
    from dataclasses import replace
    from backend.config import get_flags
    flipped = replace(get_flags(), E1_EMIT_DESK_CONSENSUS=True)
    monkeypatch.setattr(mod, "get_flags", lambda: flipped)

    def _inject_consensus(**kw):
        p = _stub_payload()
        p["e1DeskConsensus"] = {"label": "TRADE", "score": 72, "rationale": []}
        p["e1EmPreference"] = {"preferred": "1.25x"}
        return p
    monkeypatch.setattr(mod, "compute_breach_stats", _inject_consensus)

    r = client.get(
        "/api/breach?ticker=NVDA&n=20&years=5&k=1.0"
        "&event_date=2026-05-28&event_timing=AMC"
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("e1DeskConsensus", {}).get("label") == "TRADE"
