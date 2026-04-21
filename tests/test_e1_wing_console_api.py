"""Engine 1 v2 — /api/breach/wing-console HTTP tests.

These exercise the router validation logic without hitting ORATS — the
compute_breach_stats path is monkeypatched to return a synthetic payload.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


def _stub_payload():
    """Synthetic breach-stats payload with just enough for the scorer."""
    events = []
    # Mix of breaching + non-breaching (ratio ≈ 0.7 mean)
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
        "summary": {"events_used": 15, "events_found": 15, "breaches": 4},
        "baseline": {},
        "regime": {"label": "Normal"},
        "goNoGo": {"checks": []},
        "params": {"n": 20, "years": 5, "k": 1.0},
    }


def _stub_breach_stats(**kwargs):
    return _stub_payload()


@pytest.fixture(autouse=True)
def _patch_compute_breach_stats(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.engine1_breach.compute_breach_stats",
        _stub_breach_stats,
    )
    # And also the orats client dependency
    class _DummyClient: ...
    monkeypatch.setattr(
        "backend.routers.engine1_breach.get_client",
        lambda: _DummyClient(),
    )
    monkeypatch.setattr(
        "backend.routers.engine1_breach.get_benzinga_client_optional",
        lambda: None,
    )


def test_wing_console_happy_path(client):
    r = client.post("/api/breach/wing-console", json={
        "ticker": "NVDA",
        "event_date": "2026-05-28",
        "event_timing": "AMC",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ticker"] == "NVDA"
    assert body["event_date"] == "2026-05-28"
    assert body["event_timing"] == "AMC"
    assert isinstance(body.get("placements"), list)
    assert len(body["placements"]) >= 5
    # Sorted descending by composite
    scores = [p["composite_score"] for p in body["placements"]]
    assert scores == sorted(scores, reverse=True)
    # Top placement should produce a respectable composite
    assert body["placements"][0]["composite_score"] > 30.0
    # Weights echoed back
    assert "gap" in body["weights_used"]


def test_wing_console_requires_ticker(client):
    r = client.post("/api/breach/wing-console", json={})
    assert r.status_code == 400
    assert "ticker" in r.text.lower()


def test_wing_console_requires_event_date_and_timing(client):
    r = client.post("/api/breach/wing-console", json={"ticker": "NVDA"})
    assert r.status_code == 400
    body = r.json()
    assert "event_date" in body["detail"] and "event_timing" in body["detail"]


def test_wing_console_rejects_bad_event_timing(client):
    r = client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "PRE",
    })
    assert r.status_code == 400


def test_wing_console_rejects_bad_event_date(client):
    r = client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "not-a-date", "event_timing": "AMC",
    })
    assert r.status_code == 400


def test_wing_console_respects_kill_switch(client, monkeypatch):
    from backend.config import get_flags
    original_flags = get_flags()
    # Flip ENABLE_E1_V2 off via a monkeypatched get_flags proxy.
    import backend.routers.engine1_breach as e1_router
    class _F: pass
    for k, v in vars(original_flags).items():
        setattr(_F, k, v)
    _F.ENABLE_E1_V2 = False
    monkeypatch.setattr(e1_router, "get_flags", lambda: _F)

    r = client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
    })
    assert r.status_code == 404


def test_wing_console_accepts_custom_grid(client):
    r = client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "em_mults": [1.0, 2.0],
        "wing_pts":  [5.0, 10.0],
    })
    assert r.status_code == 200
    body = r.json()
    assert len(body["placements"]) == 4


def test_wing_console_accepts_custom_weights(client):
    r = client.post("/api/breach/wing-console", json={
        "ticker": "NVDA", "event_date": "2026-05-28", "event_timing": "AMC",
        "weights": {"gap": 0.9, "ctc": 0.0, "mae": 0.05, "theta": 0.03, "credit": 0.02},
    })
    assert r.status_code == 200
    body = r.json()
    assert body["weights_used"]["gap"] == 0.9
