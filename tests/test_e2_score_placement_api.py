"""Engine 2 v2 — /api/spx-ic/wing-console/score-placement HTTP tests."""
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
    rng = random.Random(17)
    weeks = []
    for i in range(40):
        daily = [rng.gauss(0, 0.008) for _ in range(5)]
        weeks.append({
            "entryDate":      f"2025-0{(i % 9) + 1}-0{(i % 7) + 1}",
            "expiryDate":     f"2025-0{(i % 9) + 1}-0{(i % 7) + 5}",
            "entryPx":        5000.0,
            "expiryPx":       5000.0 * (1 + sum(daily)),
            "signedMovePct":  round(sum(daily) * 100, 3),
            "dailyReturns":   daily,
            "regimeBucket":   "LOW",
            "bucket":         "LOW",
            "macroBucket":    "NORMAL",
            "mb":             "NORMAL",
            "season":         "ALL",
            "regime_bucket":  "LOW",
            "macro_bucket":   "NORMAL",
            "signed_move_pct": round(sum(daily) * 100, 3),
        })
    return {
        "asOfDate": "2026-04-21",
        "underlying": {"symbol": "SPX"},
        "current":    {"stockPrice": 5000.0, "regime": {"label": "MODERATE", "bucket": "MODERATE"}, "macro": {"bucket": "NORMAL"}, "regimeMiV2": None},
        "regime":     {"label": "MODERATE", "mi_v2": None},
        "expectedMove": {"expectedMovePct": 1.5, "dte": 5, "smartSpotPrice": 5000.0},
        "weeks":      weeks,
    }


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.compute_engine2_spx_ic",
        lambda **kw: _stub_scan(),
    )
    class _D: ...
    monkeypatch.setattr("backend.routers.engine2_spx_ic.get_client", lambda: _D())
    monkeypatch.setattr("backend.routers.engine2_spx_ic.get_benzinga_client_optional", lambda: None)
    from backend.config import get_flags
    f = get_flags()
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_flags",
        lambda: replace(f, ENABLE_E2_V2=True, ENABLE_ENGINE2_SPX_IC=True),
    )
    # Force context cache clean between tests
    from backend.engine2.scoring_context import clear_scoring_cache
    clear_scoring_cache()


def test_score_placement_cold_start_builds_context(client):
    r = client.post("/api/spx-ic/wing-console/score-placement", json={
        "underlying": "SPX", "entry_day": "mon",
        "em_mult": 1.35, "wing_pts": 12.5,
    })
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["context_source"] in ("rebuilt_context", "cached_context")
    placement = body["placement"]
    assert abs(placement["em_mult"] - 1.35) < 1e-3
    assert abs(placement["wing_pts"] - 12.5) < 1e-3
    assert 0.0 <= placement["composite_score"] <= 100.0


def test_score_placement_uses_cache_after_warmup(client):
    # 1. Warm up the context via the primary Wing Console route.
    r = client.post("/api/spx-ic/wing-console", json={"underlying": "SPX", "entry_day": "mon"})
    assert r.status_code == 200, r.text
    as_of_date = r.json()["wingConsole"]["as_of_date"]

    # 2. Now score-placement with a matching as_of_date hits the cache.
    r2 = client.post("/api/spx-ic/wing-console/score-placement", json={
        "underlying": "SPX", "entry_day": "mon", "as_of_date": as_of_date,
        "em_mult": 1.4, "wing_pts": 12,
    })
    assert r2.status_code == 200, r2.text
    assert r2.json()["context_source"] == "cached_context"


def test_score_placement_validation_rejects_out_of_range(client):
    r = client.post("/api/spx-ic/wing-console/score-placement", json={
        "underlying": "SPX", "entry_day": "mon", "em_mult": 5.0, "wing_pts": 10.0,
    })
    assert r.status_code == 400
    r2 = client.post("/api/spx-ic/wing-console/score-placement", json={
        "underlying": "SPX", "entry_day": "mon", "em_mult": 1.5, "wing_pts": 0.1,
    })
    assert r2.status_code == 400


def test_score_placement_requires_numeric_inputs(client):
    r = client.post("/api/spx-ic/wing-console/score-placement", json={
        "underlying": "SPX", "entry_day": "mon", "em_mult": "abc", "wing_pts": 10,
    })
    assert r.status_code == 400
