"""Tests for backend.engine14.live_chain helpers."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from backend.engine14.live_chain import fetch_live_chain_nbbo, validate_strikes_exist


class _FakeClient:
    """ORATS client shim that returns canned live_strikes rows."""

    def __init__(self, rows):
        self._rows = list(rows)
        self.calls = 0

    def live_strikes(self, *, ticker, fields=None):
        self.calls += 1
        return SimpleNamespace(rows=list(self._rows))

    def live_strikes_by_expiry(self, *, ticker, expiry, fields=None):
        self.calls += 1
        return SimpleNamespace(rows=list(self._rows))


def _row(strike, *, expiry="2026-04-24",
         call_bid=0.0, call_ask=0.0, put_bid=0.0, put_ask=0.0):
    return {
        "ticker": "SPX", "expirDate": expiry, "strike": strike,
        "callBidPrice": call_bid, "callAskPrice": call_ask,
        "putBidPrice": put_bid,   "putAskPrice": put_ask,
    }


def test_validate_strikes_exist_all_present():
    rows = [
        _row(6880.0), _row(6890.0), _row(7360.0), _row(7370.0),
        _row(7365.0, expiry="2026-04-25"),  # wrong expiry, ignored
    ]
    out = validate_strikes_exist(
        _FakeClient(rows), ticker="SPX", expiry="2026-04-24",
        short_put=6890.0, long_put=6880.0,
        short_call=7360.0, long_call=7370.0,
    )
    assert out["ok"] is True
    assert out["expiryFound"] is True
    assert out["missing"] == []


def test_validate_strikes_exist_flags_missing_and_suggests_nearest():
    """The exact fat-finger scenario: 7365 not on the chain, 7360 is."""
    rows = [_row(6880.0), _row(6890.0), _row(7360.0), _row(7370.0)]
    out = validate_strikes_exist(
        _FakeClient(rows), ticker="SPX", expiry="2026-04-24",
        short_put=6890.0, long_put=6880.0,
        short_call=7365.0, long_call=7375.0,
    )
    assert out["ok"] is False
    assert out["expiryFound"] is True
    missing_legs = {m["leg"]: m for m in out["missing"]}
    assert "shortCall" in missing_legs
    assert missing_legs["shortCall"]["nearest"] == pytest.approx(7360.0)
    assert missing_legs["longCall"]["nearest"] == pytest.approx(7370.0)


def test_validate_strikes_exist_no_chain_returns_na():
    out = validate_strikes_exist(
        _FakeClient([]), ticker="SPX", expiry="2026-04-24",
        short_put=6890.0, long_put=6880.0,
        short_call=7360.0, long_call=7370.0,
    )
    assert out["ok"] is False
    assert out["expiryFound"] is False
    assert "unavailable" in out["note"].lower()


def test_fetch_live_chain_nbbo_computes_net_credit():
    """Put spread: sell 6890 for 0.50/0.60, buy 6880 for 0.30/0.40.
    Call spread: sell 7360 for 0.10/0.20, buy 7370 for 0.05/0.15.

    Mid credit = (0.55 + 0.15) - (0.35 + 0.10) = 0.70 - 0.45 = 0.25
    Worst case (netBid): sell@bid - buy@ask = (0.50+0.10) - (0.40+0.15) = 0.05
    Best case  (netAsk): sell@ask - buy@bid = (0.60+0.20) - (0.30+0.05) = 0.45
    """
    rows = [
        _row(6880.0, put_bid=0.30, put_ask=0.40),
        _row(6890.0, put_bid=0.50, put_ask=0.60),
        _row(7360.0, call_bid=0.10, call_ask=0.20),
        _row(7370.0, call_bid=0.05, call_ask=0.15),
    ]
    out = fetch_live_chain_nbbo(
        _FakeClient(rows), ticker="SPX", expiry="2026-04-24",
        short_put=6890.0, long_put=6880.0,
        short_call=7360.0, long_call=7370.0,
    )
    assert out is not None
    assert out["mid"] == pytest.approx(0.25, abs=1e-3)
    assert out["netBid"] == pytest.approx(0.05, abs=1e-3)
    assert out["netAsk"] == pytest.approx(0.45, abs=1e-3)
    assert out["legs"]["shortPut"]["strike"] == 6890.0
    assert out["legs"]["shortPut"]["side"] == "short"
    assert out["legs"]["longCall"]["side"] == "long"


def test_fetch_live_chain_nbbo_returns_none_when_leg_missing():
    """If any leg lacks a quote we decline to anchor credit on live data."""
    rows = [
        _row(6880.0, put_bid=0.30, put_ask=0.40),
        _row(6890.0, put_bid=0.50, put_ask=0.60),
        _row(7360.0, call_bid=0.10, call_ask=0.20),
        # 7370 missing entirely
    ]
    out = fetch_live_chain_nbbo(
        _FakeClient(rows), ticker="SPX", expiry="2026-04-24",
        short_put=6890.0, long_put=6880.0,
        short_call=7360.0, long_call=7370.0,
    )
    assert out is None


def test_fetch_live_chain_nbbo_returns_none_when_quote_has_no_bid_ask():
    rows = [
        _row(6880.0, put_bid=0.30, put_ask=0.40),
        _row(6890.0),  # no quote
        _row(7360.0, call_bid=0.10, call_ask=0.20),
        _row(7370.0, call_bid=0.05, call_ask=0.15),
    ]
    out = fetch_live_chain_nbbo(
        _FakeClient(rows), ticker="SPX", expiry="2026-04-24",
        short_put=6890.0, long_put=6880.0,
        short_call=7360.0, long_call=7370.0,
    )
    assert out is None


def test_fetch_live_chain_nbbo_none_when_client_none():
    out = fetch_live_chain_nbbo(
        None, ticker="SPX", expiry="2026-04-24",
        short_put=6890.0, long_put=6880.0,
        short_call=7360.0, long_call=7370.0,
    )
    assert out is None
