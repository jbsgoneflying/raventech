"""Engine 1 — Tracked Trades + Trade Builder (post-WDC refactor).

Covers the new tracked/live ``mode`` split that replaced the Wing
Decision Console workflow on 2026-05-20:

1. ``log_trade`` defaults new docs to ``mode="tracked"`` and persists
   the field; legacy docs read back as ``"live"``.
2. ``promote_to_live`` flips a tracked active trade to live and stamps
   ``promotedAt`` (idempotent for live trades).
3. The list endpoint returns the ``mode`` field on every trade so the
   UI can bucket LIVE vs TRACKING.
4. ``POST /api/breach/trade/draft-price`` returns symmetric strikes and
   a credit estimate without re-hitting ORATS when the breach cache is
   warm.
5. ``POST /api/breach/trade/{id}/promote`` flips ``mode`` on the
   underlying doc and returns the new state.
6. ``POST /api/breach/trade/{id}/close`` accepts ``closeReason="cancelled_tracking"``
   (the "stop tracking" path from the UI) without complaint.
"""
from __future__ import annotations

from typing import Any, Dict, Optional

import pytest
from fastapi.testclient import TestClient


@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


# ---------------------------------------------------------------------------
# Direct-call coverage (no HTTP)
# ---------------------------------------------------------------------------

class _FakeStore:
    """Minimal in-memory stand-in for RedisStore covering the surface
    log_trade / get_trade / promote_to_live / close_trade use."""

    def __init__(self) -> None:
        self.kv: Dict[str, Any] = {}

    def set_json(self, key: str, value: Any, ttl_s: int = 0) -> None:
        self.kv[key] = value

    def get_json(self, key: str) -> Optional[Any]:
        return self.kv.get(key)

    def scan_keys(self, pattern: str) -> list:
        # Not used by the paths we exercise here.
        return []


def test_log_trade_defaults_to_tracked_mode():
    from backend import e1_earnings_trades as mod
    store = _FakeStore()
    tid = mod.log_trade({"ticker": "NVDA", "entry": {}}, store=store)
    assert tid is not None
    doc = mod.get_trade(tid, store=store)
    assert doc is not None
    assert doc["mode"] == "tracked"
    assert doc["status"] == "active"


def test_log_trade_honors_explicit_live_mode():
    from backend import e1_earnings_trades as mod
    store = _FakeStore()
    tid = mod.log_trade({"ticker": "NVDA", "entry": {}, "mode": "live"}, store=store)
    doc = mod.get_trade(tid, store=store)
    assert doc["mode"] == "live"


def test_log_trade_persists_history_breaker_snapshot_in_entry_context():
    from backend import e1_earnings_trades as mod
    store = _FakeStore()
    tid = mod.log_trade(
        {
            "ticker": "NVDA",
            "entry": {},
            "historyBreakerRisk": {"score": 71.0, "gate": "NO_TRADE", "level": "high"},
        },
        store=store,
    )
    doc = mod.get_trade(tid, store=store)
    ctx = doc.get("entryContext") or {}
    assert isinstance(ctx, dict)
    assert isinstance(ctx.get("historyBreakerRisk"), dict)
    assert ctx["historyBreakerRisk"]["gate"] == "NO_TRADE"


def test_get_trade_backfills_legacy_docs_as_live():
    """A doc that pre-dates the mode field should read back as live."""
    from backend import e1_earnings_trades as mod
    store = _FakeStore()
    store.set_json("e1:trades:legacy-1", {
        "tradeId": "legacy-1",
        "status": "active",
        "ticker": "AAPL",
        "entry": {},
        # NOTE: no `mode` field — emulates an existing on-disk doc.
    })
    doc = mod.get_trade("legacy-1", store=store)
    assert doc is not None
    assert doc["mode"] == "live"


def test_promote_to_live_flips_mode_and_stamps_time():
    from backend import e1_earnings_trades as mod
    store = _FakeStore()
    tid = mod.log_trade({"ticker": "MSFT", "entry": {}}, store=store)
    promoted = mod.promote_to_live(tid, store=store)
    assert promoted is not None
    assert promoted["mode"] == "live"
    assert promoted.get("promotedAt") is not None
    # Idempotent: a second promote on the same trade should return the
    # already-live doc without erroring.
    again = mod.promote_to_live(tid, store=store)
    assert again is not None
    assert again["mode"] == "live"


def test_promote_to_live_returns_none_for_missing_trade():
    from backend import e1_earnings_trades as mod
    store = _FakeStore()
    assert mod.promote_to_live("does-not-exist", store=store) is None


# ---------------------------------------------------------------------------
# HTTP coverage
# ---------------------------------------------------------------------------

@pytest.fixture
def _http_trade_store(monkeypatch):
    """Patch the e1_earnings_trades helpers used by the router so the
    HTTP layer drives an in-memory store and we don't need Redis."""
    store: Dict[str, Dict[str, Any]] = {}

    def _normalize_mode(t):
        raw = t.get("mode")
        if raw is None:
            return "live"
        return "tracked" if str(raw).lower().strip() == "tracked" else "live"

    def _log(trade_data, *args, **kwargs):
        tid = "tt-" + str(len(store) + 1)
        raw_mode = str(trade_data.get("mode", "tracked") or "tracked").lower().strip()
        mode = "live" if raw_mode == "live" else "tracked"
        doc = dict(trade_data)
        doc["tradeId"] = tid
        doc["status"] = "active"
        doc["mode"] = mode
        doc["checkIns"] = []
        doc.setdefault("ticker", "")
        store[tid] = doc
        return tid

    def _get(tid, *args, **kwargs):
        t = store.get(tid)
        if t is None:
            return None
        t["mode"] = _normalize_mode(t)
        return t

    def _list_active(*args, **kwargs):
        out = []
        for t in store.values():
            if t.get("status") in ("active", "monitoring"):
                t["mode"] = _normalize_mode(t)
                out.append(t)
        return out

    def _promote(tid, *args, **kwargs):
        t = store.get(tid)
        if t is None or t.get("status") != "active":
            return None
        t["mode"] = "live"
        t["promotedAt"] = "2026-05-20T00:00:00Z"
        return t

    def _close(tid, close_data=None, *args, **kwargs):
        t = store.get(tid)
        if t is None:
            return None
        t["status"] = "closed"
        t["closeReason"] = (close_data or {}).get("closeReason", "manual")
        return t

    monkeypatch.setattr("backend.e1_earnings_trades.log_trade", _log)
    monkeypatch.setattr("backend.e1_earnings_trades.get_trade", _get)
    monkeypatch.setattr("backend.e1_earnings_trades.list_active_trades", _list_active)
    monkeypatch.setattr("backend.e1_earnings_trades.promote_to_live", _promote)
    monkeypatch.setattr("backend.e1_earnings_trades.close_trade", _close)
    yield store


def _stub_breach_payload():
    return {
        "ticker": "NVDA",
        "current": {"stockPrice": 100.0, "impliedMovePct": 5.0},
        "nextEvent": {},
        "events": [],
        "historyBreakerRisk": {"score": 66.0, "level": "elevated", "gate": "CAUTION"},
    }


@pytest.fixture
def _stub_orats(monkeypatch):
    class _C: ...
    monkeypatch.setattr(
        "backend.routers.engine1_breach.compute_breach_stats",
        lambda **kw: _stub_breach_payload(),
    )
    monkeypatch.setattr("backend.routers.engine1_breach.get_client", lambda: _C())
    monkeypatch.setattr(
        "backend.routers.engine1_breach.get_benzinga_client_optional",
        lambda: None,
    )
    yield


def test_post_trade_defaults_mode_to_tracked_via_http(client, _http_trade_store):
    body = {"ticker": "NVDA", "entry": {"emMultiple": 1.25, "wingWidth": 5}}
    r = client.post("/api/breach/trade", json=body)
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["status"] == "active"
    assert j["mode"] == "tracked"


def test_post_trade_with_explicit_live_mode_via_http(client, _http_trade_store):
    body = {"ticker": "NVDA", "entry": {}, "mode": "live"}
    r = client.post("/api/breach/trade", json=body)
    assert r.status_code == 200
    assert r.json()["mode"] == "live"


def test_get_trades_segregates_mode_field(client, _http_trade_store):
    client.post("/api/breach/trade", json={"ticker": "NVDA", "entry": {}, "mode": "tracked"})
    client.post("/api/breach/trade", json={"ticker": "AAPL", "entry": {}, "mode": "live"})
    r = client.get("/api/breach/trades")
    assert r.status_code == 200
    trades = r.json()["trades"]
    by_mode = {t.get("ticker"): t.get("mode") for t in trades}
    assert by_mode.get("NVDA") == "tracked"
    assert by_mode.get("AAPL") == "live"


def test_promote_endpoint_flips_mode(client, _http_trade_store):
    log = client.post("/api/breach/trade", json={"ticker": "NVDA", "entry": {}}).json()
    tid = log["tradeId"]
    r = client.post(f"/api/breach/trade/{tid}/promote")
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["tradeId"] == tid
    assert j["mode"] == "live"
    assert j["promotedAt"] is not None


def test_promote_endpoint_404_for_missing_trade(client, _http_trade_store):
    r = client.post("/api/breach/trade/nope-999/promote")
    assert r.status_code == 404


def test_close_endpoint_accepts_cancelled_tracking_reason(client, _http_trade_store):
    log = client.post("/api/breach/trade", json={"ticker": "NVDA", "entry": {}}).json()
    tid = log["tradeId"]
    r = client.post(
        f"/api/breach/trade/{tid}/close",
        json={"closeReason": "cancelled_tracking", "notes": "stop tracking from UI"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "closed"
    assert body["closeReason"] == "cancelled_tracking"


def test_draft_price_returns_symmetric_strikes(client, _stub_orats):
    """draft-price should round to $0.50, place wings on both sides, and
    return a credit estimate even when the breach payload has no tradeBuilder."""
    r = client.post(
        "/api/breach/trade/draft-price",
        json={
            "ticker": "NVDA",
            "event_date": "2026-05-28",
            "event_timing": "AMC",
            "emMultiple": 1.2,
            "wingWidth": 5,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    # spot=100, em=5%, mult=1.2 -> short put = 94, short call = 106
    assert body["shortPutStrike"] == pytest.approx(94.0)
    assert body["shortCallStrike"] == pytest.approx(106.0)
    # wings 5 pts wider than each short.
    assert body["longPutStrike"] == pytest.approx(89.0)
    assert body["longCallStrike"] == pytest.approx(111.0)
    # Heuristic credit when tradeBuilder is absent: ~10% of wing.
    assert body["creditSource"] == "heuristic"
    assert body["estCredit"] == pytest.approx(0.50, abs=0.01)
    # Sanity: cushions are positive and roughly symmetric.
    assert body["breachDistPutPct"] > 0
    assert body["breachDistCallPct"] > 0
    assert isinstance(body.get("historyBreakerRisk"), dict)
    assert body["historyBreakerRisk"]["gate"] == "CAUTION"


def test_draft_price_rejects_out_of_range_em(client, _stub_orats):
    r = client.post(
        "/api/breach/trade/draft-price",
        json={
            "ticker": "NVDA",
            "event_date": "2026-05-28",
            "event_timing": "AMC",
            "emMultiple": 5.0,
            "wingWidth": 5,
        },
    )
    assert r.status_code == 400


def test_draft_price_requires_event_fields(client, _stub_orats):
    r = client.post(
        "/api/breach/trade/draft-price",
        json={"ticker": "NVDA", "emMultiple": 1.0, "wingWidth": 5},
    )
    assert r.status_code == 400
