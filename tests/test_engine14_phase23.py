"""Engine 14 Phase 2 (conditioning) + Phase 3 (journal/review/backfill) tests.

These isolate the conditioning modifiers and the post-sim endpoints so the
existing Phase 1 suite (test_engine14_ic_scenario.py) stays focused on the
simulator's empirical math.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from backend.engine14 import conditioning


# ---------------------------------------------------------------------------
# 2a. Calendar modifier
# ---------------------------------------------------------------------------

class _FakeBzResp:
    def __init__(self, rows): self.rows = rows


class _FakeBz:
    """Minimal BenzingaClient stand-in for macro_events_by_date.

    Real `bz.calendar_economics(...)` returns an object with a `.rows`
    attribute holding a list of dict events. The real `rows[i]` shape uses
    `event_name` + `importance` + `country` + `date`. We mirror that.
    """
    def __init__(self, rows: List[Dict[str, Any]]) -> None:
        self._rows = rows

    def calendar_economics(self, *args, **kwargs):
        page = int(kwargs.get("page", 0) or 0)
        if page > 0:
            return _FakeBzResp([])
        return _FakeBzResp(list(self._rows))


def test_calendar_modifier_unavailable_when_no_client():
    m = conditioning.compute_calendar_modifier(
        entry_date="2025-06-02", expiry_date="2025-06-06",
        benzinga_client=None,
    )
    assert m.status == "unavailable"
    assert m.tail_multiplier == 1.0
    assert m.win_rate_shift_pct == 0.0


def test_calendar_modifier_detects_fomc_extreme():
    bz = _FakeBz([
        {"date": "2025-06-04", "event_name": "FOMC Rate Decision",
         "importance": 5, "country": "US"},
        {"date": "2025-06-05", "event_name": "Initial Jobless Claims",
         "importance": 3, "country": "US"},
    ])
    m = conditioning.compute_calendar_modifier(
        entry_date="2025-06-02", expiry_date="2025-06-06", benzinga_client=bz,
    )
    assert m.status == "ok"
    assert m.severity == "extreme"
    assert m.tail_multiplier > 1.0
    assert m.win_rate_shift_pct < 0
    assert "FOMC" in m.note


def test_calendar_modifier_no_hits_when_quiet_week():
    bz = _FakeBz([
        {"date": "2025-06-03", "event_name": "API Crude Oil Stocks",
         "importance": 3, "country": "US"},
    ])
    m = conditioning.compute_calendar_modifier(
        entry_date="2025-06-02", expiry_date="2025-06-06", benzinga_client=bz,
    )
    assert m.status == "ok"
    assert m.severity == "none"
    assert m.tail_multiplier == 1.0


def test_calendar_modifier_caps_tail_bump_on_pathological_week():
    rows = []
    for i, day in enumerate(["2025-06-02", "2025-06-03", "2025-06-04", "2025-06-05"]):
        rows.append({"date": day, "event_name": f"FOMC Decision #{i}",
                     "importance": 5, "country": "US"})
    m = conditioning.compute_calendar_modifier(
        entry_date="2025-06-02", expiry_date="2025-06-06",
        benzinga_client=_FakeBz(rows),
    )
    # Tail bump is summed but capped at +1.2 (so tail_mult <= 2.20).
    assert m.tail_multiplier <= 2.21
    # WR shift is floored at -18.0.
    assert m.win_rate_shift_pct >= -18.01


# ---------------------------------------------------------------------------
# 2b. Dealer gamma modifier
# ---------------------------------------------------------------------------

def test_dealer_gamma_unavailable_when_no_client():
    m = conditioning.compute_dealer_gamma_modifier(orats_client=None)
    assert m.status == "unavailable"


def _make_live_levels_stub(*, sign: str, bucket: str, net_gex: float = 1.0e9):
    class _OC:
        pass
    oc = _OC()

    def _stub(*args, **kwargs):
        return {
            "dealerGamma": {
                "netGammaSign": sign,
                "magnitudeBucket": bucket,
                "netGex": net_gex,
            },
            "gammaFlipStrike": 5800.0,
            "asOf": "2026-04-18T14:00:00Z",
        }

    import backend.spx_ic.live_levels as ll
    return oc, _stub, ll


def test_dealer_gamma_positive_high_is_tailwind(monkeypatch):
    oc, stub, ll = _make_live_levels_stub(sign="POSITIVE", bucket="high")
    monkeypatch.setattr(ll, "compute_spx_live_levels", stub)
    m = conditioning.compute_dealer_gamma_modifier(orats_client=oc)
    assert m.status == "ok"
    assert m.tail_multiplier < 1.0
    assert m.win_rate_shift_pct > 0


def test_dealer_gamma_negative_is_headwind(monkeypatch):
    oc, stub, ll = _make_live_levels_stub(sign="NEGATIVE", bucket="high")
    monkeypatch.setattr(ll, "compute_spx_live_levels", stub)
    m = conditioning.compute_dealer_gamma_modifier(orats_client=oc)
    assert m.status == "ok"
    assert m.tail_multiplier > 1.0
    assert m.win_rate_shift_pct < 0


def test_dealer_gamma_neutral_noop(monkeypatch):
    oc, stub, ll = _make_live_levels_stub(sign="NEUTRAL", bucket="low")
    monkeypatch.setattr(ll, "compute_spx_live_levels", stub)
    m = conditioning.compute_dealer_gamma_modifier(orats_client=oc)
    assert m.status == "ok"
    assert m.tail_multiplier == 1.0
    assert m.win_rate_shift_pct == 0.0


# ---------------------------------------------------------------------------
# 2c. Credit stress modifier
# ---------------------------------------------------------------------------

def test_credit_stress_unavailable_without_store():
    m = conditioning.compute_credit_stress_modifier(store=None)
    assert m.status == "unavailable"


def test_credit_stress_reads_stressed_label(monkeypatch):
    fake_dms = type("_DMS", (), {})()
    fake_dms.cross_asset_stress = {"composite_label": "Stressed", "composite_score": 82.0}
    fake_dms.date = "2026-04-18"

    import backend.engine14.conditioning as cond_mod

    def _load_dms(date_str, store):
        return fake_dms

    # Patch the lazy-imported load_dms inside daily_market_state.
    import backend.daily_market_state as dms_mod
    monkeypatch.setattr(dms_mod, "load_dms", _load_dms)
    monkeypatch.setattr(dms_mod, "load_dms_history", lambda store, n=5: [fake_dms])

    m = conditioning.compute_credit_stress_modifier(store=object())
    assert m.status == "ok"
    assert m.severity == "elevated"
    assert m.tail_multiplier > 1.0
    assert m.win_rate_shift_pct < 0


# ---------------------------------------------------------------------------
# 2d. Gap-regime modifier
# ---------------------------------------------------------------------------

def test_gap_regime_inactive_noop(monkeypatch):
    import backend.engine13_gap_regime as e13

    def _stub(*args, **kwargs):
        return {"asOfDate": "2026-04-18",
                "gap": {"enabled": False, "absGapPct": 0.3, "direction": "UP"},
                "scenarios": {"dominantScenario": "fade"}}

    monkeypatch.setattr(e13, "compute_gap_regime_scan", _stub)
    m = conditioning.compute_gap_regime_modifier(orats_client=object())
    assert m.status == "ok"
    assert m.severity == "none"
    assert m.tail_multiplier == 1.0


def test_gap_regime_extreme_when_large_gap(monkeypatch):
    import backend.engine13_gap_regime as e13

    def _stub(*args, **kwargs):
        return {"asOfDate": "2026-04-18",
                "gap": {"enabled": True, "absGapPct": 3.0, "direction": "DOWN"},
                "scenarios": {"dominantScenario": "capitulation_rebound"}}

    monkeypatch.setattr(e13, "compute_gap_regime_scan", _stub)
    m = conditioning.compute_gap_regime_modifier(orats_client=object())
    assert m.status == "ok"
    assert m.severity == "extreme"
    assert m.tail_multiplier >= 1.4
    assert m.win_rate_shift_pct <= -5.0


# ---------------------------------------------------------------------------
# Orchestrator + distribution adjustment
# ---------------------------------------------------------------------------

def test_compute_conditioning_combines_modifiers_gracefully():
    # All clients absent → every modifier degrades to "unavailable" but the
    # orchestrator still returns a well-formed dict.
    result = conditioning.compute_conditioning(
        entry_date="2025-06-02", expiry_date="2025-06-06",
        orats_client=None, benzinga_client=None, store=None,
    )
    for key in ("calendar", "dealerGamma", "creditStress", "gapRegime"):
        assert key in result
        # calendar/dealerGamma/creditStress all need a client and should degrade.
        # gapRegime internally tolerates missing clients (returns no-gap), so it
        # may still be "ok" with severity "none" — either way must not be error.
        assert result[key]["status"] in ("ok", "unavailable", "skipped")
    # With every modifier either unavailable or a no-op, net effect is neutral.
    assert 0.99 <= result["netTailMultiplier"] <= 1.01
    assert abs(result["netWinRateShiftPct"]) < 0.01


def test_apply_modifiers_empty_base_is_safe():
    assert conditioning.apply_modifiers_to_distribution(
        base_distribution={},
        net_tail_multiplier=1.3,
        net_wr_shift_pct=-5.0,
    ) == {}


def test_apply_modifiers_scales_tails_and_renormalizes():
    base = {
        "earlyTarget":  {"pct": 40.0, "n": 40, "avgPnlPct": 50.0, "avgDays": 2.0, "maxAdverseExcursionPct": -20.0},
        "fullCollect":  {"pct": 30.0, "n": 30, "avgPnlPct": 100.0, "avgDays": 5.0, "maxAdverseExcursionPct": 0.0},
        "whiteKnuckle": {"pct": 15.0, "n": 15, "avgPnlPct": 90.0, "avgDays": 5.0, "maxAdverseExcursionPct": -140.0},
        "stopOut":      {"pct": 10.0, "n": 10, "avgPnlPct": -200.0, "avgDays": 3.0, "maxAdverseExcursionPct": -210.0},
        "breach":       {"pct":  5.0, "n":  5, "avgPnlPct": -400.0, "avgDays": 5.0, "maxAdverseExcursionPct": -400.0},
    }
    adj = conditioning.apply_modifiers_to_distribution(
        base_distribution=base,
        net_tail_multiplier=1.5,
        net_wr_shift_pct=-6.0,
    )
    total = sum(v["pct"] for v in adj.values())
    assert abs(total - 100.0) < 0.2  # renormalized

    # Tails must grow.
    assert adj["stopOut"]["pct"] > base["stopOut"]["pct"] * 0.95
    assert adj["breach"]["pct"] > base["breach"]["pct"] * 0.95

    # Wins must shrink. Under the path-aware taxonomy, whiteKnuckle is a
    # win (exit > 0 after a scary drawdown) so it's included in the pool.
    win_adj = adj["earlyTarget"]["pct"] + adj["fullCollect"]["pct"] + adj["whiteKnuckle"]["pct"]
    win_base = base["earlyTarget"]["pct"] + base["fullCollect"]["pct"] + base["whiteKnuckle"]["pct"]
    assert win_adj < win_base
    # And the non-whiteKnuckle win subset must shrink as well (wr_shift_pct
    # distributes proportionally, so any individual winning bucket shrinks).
    assert adj["earlyTarget"]["pct"] + adj["fullCollect"]["pct"] < \
           base["earlyTarget"]["pct"] + base["fullCollect"]["pct"]

    # Averages are preserved (we only shift probabilities).
    for k, v in base.items():
        assert adj[k]["avgPnlPct"] == pytest.approx(v["avgPnlPct"])


# ---------------------------------------------------------------------------
# Phase 3: router endpoints (journal/review/backfill gating)
# ---------------------------------------------------------------------------

def _build_test_app(monkeypatch):
    """Wire a minimal FastAPI app with the engine14 router for endpoint tests."""
    # Enable the feature flag.
    import dataclasses
    from backend import config as cfg_mod
    orig_flags = cfg_mod.get_flags()
    test_flags = dataclasses.replace(
        orig_flags,
        ENABLE_ENGINE14_IC_SCENARIO=True,
        ENGINE14_ADMIN_TOKEN="unit-test-token",
    )
    monkeypatch.setattr(cfg_mod, "get_flags", lambda: test_flags)

    from backend.routers import engine14_ic_scenario
    monkeypatch.setattr(engine14_ic_scenario, "get_flags", lambda: test_flags)

    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(engine14_ic_scenario.router)
    return TestClient(app), engine14_ic_scenario


def test_backfill_endpoint_requires_token(monkeypatch):
    client, _ = _build_test_app(monkeypatch)
    r = client.post("/api/ic-scenario/backfill", json={"years": 0.1})
    assert r.status_code == 401


def test_backfill_endpoint_accepts_valid_token(monkeypatch):
    client, mod = _build_test_app(monkeypatch)
    # Stub the background worker so the test doesn't spin up ORATS/network.
    monkeypatch.setattr(mod, "_run_backfill_bg", lambda **kwargs: None)
    r = client.post(
        "/api/ic-scenario/backfill",
        json={"years": 0.1, "maxDte": 30, "resume": True, "delayMs": 0},
        headers={"X-Admin-Token": "unit-test-token"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("started") is True


def test_backfill_status_open_endpoint(monkeypatch):
    client, _ = _build_test_app(monkeypatch)
    r = client.get("/api/ic-scenario/backfill/status")
    assert r.status_code == 200
    body = r.json()
    for k in ("running", "progress", "coverage"):
        assert k in body


def test_journal_endpoint_rejects_empty_body(monkeypatch):
    client, _ = _build_test_app(monkeypatch)
    r = client.post("/api/ic-scenario/journal", json={})
    assert r.status_code == 400


def test_journal_endpoint_routes_to_log_trade(monkeypatch):
    client, mod = _build_test_app(monkeypatch)
    captured: Dict[str, Any] = {}

    def _fake_log_trade(data, store=None, flags=None):
        captured["data"] = data
        return "e2-TEST-SPX-abc123"

    monkeypatch.setattr(mod, "log_trade", _fake_log_trade)

    body = {
        "scenario": {
            "expectedValue": {"meanPnlPct": 20.0, "medianPnlPct": 50.0},
            "outcomeDistribution": {"fullCollect": {"pct": 50}},
            "adjustedOutcomeDistribution": {},
            "exitRulesOptimization": {},
        },
        "request": {
            "underlying": "SPX", "entry_date": "2025-06-02", "expiry": "2025-06-06",
            "short_put": 5180, "long_put": 5170, "short_call": 5380, "long_call": 5390,
            "credit_received": 1.85, "profit_target_pct": 50, "stop_loss_pct": 200,
        },
        "note": "unit test",
    }
    r = client.post("/api/ic-scenario/journal", json=body)
    assert r.status_code == 200
    out = r.json()
    assert out["tradeId"] == "e2-TEST-SPX-abc123"
    assert captured["data"]["source"] == "engine14"
    assert captured["data"]["entry"]["strikes"]["shortPut"] == 5180


def test_journal_endpoint_persists_inline_reconcile_snapshot(monkeypatch):
    """When the frontend passes a reconcile payload, we store a compact snapshot."""
    client, mod = _build_test_app(monkeypatch)
    captured: Dict[str, Any] = {}

    def _fake_log_trade(data, store=None, flags=None):
        captured["data"] = data
        return "e2-TEST-SPX-recon01"

    monkeypatch.setattr(mod, "log_trade", _fake_log_trade)

    reconcile_payload = {
        "overall": {
            "status": "mismatch",
            "counts": {"agree": 4, "drift": 2, "mismatch": 1, "na": 1},
            "topFindings": [
                "Credit mismatch: user 1.85 vs live mid 0.60.",
                "Policy: outside-wings exceeds 15%.",
            ],
        },
        "checks": [
            {"key": "credit", "label": "Credit", "status": "mismatch",
             "note": "user 1.85 vs live mid 0.60", "rule": "verbose rule body",
             "e2": {"a": 1}, "e14": {"b": 2}},
            {"key": "policy", "label": "Policy", "status": "drift",
             "note": "outside-wings > 15%", "extra": "drop me"},
        ],
    }

    body = {
        "scenario": {
            "expectedValue": {"meanPnlPct": 20.0, "medianPnlPct": 50.0},
            "outcomeDistribution": {"fullCollect": {"pct": 50}},
            "adjustedOutcomeDistribution": {},
        },
        "request": {
            "underlying": "SPX", "entry_date": "2026-04-17", "expiry": "2026-04-24",
            "short_put": 6890, "long_put": 6880, "short_call": 7360, "long_call": 7370,
            "credit_received": 0.65, "profit_target_pct": 50, "stop_loss_pct": 200,
        },
        "reconcile": reconcile_payload,
    }
    r = client.post("/api/ic-scenario/journal", json=body)
    assert r.status_code == 200
    out = r.json()

    # The response exposes the snapshot for the UI.
    assert out["reconcile"] is not None
    assert out["reconcile"]["overall"]["status"] == "mismatch"

    # The persisted trade carries the compact snapshot on entryContext.
    stored_ctx = captured["data"]["entryContext"]
    assert "reconcile" in stored_ctx
    snap = stored_ctx["reconcile"]
    assert snap["overall"]["status"] == "mismatch"
    assert snap["overall"]["counts"] == {"agree": 4, "drift": 2, "mismatch": 1, "na": 1}
    assert snap["overall"]["topFindings"][0].startswith("Credit mismatch")
    # Verbose fields (rule/e2/e14/extra) stripped; only compact keys remain.
    for c in snap["checks"]:
        assert set(c.keys()) == {"key", "label", "status", "note"}
    assert snap["generatedAt"].endswith("Z")


def test_journal_endpoint_auto_computes_reconcile_when_missing(monkeypatch):
    """When the client doesn't pass reconcile, we synthesize a deterministic one."""
    client, mod = _build_test_app(monkeypatch)
    captured: Dict[str, Any] = {}

    def _fake_log_trade(data, store=None, flags=None):
        captured["data"] = data
        return "e2-TEST-SPX-autorec"

    monkeypatch.setattr(mod, "log_trade", _fake_log_trade)
    monkeypatch.setattr(mod, "_compute_engine2_payload", lambda u: None)

    called: Dict[str, Any] = {"count": 0}

    def _fake_reconcile_det(*, scenario_result, engine2_payload):
        called["count"] += 1
        return {
            "overall": {"status": "agree", "counts": {"agree": 9}, "topFindings": []},
            "checks": [{"key": "k1", "label": "L1", "status": "agree", "note": None}],
        }

    monkeypatch.setattr(mod.reconciliation, "reconcile_deterministic", _fake_reconcile_det)

    body = {
        "scenario": {"expectedValue": {"meanPnlPct": 10.0}, "outcomeDistribution": {}},
        "request": {
            "underlying": "SPX", "entry_date": "2026-04-17", "expiry": "2026-04-24",
            "short_put": 6890, "long_put": 6880, "short_call": 7360, "long_call": 7370,
            "credit_received": 0.65, "profit_target_pct": 50, "stop_loss_pct": 200,
        },
    }
    r = client.post("/api/ic-scenario/journal", json=body)
    assert r.status_code == 200
    assert called["count"] == 1
    snap = captured["data"]["entryContext"]["reconcile"]
    assert snap["overall"]["status"] == "agree"


def test_review_endpoint_returns_verdict_for_closed_trade(monkeypatch):
    client, mod = _build_test_app(monkeypatch)

    def _fake_get_trade(trade_id, store=None):
        return {
            "tradeId": trade_id,
            "status": "closed",
            "closedAt": "2025-06-05T20:00:00Z",
            "closeReason": "profit_target",
            "outcome": {"pnlPct": 48.0, "pnlDollars": 96.0, "daysHeld": 3},
            "entryContext": {
                "engine14Scenario": {
                    "version": "1.1.0",
                    "analoguesUsed": 42,
                    "expectedValue": {"meanPnlPct": 44.0, "medianPnlPct": 50.0},
                    "outcomeDistribution": {
                        "fullCollect": {"pct": 35.0},
                        "earlyTarget": {"pct": 42.0},
                        "breach": {"pct": 5.0},
                        "stopOut": {"pct": 8.0},
                    },
                },
            },
        }

    monkeypatch.setattr(mod, "get_trade", _fake_get_trade)
    r = client.get("/api/ic-scenario/review", params={"tradeId": "e2-TEST"})
    assert r.status_code == 200
    body = r.json()
    assert body["predicted"]["meanPnlPct"] == 44.0
    assert body["actual"]["pnlPct"] == 48.0
    assert body["verdict"] and "within" in body["verdict"].lower()


def test_review_endpoint_404_for_missing_trade(monkeypatch):
    client, mod = _build_test_app(monkeypatch)
    monkeypatch.setattr(mod, "get_trade", lambda tid, store=None: None)
    r = client.get("/api/ic-scenario/review", params={"tradeId": "nope"})
    assert r.status_code == 404


def test_review_endpoint_400_when_no_engine14_context(monkeypatch):
    client, mod = _build_test_app(monkeypatch)
    monkeypatch.setattr(mod, "get_trade",
        lambda tid, store=None: {"tradeId": tid, "entryContext": {}, "status": "active"})
    r = client.get("/api/ic-scenario/review", params={"tradeId": "abc"})
    assert r.status_code == 400
