"""Engine 1 v2 — Monte Carlo always-on tests.

In v2 the ``?mc=1`` query param is accepted-but-ignored at the router
level. MC fires every call whenever ``ENABLE_MONTE_CARLO_EARNINGS`` is
True (the default), and is only disabled when the kill-switch is flipped
in config. ``mc_simulator.run_monte_carlo_for_placement`` is the new
wrapper that scores MC per-wing-placement for the Wing Console.
"""
from __future__ import annotations

import pytest

from backend.config import get_flags


def test_monte_carlo_default_is_on():
    f = get_flags()
    assert f.ENABLE_MONTE_CARLO_EARNINGS is True, \
        "v2: MC kill-switch should default to ON"


def test_mc_query_param_is_accepted_but_ignored(monkeypatch):
    """Router accepts mc= param for back-compat but no longer toggles the flag."""
    from fastapi.testclient import TestClient
    from backend.app import app
    client = TestClient(app)

    # Monkeypatch the heavy backends
    captured = {}
    def fake_compute_breach_stats(**kw):
        captured["flags_override"] = kw.get("flags_override")
        return {
            "ticker": "NVDA",
            "current": {"stockPrice": 100.0, "impliedMovePct": 5.0, "asOfDate": "2026-04-21"},
            "nextEvent": {"earnDateNext": "2026-05-28", "timingPlanned": "AMC",
                          "impliedMovePctPlanned": 5.0, "override_source": "user_override"},
            "events": [], "tradeBuilder": {}, "summary": {}, "baseline": {},
            "goNoGo": {"checks": []}, "regime": {"label": "Normal"},
            "monteCarlo": {"nSims": 0, "notes": ["stub"]},
            "params": {"n": 20, "years": 5, "k": 1.0},
        }
    class _DummyClient: ...
    monkeypatch.setattr("backend.routers.engine1_breach.compute_breach_stats", fake_compute_breach_stats)
    monkeypatch.setattr("backend.routers.engine1_breach.get_client", lambda: _DummyClient())
    monkeypatch.setattr("backend.routers.engine1_breach.get_benzinga_client_optional", lambda: None)
    monkeypatch.setattr("backend.routers.engine1_breach.compute_current_snapshot", lambda **kw: {"stockPrice": 100.0})
    monkeypatch.setattr("backend.routers.engine1_breach.compute_go_no_go", lambda *a, **kw: {"checks": []})

    # Sending mc=0 must NOT flip the flag to False.
    r = client.get(
        "/api/breach?ticker=NVDA&n=20&years=5&k=1.0"
        "&mc=0&event_date=2026-05-28&event_timing=AMC"
    )
    assert r.status_code == 200, r.text
    flags_used = captured.get("flags_override")
    assert flags_used is not None
    # The accepted-but-ignored contract: mc=0 does NOT set MC off at router.
    assert getattr(flags_used, "ENABLE_MONTE_CARLO_EARNINGS") is True


def _pool_events(n=12):
    out = []
    for i, r in enumerate([0.2, 0.4, 0.6, 0.8, 1.0, 1.2, 1.4, 0.3, 0.5, 0.7, 0.9, 1.1][:n]):
        out.append({
            "earnDate":       f"2024-{(i % 12) + 1:02d}-15",
            "signedMovePct":  r * 5.0 * (1 if i % 2 == 0 else -1),
            "impliedMovePct": 5.0,
            "pricingDateUsed": f"2024-{(i % 12) + 1:02d}-14",
            "regimeAtEvent": {"label": "Normal", "tradeGate": "OK"},
        })
    return out


def test_run_monte_carlo_for_placement_wires_strikes():
    """The new per-placement adapter forwards strikes into the existing MC cache."""
    from backend.mc_simulator import run_monte_carlo_for_placement

    out = run_monte_carlo_for_placement(
        ticker="NVDA",
        params={"n": 20, "years": 5, "k": 1.0},
        flags=get_flags(),
        current={"stockPrice": 100.0, "asOfDate": "2026-04-21"},
        next_event={"impliedMovePctPlanned": 5.0, "earnDateNext": "2026-05-28"},
        regime={"label": "Normal"},
        events=_pool_events(),
        placement={
            "short_put_strike":  92.5,
            "long_put_strike":   82.5,
            "short_call_strike": 107.5,
            "long_call_strike":  117.5,
            "credit_est":        0.5,
        },
    )
    assert "nSims" in out
    assert out.get("nSims", 0) > 0


def test_run_monte_carlo_for_placement_guards_missing_strikes():
    from backend.mc_simulator import run_monte_carlo_for_placement
    out = run_monte_carlo_for_placement(
        ticker="NVDA",
        params={},
        flags=get_flags(),
        current={"stockPrice": 100.0},
        next_event={"impliedMovePctPlanned": 5.0},
        regime={},
        events=[{"signedMovePct": 1, "impliedMovePct": 5.0}],
        placement={},
    )
    assert out.get("nSims", 0) == 0


def test_mc_per_placement_cache_reuses_structure_key():
    """Calling the per-placement adapter twice with identical strikes returns
    the same cached result (no re-simulation).
    """
    from backend.mc_simulator import run_monte_carlo_for_placement

    events = _pool_events()
    placement = {
        "short_put_strike":  93.0, "long_put_strike":  83.0,
        "short_call_strike": 107.0, "long_call_strike": 117.0,
    }
    common = dict(
        ticker="NVDA",
        params={"n": 20, "years": 5, "k": 1.0},
        flags=get_flags(),
        current={"stockPrice": 100.0, "asOfDate": "2026-04-22"},
        next_event={"impliedMovePctPlanned": 5.0, "earnDateNext": "2026-05-28"},
        regime={"label": "Normal"},
        events=events,
        placement=placement,
    )
    out1 = run_monte_carlo_for_placement(**common)
    out2 = run_monte_carlo_for_placement(**common)
    # Same seed + same structure → identical output
    assert out1 == out2
