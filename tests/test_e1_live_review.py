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


def test_score_action_ladder_nudges_hold_when_history_breaker_is_high():
    from backend.e1_live_review import _score_action_ladder

    fields = {"emMultiple": 1.5}
    evidence = {
        "spot": {"nearestShortPct": 6.0, "putDistPct": 7.0, "callDistPct": 8.0},
        "statusChip": "on_track",
        "replay": {"p10PnlPct": 2.0, "p50PnlPct": 45.0, "p90PnlPct": 80.0, "fullCollectRate": 0.85},
        "analogues": {"rateAtEmPct": 10.0},
        "news": {"counts": {"high": 0}},
        "historyBreaker": {"score": 78.0, "gate": "NO_TRADE", "level": "high"},
    }
    _ladder, pre, conf = _score_action_ladder(fields=fields, evidence=evidence, phase="pre_event")
    assert pre == "ADJUST"
    assert conf >= 0.66


def test_live_review_includes_history_breaker_evidence(client, monkeypatch):
    monkeypatch.setattr("backend.deps.get_client_optional", lambda: None)
    monkeypatch.setattr(
        "backend.e1_live_review._layer_analogues",
        lambda ticker, fields: {
            "available": True,
            "ladder": {"1.0x": 10.0, "1.5x": 5.0, "2.0x": 2.0},
            "rateAtEmPct": 10.0,
            "historyBreaker": {"score": 61.0, "gate": "CAUTION", "level": "elevated", "drivers": ["Test driver"]},
        },
    )

    tid = _log_trade(client)
    r = client.post(
        f"/api/breach/trade/{tid}/live-review",
        json={"currentSpot": 150.5, "force_refresh": True},
    )
    assert r.status_code == 200, r.text
    review = r.json()["review"]
    hb = (review.get("evidence") or {}).get("historyBreaker")
    assert isinstance(hb, dict)
    assert hb.get("gate") == "CAUTION"


# ---------------------------------------------------------------------------
# _summarize_replay_for_review — projection-tile fields surfaced to the FE
# ---------------------------------------------------------------------------

def _scenario_payload_fixture():
    """A minimal but realistic scenario_payload dict shaped the way
    ``run_earnings_scenario`` returns it, just enough to exercise the
    new summary fields."""
    return {
        "eventsUsed": 18,
        "expectedValue": {
            "meanPnlPct": 32.0,
            "medianPnlPct": 45.0,
            "sharpeProxy": 0.82,
        },
        "outcomeDistribution": {
            "earlyTarget": {"pct": 33.3, "n": 6, "avgDays": 1.5, "avgPnlPct": 75.0},
            "fullCollect": {"pct": 27.8, "n": 5, "avgDays": 4.0, "avgPnlPct": 95.0},
            "whiteKnuckle": {"pct": 11.1, "n": 2, "avgDays": 3.5, "avgPnlPct": 60.0},
            "stopOut":     {"pct": 16.7, "n": 3, "avgDays": 2.0, "avgPnlPct": -80.0},
            "breach":      {"pct": 11.1, "n": 2, "avgDays": 1.0, "avgPnlPct": -150.0},
        },
        "mtmTimeline": [
            {"date": "2026-05-21", "p10": -20.0, "p50": 5.0,  "p90": 30.0},
            {"date": "2026-05-22", "p10": -35.0, "p50": 18.0, "p90": 55.0},
            {"date": "2026-05-23", "p10": -55.0, "p50": 32.0, "p90": 78.0},
        ],
        "matchedEvents": [
            {"mae": 5.0}, {"mae": 9.0}, {"mae": 7.0}, {"mae": 14.0}, {"mae": 11.0},
        ],
        "exitRulesOptimization": {
            "recommendedProfitTarget": 50.0,
            "recommendedStopLoss": -100.0,
            "recommendedTimeStopDays": 3,
        },
        "creditRichness": {"verdict": "rich"},
    }


def test_summarize_replay_surfaces_new_projection_fields():
    from backend.engine15.simulator import _summarize_replay_for_review

    out = _summarize_replay_for_review(_scenario_payload_fixture())

    # New rate buckets (returned as fractions, not percent).
    assert out["earlyExitRate"] == pytest.approx(0.333, abs=0.001)
    assert out["whiteKnuckleRate"] == pytest.approx(0.111, abs=0.001)
    assert out["stopOutRate"] == pytest.approx(0.167, abs=0.001)
    assert out["breachRate"] == pytest.approx(0.111, abs=0.001)
    assert out["fullCollectRate"] == pytest.approx(0.611, abs=0.002)
    # Days-to-early-exit forwarded from the earlyTarget bucket.
    assert out["daysToEarlyExit"] == pytest.approx(1.5)
    # MAE p50 computed from matchedEvents (median of 5,7,9,11,14 -> 9.0).
    assert out["medianMaePct"] == pytest.approx(9.0)
    # Exit-rule recommendation forwarded as a compact dict.
    assert out["exitRulesRec"] == {
        "profitTarget": 50.0,
        "stopLoss": -100.0,
        "timeStopDays": 3,
    }


def test_summarize_replay_is_tolerant_of_missing_fields():
    from backend.engine15.simulator import _summarize_replay_for_review

    out = _summarize_replay_for_review({})
    assert out["available"] is True
    assert out["earlyExitRate"] is None
    assert out["whiteKnuckleRate"] is None
    assert out["medianMaePct"] is None
    assert out["exitRulesRec"] is None
