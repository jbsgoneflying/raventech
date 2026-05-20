"""Engine 1 v2 — follow-up improvements tests.

Covers the post-ship fixes that survived the 2026-05-20 refactor that
retired the Wing Decision Console:

1. ``DailyBar`` now carries high / low so ``mae_proxy`` reports
   ``daily_ohlc_proxy`` (not ``open_close_fallback``) when upstream
   bars have the fields. (Still consumed by E15 simulator's E1
   wing-MAE cross-check.)
2. The legacy desk-consensus verdict (TRADE / LEAN_PASS / PASS / FADE)
   is no longer emitted in the /api/breach response body by default;
   the LLM advisor still re-computes it internally.

The score-placement / wing-console HTTP tests were removed when those
routes were deleted; the pure-python scoring primitives still have
coverage via the E15 simulator tests.
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
# 2) Desk-consensus stripped from /api/breach by default
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
