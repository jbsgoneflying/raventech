"""HTTP-level tests for the Desk Insight router.

Uses FastAPI's TestClient to hit the live routes without a running server.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


def test_engines_endpoint(client):
    r = client.get("/api/desk-insight/engines")
    assert r.status_code == 200
    body = r.json()
    engines = body.get("engines") or []
    ids = {e["id"] for e in engines}
    for eid in ["market-intel", "e1", "e14", "e15", "calendar", "compare"]:
        assert eid in ids, f"missing engine {eid} in /engines response"
    # Every engine row must have name + description.
    for e in engines:
        assert e.get("name")
        assert e.get("card_count", 0) > 0


def test_catalog_union_endpoint(client):
    r = client.get("/api/desk-insight/catalog")
    assert r.status_code == 200
    body = r.json()
    engines = body.get("engines") or {}
    assert "e14" in engines
    assert "outcome_distribution" in engines["e14"]


def test_catalog_per_engine_endpoint(client):
    r = client.get("/api/desk-insight/catalog/e14")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] == "e14"
    assert "cards" in body
    assert "outcome_distribution" in body["cards"]
    assert body["cards"]["outcome_distribution"].get("title")
    assert isinstance(body["cards"]["outcome_distribution"].get("related_cards"), list)


def test_catalog_unknown_engine_returns_404(client):
    r = client.get("/api/desk-insight/catalog/does-not-exist")
    assert r.status_code == 404


def test_generate_rejects_missing_engine(client):
    r = client.post("/api/desk-insight", json={"cardType": "outcome_distribution"})
    assert r.status_code == 400
    assert "engine" in r.json().get("detail", "").lower()


def test_generate_rejects_missing_cardtype(client):
    r = client.post("/api/desk-insight", json={"engine": "e14"})
    assert r.status_code == 400


def test_generate_rejects_unknown_engine(client):
    r = client.post(
        "/api/desk-insight",
        json={"engine": "bogus", "cardType": "foo"},
    )
    assert r.status_code == 400


def test_generate_rejects_unknown_cardtype(client):
    r = client.post(
        "/api/desk-insight",
        json={"engine": "e14", "cardType": "bogus_slug"},
    )
    assert r.status_code == 400


def test_generate_happy_path_returns_9_fields(client):
    r = client.post(
        "/api/desk-insight",
        json={
            "engine": "e14",
            "cardType": "outcome_distribution",
            "cardData": {
                "distribution": {"fullCollect": {"pct": 60, "n": 12}},
                "eventsUsed": 20,
            },
            "scenarioContext": {"ticker": "SPX"},
        },
    )
    assert r.status_code == 200
    body = r.json()
    for key in [
        "what_this_shows", "how_to_read_it", "quant_mechanics",
        "how_to_use_it", "example_scenario", "watch_for",
        "common_mistakes", "desk_takeaway", "related_cards",
    ]:
        assert key in body, f"missing {key} in response"
    assert body.get("_engine") == "e14"
    assert body.get("_card_type") == "outcome_distribution"


def test_legacy_e14_shim(client):
    """The legacy /api/ic-scenario/explain-card URL still works."""
    r = client.post(
        "/api/ic-scenario/explain-card",
        json={"cardType": "entry_state", "cardData": {}},
    )
    assert r.status_code == 200
    body = r.json()
    for key in ["what_this_shows", "desk_takeaway"]:
        assert body.get(key)


def test_legacy_e15_shim(client):
    """The legacy /api/earnings-ic/explain-card URL still works."""
    r = client.post(
        "/api/earnings-ic/explain-card",
        json={"cardType": "adjusted_distribution", "cardData": {}},
    )
    assert r.status_code == 200
    body = r.json()
    for key in ["what_this_shows", "desk_takeaway"]:
        assert body.get(key)


def test_legacy_e14_catalog_shim(client):
    r = client.get("/api/ic-scenario/explain-card/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "outcome_distribution" in body.get("cardTypes", [])


def test_legacy_e15_catalog_shim(client):
    r = client.get("/api/earnings-ic/explain-card/catalog")
    assert r.status_code == 200
    body = r.json()
    assert "adjusted_distribution" in body.get("cardTypes", [])
