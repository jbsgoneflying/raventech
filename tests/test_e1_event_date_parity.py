"""Engine 1 v2 — event_date + event_timing parity + cache-key inclusion tests."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from backend.deps import breach_cache_key


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


def _stub_payload():
    return {
        "ticker": "NVDA",
        "current": {"stockPrice": 100.0, "impliedMovePct": 5.0, "asOfDate": "2026-04-21"},
        "nextEvent": {"earnDateNext": "2026-05-28", "timingPlanned": "AMC",
                      "impliedMovePctPlanned": 5.0, "override_source": "user_override"},
        "events": [{"signedMovePct": 2.0, "impliedMovePct": 5.0}] * 10,
        "tradeBuilder": {},
        "summary": {}, "baseline": {}, "goNoGo": {"checks": []},
        "params": {"n": 20, "years": 5, "k": 1.0},
    }


def _stub_breach_stats(**kwargs):
    return _stub_payload()


@pytest.fixture(autouse=True)
def _patch_client_and_compute(monkeypatch):
    class _DummyClient: ...
    monkeypatch.setattr("backend.routers.engine1_breach.compute_breach_stats", _stub_breach_stats)
    monkeypatch.setattr("backend.routers.engine1_breach.get_client", lambda: _DummyClient())
    monkeypatch.setattr("backend.routers.engine1_breach.get_benzinga_client_optional", lambda: None)
    monkeypatch.setattr("backend.routers.engine1_breach.compute_current_snapshot", lambda **kw: _stub_payload()["current"])
    monkeypatch.setattr("backend.routers.engine1_breach.compute_go_no_go", lambda *a, **kw: {"checks": []})


def test_breach_cache_key_separates_by_event_date():
    base = ("NVDA", 20, 5, 1.0, tuple())
    k1 = breach_cache_key("NVDA", 20, 5, 1.0, tuple(), event_date="2026-05-28", event_timing="AMC")
    k2 = breach_cache_key("NVDA", 20, 5, 1.0, tuple(), event_date="2026-08-28", event_timing="AMC")
    assert k1 != k2
    # And separate by timing
    k3 = breach_cache_key("NVDA", 20, 5, 1.0, tuple(), event_date="2026-05-28", event_timing="BMO")
    assert k1 != k3


def test_breach_cache_key_backwards_compat_no_event_fields():
    # Legacy callers that don't pass event_date/event_timing still get a tuple
    # (two empty-string slots appended).
    k = breach_cache_key("NVDA", 20, 5, 1.0, tuple())
    assert k[-2:] == ("", "")


def test_breach_endpoint_requires_event_date_in_v2(client, monkeypatch):
    # With E1_V2 + E1_REQUIRE_EVENT_DATE both True, /api/breach returns 400
    # when event_date/event_timing are missing.
    r = client.get("/api/breach?ticker=NVDA&n=20&years=5&k=1.0")
    assert r.status_code == 400
    assert "event_date" in r.text.lower()


def test_breach_endpoint_accepts_event_date_and_timing(client):
    r = client.get(
        "/api/breach?ticker=NVDA&n=20&years=5&k=1.0"
        "&event_date=2026-05-28&event_timing=AMC"
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["nextEvent"]["earnDateNext"] == "2026-05-28"


def test_breach_endpoint_accepts_legacy_mc_event_aliases(client):
    r = client.get(
        "/api/breach?ticker=NVDA&n=20&years=5&k=1.0"
        "&mc_event_date=2026-05-28&mc_event_timing=BMO"
    )
    assert r.status_code == 200


def test_breach_endpoint_rejects_bad_event_timing(client):
    r = client.get(
        "/api/breach?ticker=NVDA&n=20&years=5&k=1.0"
        "&event_date=2026-05-28&event_timing=PRE"
    )
    assert r.status_code == 400


def test_breach_advisor_requires_event_date(client):
    r = client.post("/api/breach/advisor", json={"ticker": "NVDA"})
    assert r.status_code == 400
    assert "event_date" in r.text.lower()


def test_next_event_override_source_chip_in_payload(client):
    # The nextEvent.override_source field is present and maps to user_override
    # when the caller supplies a manual override.
    r = client.get(
        "/api/breach?ticker=NVDA&n=20&years=5&k=1.0"
        "&event_date=2026-05-28&event_timing=AMC"
    )
    assert r.status_code == 200
    ne = r.json().get("nextEvent") or {}
    assert ne.get("override_source") == "user_override"
