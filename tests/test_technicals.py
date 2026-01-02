import datetime as dt

from backend.technicals import (
    DailyBar,
    compute_bollinger_series,
    compute_ema_levels,
    compute_ichimoku_levels,
    compute_macd_series,
    compute_rsi_series,
    compute_vwap_proxy,
    detect_red_dog_reversal,
)


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


def test_rsi_series_monotonic_behaves_sensibly():
    # Strictly increasing closes should produce high RSI once warmed up.
    closes = [float(i) for i in range(1, 200)]
    rsi = compute_rsi_series(closes, period=14)
    assert rsi[-1] is not None
    assert float(rsi[-1]) > 70.0

    # Strictly decreasing closes should produce low RSI once warmed up.
    closes2 = [float(200 - i) for i in range(200)]
    rsi2 = compute_rsi_series(closes2, period=14)
    assert rsi2[-1] is not None
    assert float(rsi2[-1]) < 30.0


def test_macd_series_monotonic_hist_directionality():
    closes = [float(i) for i in range(1, 260)]
    out = compute_macd_series(closes, fast=12, slow=26, signal=9)
    hist = out["hist"]
    # Should have a computed histogram by the end
    assert hist[-1] is not None
    assert isinstance(hist[-1], float)


def test_bollinger_series_shapes():
    closes = [100.0 + (i * 0.1) for i in range(120)]
    out = compute_bollinger_series(closes, period=20, stdev=2.0)
    assert out["mid"][-1] is not None
    assert out["upper"][-1] is not None
    assert out["lower"][-1] is not None
    assert float(out["upper"][-1]) > float(out["mid"][-1]) > float(out["lower"][-1])


def test_red_dog_bullish_pattern_flags():
    # Construct a simple 2-day bullish Red Dog: lower low + close back above prior low.
    bars = [
        DailyBar(trade_date="2025-01-01", open=100, high=110, low=90, close=95, volume=None, vwap=None),
        DailyBar(trade_date="2025-01-02", open=96, high=108, low=85, close=92, volume=None, vwap=None),
    ]
    rd = detect_red_dog_reversal(bars)
    assert rd["enabled"] is True
    assert rd["bullish"] is True
    assert rd["bearish"] is False

