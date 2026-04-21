"""Engine 1 v2 — /api/breach/trade/{id}/live-review endpoint tests.

The endpoint drives the "Run Live Review" button on the Active Trades
panel. Unlike the legacy /checkin route (which expects a post-earnings
open price to compute gap + breach), this one gives the desk a
hold/cut narrative for an OPEN pre-earnings trade based on current
spot + short-strike distance + regime drift.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def _patch_trade_store(monkeypatch):
    """Drop in a minimal in-memory stand-in for the trade store so the
    test doesn't depend on Redis."""
    store = {}

    def _log(trade_data, *args, **kwargs):
        tid = "test-" + str(len(store) + 1)
        trade_data = dict(trade_data)
        trade_data["tradeId"] = tid
        trade_data["checkIns"] = []
        store[tid] = trade_data
        return tid

    def _get(tid, *args, **kwargs):
        return store.get(tid)

    def _add_checkin(tid, record, *args, **kwargs):
        if tid in store:
            store[tid].setdefault("checkIns", []).append(record)
            return True
        return False

    monkeypatch.setattr("backend.e1_earnings_trades.log_trade", _log)
    monkeypatch.setattr("backend.e1_earnings_trades.get_trade", _get)
    monkeypatch.setattr("backend.e1_earnings_trades.add_checkin", _add_checkin)
    # Router imports these lazily at runtime so nothing else is needed.


def _log_trade(client):
    body = {
        "source": "wing_console",
        "ticker": "NVDA",
        "entry": {
            "emMultiple":     1.5,
            "wingWidth":      5,
            "entryCredit":    1.85,
            "shortPutStrike": 140.0,
            "longPutStrike":  135.0,
            "shortCallStrike": 160.0,
            "longCallStrike": 165.0,
            "spotAtEntry":    150.0,
            "impliedMovePct": 6.5,
            "earningsDate":   "2026-05-28",
            "earningsTiming": "AMC",
        },
        "entryContext": {"vrpScore": 0.7, "regimeBucket": "MODERATE"},
        "advisorVerdict": {"verdict": None, "source": "wing_console"},
    }
    r = client.post("/api/breach/trade", json=body)
    assert r.status_code == 200, r.text
    return r.json()["tradeId"]


def test_live_review_returns_status_chip(client, monkeypatch):
    # Simplest path: pass currentSpot via body so we don't depend on
    # the ORATS live-price path (which is stubbed to None in tests).
    monkeypatch.setattr("backend.deps.get_client_optional", lambda: None)

    tid = _log_trade(client)
    r = client.post(
        f"/api/breach/trade/{tid}/live-review",
        json={"currentSpot": 141.0},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tradeId"] == tid
    review = body["review"]
    assert review["statusChip"] in (
        "on_track", "caution", "short_strike_challenged", "breached", "unknown",
    )
    # Spot at 141 vs short put 140 -> within 0.71% of the short -> challenged.
    assert review["currentSpot"] == 141.0
    assert review["statusChip"] in ("short_strike_challenged", "caution", "breached")


def test_live_review_404_on_missing_trade(client):
    r = client.post("/api/breach/trade/nope-999/live-review", json={})
    assert r.status_code == 404


def test_live_review_handles_missing_current_spot(client, monkeypatch):
    monkeypatch.setattr(
        "backend.technicals.fetch_live_price_context_optional",
        lambda **kw: None,
    )
    monkeypatch.setattr("backend.deps.get_client_optional", lambda: None)

    tid = _log_trade(client)
    r = client.post(f"/api/breach/trade/{tid}/live-review", json={})
    assert r.status_code == 200, r.text
    review = r.json()["review"]
    # With no spot we can't compute distances; status chip defaults to
    # "unknown" and the response is still well-formed.
    assert review["statusChip"] == "unknown"
    assert review["currentSpot"] == 0.0


def test_live_review_accepts_body_overrides(client, monkeypatch):
    monkeypatch.setattr(
        "backend.technicals.fetch_live_price_context_optional",
        lambda **kw: None,
    )
    monkeypatch.setattr("backend.deps.get_client_optional", lambda: None)

    tid = _log_trade(client)
    # Override spot via body — useful for backtest / paper-trading replay.
    r = client.post(
        f"/api/breach/trade/{tid}/live-review",
        json={"currentSpot": 150.5, "currentVix": 18.2, "notes": "paper check"},
    )
    assert r.status_code == 200, r.text
    review = r.json()["review"]
    assert review["currentSpot"] == 150.5
    assert review["currentVix"] == 18.2
    assert review["userNotes"] == "paper check"
    # Spot 150.5 with shorts 140/160 -> +6.27% below call, +6.9% above put -> on_track.
    assert review["statusChip"] == "on_track"
