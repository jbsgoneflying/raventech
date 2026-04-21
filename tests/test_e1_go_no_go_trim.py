"""Engine 1 v2 — Go/No-Go trim tests.

With ``ENABLE_E1_OPTIONS_LIQUIDITY_GATE=False`` (the E1 v2 default), the
options-liquidity family of checks should report MISSING with the
``SN_OPT_GATE_DISABLED`` code and not appear as BLOCK / FAIL. The legal-reg
check and the underlying $-volume check should still be enforceable.
"""
from __future__ import annotations

import pytest

from backend import go_no_go


class _Resp:
    def __init__(self, rows):
        self.rows = rows


class _FakeClient:
    """Minimal client that satisfies go_no_go's reads."""

    def __init__(self, *, avg_dvol, strikes=None, monies=None, cores_extra=None):
        self._avg_dvol = avg_dvol
        self._strikes = strikes or []
        self._monies = monies or []
        self._cores_extra = cores_extra or {}

    def cores(self, ticker, fields, trade_date=None, **kw):
        row = {"ticker": ticker, "avgDollarVol20": self._avg_dvol, "stockPrice": 100}
        row.update(self._cores_extra)
        if trade_date:
            row["tradeDate"] = trade_date
        return _Resp([row])

    def hist_cores(self, ticker, trade_date, fields, **kw):
        return _Resp([{"ticker": ticker, "tradeDate": trade_date, "avgDollarVol20": self._avg_dvol}])

    def hist_monies_forecasts(self, ticker, trade_date, fields, dte, **kw):
        return _Resp(self._monies)

    def hist_strikes(self, ticker, trade_date, fields, dte, **kw):
        return _Resp(self._strikes)

    def live_summaries(self, ticker, **kw):
        return _Resp([{"spotPrice": 100.0}])


def _base_payload():
    return {
        "ticker": "NVDA",
        "current": {"asOfDate": "2026-04-21", "impliedMovePct": 6.0, "stockPrice": 100.0},
        "events": [{"realizedMovePct": 4.0} for _ in range(10)],
        "summary": {"events_used": 10},
    }


def _find(checks, cid):
    for c in checks:
        if c.get("id") == cid:
            return c
    return None


def test_options_liquidity_gate_disabled_reports_missing(monkeypatch):
    """Default v2 config: ENABLE_E1_OPTIONS_LIQUIDITY_GATE = False."""
    # Wide spreads would normally trigger BLOCK — with gate off, we want MISSING.
    strikes = [
        {"expirDate": "2026-05-02", "strike": 94, "stockPrice": 100, "putDelta": -0.17,
         "putBidPrice": 1.0, "putAskPrice": 1.4, "putOpenInterest": 5000, "putVolume": 500},
        {"expirDate": "2026-05-02", "strike": 106, "stockPrice": 100, "callDelta": 0.17,
         "callBidPrice": 1.0, "callAskPrice": 1.4, "callOpenInterest": 5000, "callVolume": 500},
    ]
    client = _FakeClient(
        avg_dvol=300_000_000.0,
        strikes=strikes,
        monies=[{"expirDate": "2026-05-02", "dte": 7}],
    )
    out = go_no_go.compute_go_no_go(client, ticker="NVDA", payload=_base_payload(), benzinga_client=None)
    liq = _find(out["checks"], "SN_LIQUIDITY")
    assert liq is not None
    # The options leg reports MISSING + the gate-disabled note; underlying $-vol still passes.
    liq_notes_list = (liq.get("value") or {}).get("notes") or []
    liq_explain = str(liq.get("explain") or "")
    all_notes = " ".join([*liq_notes_list, liq_explain]).lower()
    assert "gate disabled" in all_notes or "sn_opt_gate_disabled" in all_notes


def test_options_liquidity_gate_off_still_blocks_on_underlying(monkeypatch):
    # Underlying $-volume below hard BLOCK threshold → still BLOCK.
    client = _FakeClient(avg_dvol=5_000.0)  # $5k/day is critically illiquid
    out = go_no_go.compute_go_no_go(client, ticker="PENNY", payload=_base_payload(), benzinga_client=None)
    liq = _find(out["checks"], "SN_LIQUIDITY")
    assert liq is not None
    assert liq.get("code") in ("SN_LIQ_UNDERLYING_TOO_LOW", "SN_LIQ_UNDERLYING_LOW") or liq["state"] in ("FAIL", "BLOCK")


def test_options_liquidity_gate_on_re_enables_old_checks(monkeypatch):
    # Flip the flag ON and the old SN_OPT_SPREAD_* behavior should return.
    import backend.go_no_go as mod
    from backend.config import get_flags
    class _F:
        pass
    for k, v in vars(get_flags()).items():
        setattr(_F, k, v)
    _F.ENABLE_E1_OPTIONS_LIQUIDITY_GATE = True
    monkeypatch.setattr(mod, "get_flags", lambda: _F)

    strikes = [
        {"expirDate": "2026-05-02", "strike": 94, "stockPrice": 100, "putDelta": -0.17,
         "putBidPrice": 1.0, "putAskPrice": 1.4, "putOpenInterest": 5000, "putVolume": 500},
        {"expirDate": "2026-05-02", "strike": 106, "stockPrice": 100, "callDelta": 0.17,
         "callBidPrice": 1.0, "callAskPrice": 1.4, "callOpenInterest": 5000, "callVolume": 500},
    ]
    client = _FakeClient(
        avg_dvol=300_000_000.0,
        strikes=strikes,
        monies=[{"expirDate": "2026-05-02", "dte": 7}],
    )
    out = mod.compute_go_no_go(client, ticker="NVDA", payload=_base_payload(), benzinga_client=None)
    # With gate on, the options leg should report either PASS, FLAG, or BLOCK —
    # NOT MISSING with SN_OPT_GATE_DISABLED.
    liq = _find(out["checks"], "SN_LIQUIDITY")
    notes_list = (liq.get("value") or {}).get("notes") or []
    notes_join = " ".join([*notes_list, str(liq.get("explain") or "")]).lower()
    assert "sn_opt_gate_disabled" not in notes_join and "gate disabled" not in notes_join
