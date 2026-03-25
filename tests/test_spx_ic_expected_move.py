import datetime as dt
import math

from backend.spx_ic.live_levels import compute_expected_move_weekly, _iv_to_weekly_em, _pick_iv


class _Resp:
    def __init__(self, rows):
        self.rows = rows


class _Client:
    def __init__(self, *, delayed_imp=None, eod_imp=None, delayed_iv7=None, eod_iv7=None):
        self._delayed_imp = delayed_imp
        self._eod_imp = eod_imp
        self._delayed_iv7 = delayed_iv7
        self._eod_iv7 = eod_iv7

    def live_expirations(self, *, ticker: str):
        return _Resp([{"expirDate": "2026-03-27"}])

    def live_strikes_by_expiry(self, *, ticker: str, expiry: str, fields: str | None = None):
        rows = [
            {
                "expirDate": "2026-03-27",
                "strike": 95.0,
                "spotPrice": 100.0,
                "callBidPrice": 6.0,
                "callAskPrice": 6.2,
                "putBidPrice": 1.0,
                "putAskPrice": 1.2,
            },
            {
                "expirDate": "2026-03-27",
                "strike": 100.0,
                "spotPrice": 100.0,
                "callBidPrice": 3.0,
                "callAskPrice": 3.2,
                "putBidPrice": 3.0,
                "putAskPrice": 3.2,
            },
            {
                "expirDate": "2026-03-27",
                "strike": 105.0,
                "spotPrice": 100.0,
                "callBidPrice": 1.0,
                "callAskPrice": 1.2,
                "putBidPrice": 6.0,
                "putAskPrice": 6.2,
            },
        ]
        return _Resp(rows)

    def cores_delayed(self, *, ticker: str, fields: str):
        if self._delayed_imp is None and self._delayed_iv7 is None:
            raise RuntimeError("delayed unavailable")
        row = {
            "tradeDate": "2026-03-25",
            "updatedAt": "2026-03-25 20:55:12",
        }
        if self._delayed_imp is not None:
            row["impErnMv"] = self._delayed_imp
        if self._delayed_iv7 is not None:
            row["iv7"] = self._delayed_iv7
        return _Resp([row])

    def hist_cores(self, ticker: str, trade_date: str, fields: str):
        if self._eod_imp is None and self._eod_iv7 is None:
            return _Resp([])
        row = {"tradeDate": trade_date}
        if self._eod_imp is not None:
            row["impErnMv"] = self._eod_imp
        if self._eod_iv7 is not None:
            row["iv7"] = self._eod_iv7
        return _Resp([row])


def test_expected_move_weekly_prefers_delayed_orats_em(monkeypatch):
    monkeypatch.setattr(
        "backend.spx_ic.live_levels.fetch_live_price_context_optional",
        lambda client, ticker: {"price": 101.0, "source": "latest_close", "mode": "closed_close", "marketOpen": False},
    )
    c = _Client(delayed_imp=0.048, eod_imp=0.052)
    out = compute_expected_move_weekly(c, ticker="SPX", today=dt.date(2026, 3, 25), symbols=("SPXW",))

    assert out["oratsExpectedMovePct"] == 4.8
    assert out["oratsExpectedMoveSource"] == "delayed"
    assert out["delayedImpliedMovePct"] == 4.8
    assert out["eodImpliedMovePct"] == 5.2
    assert out["expectedMovePct"] is not None


def test_expected_move_weekly_falls_back_to_eod_orats_em(monkeypatch):
    monkeypatch.setattr(
        "backend.spx_ic.live_levels.fetch_live_price_context_optional",
        lambda client, ticker: {"price": 100.0, "source": "latest_close", "mode": "closed_close", "marketOpen": False},
    )
    c = _Client(delayed_imp=None, eod_imp=0.051)
    out = compute_expected_move_weekly(c, ticker="QQQ", today=dt.date(2026, 3, 25), symbols=("QQQ",))

    assert out["delayedImpliedMovePct"] is None
    assert out["oratsExpectedMovePct"] == 5.1
    assert out["oratsExpectedMoveSource"] == "eod"


def test_iv_derived_em_when_impernmv_null_index(monkeypatch):
    """For indices (SPX), impErnMv is null — EM should be derived from iv7."""
    monkeypatch.setattr(
        "backend.spx_ic.live_levels.fetch_live_price_context_optional",
        lambda client, ticker: {"price": 6556.0, "source": "latest_close", "mode": "closed_close", "marketOpen": False},
    )
    # No impErnMv, but iv7=16.5% annualized, DTE=2 (expiry 2026-03-27, today 2026-03-25)
    c = _Client(delayed_iv7=0.165, eod_iv7=0.17)
    out = compute_expected_move_weekly(c, ticker="SPX", today=dt.date(2026, 3, 25), symbols=("SPXW",))

    # iv7=16.5% -> 16.5, EM = 16.5 * sqrt(2/365) ≈ 1.22%
    assert out["delayedImpliedMovePct"] is not None
    assert out["delayedImpliedMovePct"] > 1.0
    assert out["eodImpliedMovePct"] is not None
    assert out["eodImpliedMovePct"] > 1.0
    assert out["oratsExpectedMovePct"] == out["delayedImpliedMovePct"]
    assert out["oratsExpectedMoveSource"] == "delayed"


def test_iv_derived_em_eod_fallback_only(monkeypatch):
    """EOD iv7 used when delayed is unavailable."""
    monkeypatch.setattr(
        "backend.spx_ic.live_levels.fetch_live_price_context_optional",
        lambda client, ticker: {"price": 6556.0, "source": "latest_close", "mode": "closed_close", "marketOpen": False},
    )
    c = _Client(eod_iv7=0.18)
    out = compute_expected_move_weekly(c, ticker="SPX", today=dt.date(2026, 3, 25), symbols=("SPXW",))

    assert out["delayedImpliedMovePct"] is None
    assert out["eodImpliedMovePct"] is not None
    assert out["eodImpliedMovePct"] > 1.0
    assert out["oratsExpectedMovePct"] == out["eodImpliedMovePct"]
    assert out["oratsExpectedMoveSource"] == "eod"


def test_iv_to_weekly_em_helper():
    em = _iv_to_weekly_em(16.5, 5)
    expected = 16.5 * math.sqrt(5 / 365.0)
    assert abs(em - expected) < 0.001

    assert _iv_to_weekly_em(None, 5) is None
    assert _iv_to_weekly_em(16.5, 0) is None


def test_pick_iv_prefers_short_term():
    assert _pick_iv({"iv7": 0.18, "iv30": 0.22}) == 18.0
    assert _pick_iv({"iv30": 0.22}) == 22.0
    assert _pick_iv({"iv": 0.25}) == 25.0
    assert _pick_iv({}) is None
