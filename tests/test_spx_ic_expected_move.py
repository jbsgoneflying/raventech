import datetime as dt

from backend.spx_ic.live_levels import compute_expected_move_weekly


class _Resp:
    def __init__(self, rows):
        self.rows = rows


class _Client:
    def __init__(self, *, delayed_imp=None, eod_imp=None):
        self._delayed_imp = delayed_imp
        self._eod_imp = eod_imp

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
        if self._delayed_imp is None:
            raise RuntimeError("delayed unavailable")
        return _Resp(
            [
                {
                    "tradeDate": "2026-03-25",
                    "updatedAt": "2026-03-25 20:55:12",
                    "impErnMv": self._delayed_imp,
                }
            ]
        )

    def hist_cores(self, ticker: str, trade_date: str, fields: str):
        if self._eod_imp is None:
            return _Resp([])
        return _Resp([{"tradeDate": trade_date, "impErnMv": self._eod_imp}])


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
