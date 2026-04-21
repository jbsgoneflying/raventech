"""HTTP-level tests for the Market Intelligence v2 router endpoints."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


def test_health_endpoint_returns_metadata(client):
    r = client.get("/api/market-intel/health")
    assert r.status_code == 200
    body = r.json()
    assert body["enabled"] is True
    assert body.get("model_version") == "mi_hmm_v1"
    assert "feature_keys" in body
    assert len(body["feature_keys"]) == 8


def test_regime_endpoint_returns_snapshot(client):
    r = client.get("/api/market-intel/regime")
    assert r.status_code == 200
    body = r.json()
    assert "as_of" in body
    assert "probs" in body
    assert "label" in body
    assert "confidence" in body
    assert "transition_risk_1d" in body
    assert "data_quality" in body
    # probs always sum to ~1
    if body["probs"]:
        assert sum(body["probs"].values()) == pytest.approx(1.0, abs=1e-3)


def test_regime_force_refresh_works(client):
    r = client.get("/api/market-intel/regime?force_refresh=true")
    assert r.status_code == 200


def test_calibrate_endpoint_requires_admin(client):
    r = client.post("/api/market-intel/calibrate", json={})
    assert r.status_code == 401


def test_calibrate_endpoint_with_token(client, monkeypatch):
    monkeypatch.setenv("MI_ADMIN_TOKEN", "test-secret")
    r = client.post(
        "/api/market-intel/calibrate",
        json={"persist": False, "lookback_days": 100},
        headers={"X-Admin-Token": "test-secret"},
    )
    # In the test env we have no EODHD client → graceful failure expected;
    # but the endpoint itself should accept the request and return a report.
    assert r.status_code == 200
    body = r.json()
    assert "started_at" in body
    assert "finished_at" in body


def test_calibrate_rejects_bad_token(client, monkeypatch):
    monkeypatch.setenv("MI_ADMIN_TOKEN", "test-secret")
    r = client.post(
        "/api/market-intel/calibrate",
        json={},
        headers={"X-Admin-Token": "wrong"},
    )
    assert r.status_code == 401


def test_diff_endpoint_returns_panel(client):
    r = client.get("/api/market-intel/diff")
    assert r.status_code == 200
    body = r.json()
    # Even with no DMS history, endpoint returns a valid skeleton.
    assert "headline_summary" in body or "has_changes" in body
