import datetime as dt

from backend.technicals import DailyBar, compute_ema_levels, compute_ichimoku_levels, compute_vwap_proxy


def test_compute_ema_levels_basic_monotonic():
    closes = [float(i) for i in range(1, 301)]
    out = compute_ema_levels(closes, spans=[8, 21, 50, 100, 200])
    # For a strictly increasing series, EMA should be below the last close.
    last = closes[-1]
    for k, v in out.items():
        assert k.startswith("ema")
        assert v is not None
        assert 0 < float(v) < last


def test_ichimoku_shapes_and_flags():
    # Build 200 synthetic daily bars with simple ranges.
    start = dt.date(2025, 1, 1)
    bars = []
    px = 100.0
    for i in range(200):
        d = (start + dt.timedelta(days=i)).isoformat()
        high = px * 1.01
        low = px * 0.99
        close = px
        bars.append(DailyBar(trade_date=d, open=px, high=high, low=low, close=close, volume=None, vwap=None))
        px *= 1.001

    ich = compute_ichimoku_levels(bars)
    assert ich["enabled"] is True
    assert "tenkan" in ich
    assert "kijun" in ich
    # With 200 bars, we should be able to compute both cloudNow and cloudFuture.
    assert ich.get("cloudNow") is not None
    assert ich.get("cloudFuture") is not None


def test_vwap_proxy_uses_volume_when_available():
    start = dt.date(2025, 1, 1)
    bars = []
    for i in range(30):
        d = (start + dt.timedelta(days=i)).isoformat()
        bars.append(DailyBar(trade_date=d, open=100, high=110, low=90, close=100, volume=1_000_000, vwap=None))
    v = compute_vwap_proxy(bars, window=20)
    assert v["enabled"] is True
    assert v["value"] is not None
    assert v["mode"] in ("rolling_daily_typical_price_vwap", "orats_daily_vwap")


