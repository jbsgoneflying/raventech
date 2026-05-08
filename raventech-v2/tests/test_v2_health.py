"""Phase 0 smoke tests for the v2 FastAPI service.

These tests intentionally only cover the contract every downstream
piece of v2 (frontend, ship-and-verify, uptime monitors) depends on:

    1. /api/v2/health is public and returns ok=True
    2. /api/v2/version reports a sensible version + foundation flags
    3. The Phase 0 stub endpoints return shape, not 500s
    4. The auth gate doesn't accidentally block the public endpoints
"""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client() -> TestClient:
    # Force public mode so we don't need an invite cookie inside CI.
    os.environ["PUBLIC_ACCESS"] = "1"
    # Point auth-gate redirects at a stable host even if AUTH_SECRET is set elsewhere.
    os.environ.setdefault("AUTH_SECRET", "test-secret-not-real")
    from v2_app.main import app

    return TestClient(app)


def test_health_is_public_and_ok(client: TestClient) -> None:
    res = client.get("/api/v2/health")
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    assert body["service"] == "raven-tech-v2"
    assert "version" in body and body["version"]
    assert isinstance(body["ts"], int) and body["ts"] > 0


def test_version_reports_foundation_flags(client: TestClient) -> None:
    res = client.get("/api/v2/version")
    assert res.status_code == 200
    body = res.json()
    assert body["service"]
    assert body["version"]
    foundation = body["foundation"]
    expected = {
        "regime_encoder",
        "contrastive_analogues",
        "conformal_calibration",
        "path_generator",
        "learned_ranker",
        "agent_committee",
    }
    assert expected.issubset(foundation.keys())
    # Phase 0: all foundation modules disabled by default.
    assert all(v is False for v in foundation.values())


def test_regime_embed_returns_shape_in_phase0(client: TestClient) -> None:
    res = client.get("/api/v2/regime/embed")
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "phase0_stub"
    assert body["embedding_dim"] == 64
    assert body["expected_cluster_count"] == 6


def test_analogues_search_advertises_shape(client: TestClient) -> None:
    res = client.get("/api/v2/analogues/search", params={"ticker": "NVDA", "k": 80})
    assert res.status_code == 200
    body = res.json()
    assert body["status"] == "phase0_stub"
    assert body["query"]["ticker"] == "NVDA"
    assert body["query"]["k"] == 80
    assert body["query"]["cross_ticker"] is True
    assert body["embedding_space"]["dim"] == 128


def test_counterfactual_log_accepts_payload(client: TestClient) -> None:
    payload = {
        "engine": "e15",
        "v1_verdict": {"verdict": "GO", "confidence": 0.7},
        "v2_verdict": {"verdict": "PASS", "confidence": 0.55},
        "delta_summary": "v2 dissent on regime cluster",
    }
    res = client.post("/api/v2/counterfactual/log", json=payload)
    assert res.status_code == 200
    body = res.json()
    assert body["ok"] is True
    # stream_id may be None in local dev (no redis) — that's acceptable.
    assert "stream_id" in body
    assert "logged" in body


def test_landing_page_renders_v2_brand(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200
    html = res.text
    # The single most distinctive v2 marker — the wordmark + tag.
    assert "v2 · foundation brain" in html
    assert "Raven Tech" in html
    # Confirms the v2.css token system is wired.
    assert "/static/v2.css" in html


def test_engine_pages_share_one_template(client: TestClient) -> None:
    for slug in ("e1", "e2", "e14", "e15", "mi"):
        res = client.get(f"/{slug}")
        assert res.status_code == 200, f"{slug} returned {res.status_code}"
        assert "/static/engine.js" in res.text


def test_favicon_served(client: TestClient) -> None:
    res = client.get("/favicon.ico")
    # Either the file exists (200) or we degrade gracefully (204), never 500.
    assert res.status_code in (200, 204)
