from __future__ import annotations

from backend.technicals import fetch_live_price_context_optional


class _FakeResp:
    def __init__(self, rows):
        self.rows = rows


class _FakeClient:
    def __init__(self, rows):
        self._rows = rows

    def live_summaries(self, ticker: str):
        return _FakeResp(self._rows)


class _FakePriceService:
    def __init__(self, *, intraday=None, close=None):
        self._intraday = intraday
        self._close = close

    def fetch_intraday_price(self, ticker: str):
        return self._intraday

    def fetch_live_price(self, ticker: str):
        return self._close


def test_live_price_context_open_prefers_orats(monkeypatch):
    monkeypatch.setattr("backend.technicals.is_us_equity_market_open", lambda: True)
    monkeypatch.setattr(
        "backend.price_service.get_price_service",
        lambda: _FakePriceService(intraday=501.25, close=500.50),
    )
    client = _FakeClient([{"spotPrice": 500.75, "stockPrice": 500.70}])

    out = fetch_live_price_context_optional(client, ticker="SPX")

    assert out["marketOpen"] is True
    assert out["mode"] == "open_live"
    assert out["source"] == "orats_live_summaries"
    assert out["price"] == 500.75


def test_live_price_context_open_falls_back_to_eodhd(monkeypatch):
    monkeypatch.setattr("backend.technicals.is_us_equity_market_open", lambda: True)
    monkeypatch.setattr(
        "backend.price_service.get_price_service",
        lambda: _FakePriceService(intraday=499.80, close=498.10),
    )
    client = _FakeClient([])

    out = fetch_live_price_context_optional(client, ticker="SPY")

    assert out["marketOpen"] is True
    assert out["mode"] == "open_live"
    assert out["source"] == "eodhd_live_quote"
    assert out["price"] == 499.80


def test_live_price_context_closed_uses_close_only(monkeypatch):
    monkeypatch.setattr("backend.technicals.is_us_equity_market_open", lambda: False)
    monkeypatch.setattr(
        "backend.price_service.get_price_service",
        lambda: _FakePriceService(intraday=450.0, close=447.25),
    )
    client = _FakeClient([{"spotPrice": 452.00, "stockPrice": 451.50}])

    out = fetch_live_price_context_optional(client, ticker="QQQ")

    assert out["marketOpen"] is False
    assert out["mode"] == "closed_close"
    assert out["source"] == "latest_close"
    assert out["price"] == 447.25

