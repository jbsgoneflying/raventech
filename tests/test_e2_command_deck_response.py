"""Engine 2 v2 — /api/spx-ic primary response shape (weeks + MI v2 overlay)."""
from __future__ import annotations

import random
from dataclasses import replace

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


def _stub_compute(**kw):
    from backend.spx_ic.engine import compute_engine2_spx_ic  # noqa: F401
    # Re-implement a minimal payload: verdict fields present so we can
    # verify the v2 strip behaviour from the engine layer.
    return {
        "enabled": True, "asOfDate": "2026-04-21",
        "params":   {"entryDay": "mon", "years": 2, "widths": [1.0, 1.5], "emMults": [1.0, 1.5], "wingWidthPts": [10], "seasonalityMode": "none", "deskLocked": True, "multiWing": True},
        "underlying": {"symbol": "SPX", "isProxy": False, "notes": []},
        "current":    {"regime": {"label": "LOW", "bucket": "LOW"}, "macro": {"bucket": "NORMAL"}, "vwap": None, "regimeMiV2": {"label": "Risk-On", "probabilities": {"Risk-On": 0.7}, "vol_state": "stable", "source": "v2_hmm"}},
        "regime":     {"label": "LOW", "bucket": "LOW", "mi_v2": {"label": "Risk-On", "probabilities": {"Risk-On": 0.7}, "vol_state": "stable", "source": "v2_hmm"}},
        "expectedMove": {"expectedMovePct": 1.5, "dte": 5, "smartSpotPrice": 5000.0},
        "strikeTargets": {}, "liveContext": {},
        "oddsLikeNow": {"regimeBucket": "LOW", "macroBucket": "NORMAL", "seasonBucket": "ALL", "weeksUsed": 10, "byWidth": {}, "notes": []},
        "backtest":   {}, "recommendation": {},
        "riskGrid":   {"cells": [], "count": 0},
        "macroEffects": {},
        "widthComparison": [],
        "emBreachSummary": {},
        "technicals": {},
        "telemetry":  {"timingsMs": {}, "counts": {}},
        "notes":      [],
        "weeks":      [
            {"entryDate": "2025-01-06", "expiryDate": "2025-01-10",
             "entryPx": 5000.0, "expiryPx": 5040.0,
             "signedMovePct": 0.80, "signed_move_pct": 0.80,
             "regimeBucket": "LOW", "macroBucket": "NORMAL"}
        ],
    }


@pytest.fixture(autouse=True)
def _patch(monkeypatch):
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.compute_engine2_spx_ic",
        _stub_compute,
    )
    class _D: ...
    monkeypatch.setattr("backend.routers.engine2_spx_ic.get_client", lambda: _D())
    monkeypatch.setattr("backend.routers.engine2_spx_ic.get_benzinga_client_optional", lambda: None)
    monkeypatch.setattr("backend.routers.engine2_spx_ic.is_us_equity_market_open", lambda: False)
    # Empty cache between tests.
    from backend.deps import spx_ic_cache
    spx_ic_cache.clear()


def test_spx_ic_response_carries_mi_v2_overlay(client, monkeypatch):
    from backend.config import get_flags
    f = get_flags()
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_flags",
        lambda: replace(f, ENABLE_ENGINE2_SPX_IC=True),
    )
    r = client.get("/api/spx-ic?underlying=SPX&entry_day=mon&years=2&widths=1.0,1.5&weeks_limit=10")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"]["regimeMiV2"]["label"] == "Risk-On"
    assert body["regime"]["mi_v2"]["label"] == "Risk-On"
    assert isinstance(body.get("weeks"), list)
    assert body["weeksPage"]["returned"] == 1
    assert body["schemaVersion"] == 3


def test_spx_ic_weeks_pagination_respects_limit(client, monkeypatch):
    from backend.config import get_flags
    f = get_flags()
    monkeypatch.setattr(
        "backend.routers.engine2_spx_ic.get_flags",
        lambda: replace(f, ENABLE_ENGINE2_SPX_IC=True),
    )
    r = client.get("/api/spx-ic?underlying=SPX&entry_day=mon&weeks_limit=0")
    assert r.status_code == 200
    body = r.json()
    assert body["weeks"] == []
    assert body["weeksPage"]["returned"] == 0
    assert body["weeksPage"]["total"] >= 1
