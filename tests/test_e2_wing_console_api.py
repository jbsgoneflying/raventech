"""Engine 2 v2 — /api/spx-ic/wing-console HTTP tests.

Exercises the router validation + response shape without hitting ORATS —
the compute_engine2_spx_ic path is monkeypatched to return a synthetic
scan payload carrying the v2 `weeks` pool + `current.regimeMiV2`.
"""
from __future__ import annotations

import random
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


def _stub_scan():
    rng = random.Random(11)
    weeks = []
    for i in range(40):
        daily = [rng.gauss(0, 0.008) for _ in range(5)]
        weeks.append({
            "entryDate":     f"2025-{((i % 11) + 1):02d}-0{(i % 7) + 1}",
            "expiryDate":    f"2025-{((i % 11) + 1):02d}-0{(i % 7) + 5}",
            "entryPx":       5000.0,
            "expiryPx":      5000.0 * (1 + sum(daily)),
            "signedMovePct": round(sum(daily) * 100, 3),
            "dailyReturns":  daily,
            "regimeBucket":  "LOW",
            "bucket":        "LOW",
            "macroBucket":   "NORMAL",
            "mb":            "NORMAL",
            "season":        "ALL",
            "regime_bucket": "LOW",
            "macro_bucket":  "NORMAL",
            "signed_move_pct": round(sum(daily) * 100, 3),
        })
    return {
        "asOfDate": "2026-04-21",
        "underlying": {"symbol": "SPX", "isProxy": False, "notes": []},
        "current": {
            "stockPrice": 5000.0,
            "regime":     {"label": "MODERATE", "bucket": "MODERATE"},
            "macro":      {"bucket": "NORMAL", "multiplier": 1.0},
            "regimeMiV2": {"label": "Risk-On", "probabilities": {"Risk-On": 0.65, "Transitional": 0.25, "Stressed": 0.10}, "vol_state": "stable", "source": "v2_hmm"},
            "vwap": None,
        },
        "regime":       {"label": "MODERATE", "bucket": "MODERATE", "mi_v2": None},
        "expectedMove": {"expectedMovePct": 1.5, "dte": 5, "smartSpotPrice": 5000.0},
        "params":       {"emMults": [1.0, 1.5, 2.0], "wingWidthPts": [5, 10, 15]},
        "weeks":        weeks,
        "riskGrid":     {"cells": [], "count": 0},
        "historyBreakerRisk": {
            "score": 42.0,
            "level": "elevated",
            "gate": "CAUTION",
            "drivers": ["Recent moves are hotter than baseline."],
        },
    }


@pytest.fixture(autouse=True)
def _patch_engine(monkeypatch):
    def _stub(**kw):
        return _stub_scan()
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.compute_engine2_spx_ic",
        _stub,
    )
    class _D: ...
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_client",
        lambda: _D(),
    )
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_benzinga_client_optional",
        lambda: None,
    )
    # Make sure both v2 + legacy flags are on.
    from backend.config import get_flags
    f = get_flags()
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_flags",
        lambda: replace(f, ENABLE_E2_V2=True, ENABLE_ENGINE2_SPX_IC=True),
    )


def test_wing_console_happy_path(client):
    r = client.post("/api/spx-ic/wing-console", json={
        "underlying": "SPX", "entry_day": "mon", "seasonality_mode": "none",
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wingConsole"]["underlying"] == "SPX"
    assert body["wingConsole"]["entry_day"] == "mon"
    assert len(body["wingConsole"]["placements"]) > 0
    assert "weightsUsed" in body
    # Full ranking: first placement has the highest composite
    placements = body["wingConsole"]["placements"]
    scores = [p["composite_score"] for p in placements]
    assert scores == sorted(scores, reverse=True)
    # MC results populated
    assert body["mcResults"]["n_sims"] > 0
    # MI v2 is carried end-to-end
    assert body["regime"]["mi_v2"]["label"] == "Risk-On"
    assert body["historyBreakerRisk"]["level"] == "elevated"


def test_wing_console_rejects_bad_underlying(client):
    r = client.post("/api/spx-ic/wing-console", json={
        "underlying": "IWM", "entry_day": "mon",
    })
    assert r.status_code == 400


def test_wing_console_rejects_bad_entry_day(client):
    r = client.post("/api/spx-ic/wing-console", json={
        "underlying": "SPX", "entry_day": "fri",
    })
    assert r.status_code == 400


def test_wing_console_404_when_e2_v2_disabled(client, monkeypatch):
    from backend.config import get_flags
    f = get_flags()
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_flags",
        lambda: replace(f, ENABLE_E2_V2=False, ENABLE_ENGINE2_SPX_IC=True),
    )
    r = client.post("/api/spx-ic/wing-console", json={"underlying": "SPX", "entry_day": "mon"})
    assert r.status_code == 404


def test_wing_console_custom_weights_flow_through(client):
    r = client.post("/api/spx-ic/wing-console", json={
        "underlying": "SPX", "entry_day": "mon",
        "weights": {"close": 0.9, "touch": 0.01, "mae": 0.03, "theta": 0.03, "credit": 0.03},
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["weightsUsed"]["close"] == 0.9
    assert body["weightsUsed"]["touch"] == 0.01
