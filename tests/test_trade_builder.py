from backend.trade_builder import compute_trade_builder


class FakeResp:
    def __init__(self, rows):
        self.rows = rows
        self.raw = rows


class FakeClient:
    def __init__(self):
        self._monies = [
            {"ticker": "TST", "tradeDate": "2025-12-12", "expirDate": "2025-12-14", "dte": 2, "stockPrice": 100.0, "vol50": 0.50},
        ]
        # strikes rows: one row per strike with both call/put fields
        self._strikes = [
            {"expirDate": "2025-12-14", "dte": 2, "strike": 85, "stockPrice": 100.0, "callBidPrice": 0.05, "callAskPrice": 0.07, "putBidPrice": 0.01, "putAskPrice": 0.02, "callDelta": 0.01, "putDelta": -0.20},
            {"expirDate": "2025-12-14", "dte": 2, "strike": 90, "stockPrice": 100.0, "callBidPrice": 0.10, "callAskPrice": 0.12, "putBidPrice": 0.03, "putAskPrice": 0.05, "callDelta": 0.03, "putDelta": -0.10},
            {"expirDate": "2025-12-14", "dte": 2, "strike": 100, "stockPrice": 100.0, "callBidPrice": 2.10, "callAskPrice": 2.20, "putBidPrice": 2.05, "putAskPrice": 2.15, "callDelta": 0.50, "putDelta": -0.50},
            {"expirDate": "2025-12-14", "dte": 2, "strike": 110, "stockPrice": 100.0, "callBidPrice": 0.03, "callAskPrice": 0.05, "putBidPrice": 0.10, "putAskPrice": 0.12, "callDelta": 0.10, "putDelta": -0.03},
            {"expirDate": "2025-12-14", "dte": 2, "strike": 115, "stockPrice": 100.0, "callBidPrice": 0.01, "callAskPrice": 0.02, "putBidPrice": 0.20, "putAskPrice": 0.22, "callDelta": 0.05, "putDelta": -0.01},
        ]

    def hist_monies_implied(self, *, ticker: str, trade_date: str, fields: str, dte: str | None = None):
        return FakeResp(self._monies if ticker == "TST" else [])

    def hist_strikes(self, *, ticker: str, trade_date: str, fields: str, dte: str | None = None, delta: str | None = None):
        return FakeResp(self._strikes if ticker == "TST" else [])


def test_trade_builder_equal_delta_selects_strikes_and_width():
    client = FakeClient()
    out = compute_trade_builder(
        client,
        ticker="TST",
        as_of_date="2025-12-12",
        inputs={"mode": "equal_delta", "symmetry": "auto", "target_delta": 0.10, "wing_width": 5, "dte_target": 2},
        wing_recommendation={"structureMode": "AUTO_EQUAL_DELTA"},
    )
    assert out["expiration"] == "2025-12-14"
    assert out["put"]["shortStrike"] == 90.0
    assert out["put"]["longStrike"] == 85.0
    assert out["call"]["shortStrike"] == 110.0
    assert out["call"]["longStrike"] == 115.0

