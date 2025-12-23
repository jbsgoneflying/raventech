import datetime as dt

from backend.earnings_logic import _band_pct_from_em_pct, _compute_live_dealer_gamma_payload, _select_live_expiry
from backend.orats_client import OratsResponse


class FakeLiveClient:
    def __init__(self, expirations: list[str], chain_rows_by_expiry: dict[str, list[dict]]):
        self._expirations = expirations
        self._chains = chain_rows_by_expiry

    def live_expirations(self, *, ticker: str) -> OratsResponse:
        rows = [{"expirDate": d} for d in self._expirations]
        return OratsResponse(rows=rows, raw={"rows": rows})

    def live_strikes_by_expiry(self, *, ticker: str, expiry: str, fields: str | None = None) -> OratsResponse:
        rows = self._chains.get(str(expiry)[:10], [])
        return OratsResponse(rows=rows, raw={"rows": rows})

    def live_strikes(self, *, ticker: str, fields: str | None = None) -> OratsResponse:
        # Return all rows across expiries
        all_rows: list[dict] = []
        for exp, rows in self._chains.items():
            for r in rows:
                rr = dict(r)
                rr.setdefault("expirDate", exp)
                all_rows.append(rr)
        return OratsResponse(rows=all_rows, raw={"rows": all_rows})


def test_band_pct_from_em_pct_clamps_and_warns():
    band, warns = _band_pct_from_em_pct(25.0)  # 25% EM -> clamp to 12%
    assert abs(band - 0.12) < 1e-9
    assert any("clamped" in w.lower() for w in warns)


def test_select_live_expiry_prefers_on_or_after_target():
    exp_dates = ["2026-01-17", "2026-01-24", "2026-02-07"]
    today = dt.date(2026, 1, 10)
    target = dt.date(2026, 1, 29)
    picked = _select_live_expiry(exp_dates, today=today, target_on_or_after=target)
    assert picked == "2026-02-07"


def test_compute_live_dealer_gamma_payload_picks_target_expiry_and_returns_payload():
    expirations = ["2026-01-17", "2026-02-07"]
    chain_rows = [
        # Minimal strike row with spot + gamma and OI weights
        {"spotPrice": 100.0, "strike": 95.0, "gamma": 0.01, "callOpenInterest": 100, "putOpenInterest": 50},
        {"spotPrice": 100.0, "strike": 105.0, "gamma": 0.02, "callOpenInterest": 80, "putOpenInterest": 40},
    ]
    client = FakeLiveClient(expirations=expirations, chain_rows_by_expiry={"2026-02-07": chain_rows})
    out = _compute_live_dealer_gamma_payload(
        client,
        ticker="AAPL",
        today=dt.date(2026, 1, 10),
        target_date=dt.date(2026, 1, 29),
        band_pct=0.05,
        top_n=2,
    )
    assert out is not None
    assert out["enabled"] is True
    assert out["symbolUsed"] == "AAPL"
    assert out["expiry"] == "2026-02-07"
    assert out["dealerGamma"]["spot"] == 100.0


def test_compute_live_dealer_gamma_payload_returns_none_when_chain_empty():
    client = FakeLiveClient(expirations=["2026-02-07"], chain_rows_by_expiry={"2026-02-07": []})
    out = _compute_live_dealer_gamma_payload(
        client,
        ticker="AAPL",
        today=dt.date(2026, 1, 10),
        target_date=dt.date(2026, 1, 29),
        band_pct=0.05,
        top_n=2,
    )
    assert out is None


def test_diag_falls_back_to_strikes_when_expirations_empty_and_still_computes():
    from backend.earnings_logic import _compute_live_dealer_gamma_payload_diag

    chain_rows = [
        {"spotPrice": 100.0, "strike": 100.0, "gamma": 0.01, "callOpenInterest": 100, "putOpenInterest": 80},
    ]
    client = FakeLiveClient(expirations=[], chain_rows_by_expiry={"2026-02-07": chain_rows})
    out = _compute_live_dealer_gamma_payload_diag(
        client,
        ticker="AAPL",
        today=dt.date(2026, 1, 10),
        target_date=dt.date(2026, 1, 29),
        band_pct=0.05,
        top_n=1,
    )
    assert out["enabled"] is True
    assert out["expiry"] == "2026-02-07"


