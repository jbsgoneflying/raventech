import datetime as dt

from backend.earnings_logic import compute_breach_stats


class FakeResp:
    def __init__(self, rows):
        self.rows = rows
        self.raw = rows


class OneBreachClient:
    """
    One AMC event that breaches (k=1), used to test beta decision outputs integration.
    """

    def __init__(self):
        self._earnings = [{"earnDate": "2025-03-01", "anncTod": "1630"}]  # AMC
        self._dailies = {
            ("TST", "2025-03-01"): {"tradeDate": "2025-03-01", "clsPx": 100.0, "open": 100.0},
            ("TST", "2025-03-02"): {"tradeDate": "2025-03-02", "clsPx": 110.0, "open": 112.0},  # +12% open
        }
        self._cores = {("TST", "2025-03-01"): {"tradeDate": "2025-03-01", "impErnMv": 5.0}}  # 5% implied

    def hist_earnings(self, ticker: str):
        return FakeResp(self._earnings if ticker == "TST" else [])

    def hist_dailies(self, ticker: str, trade_date: str, fields: str):
        row = self._dailies.get((ticker, trade_date))
        return FakeResp([row] if row else [])

    def hist_cores(self, ticker: str, trade_date: str, fields: str):
        row = self._cores.get((ticker, trade_date))
        return FakeResp([row] if row else [])

    # Minimal range-mode support for regime overlay/backtest.
    def get(self, path: str, params: dict):
        if path == "/hist/dailies" and params.get("ticker") == "SPY":
            to_date = str(params.get("toDate") or "2025-03-01")[:10]
            end = dt.date.fromisoformat(to_date)
            rows = []
            px = 100.0
            for i in range(0, 40):
                d = end - dt.timedelta(days=(40 - i))
                px *= 1.001
                rows.append({"ticker": "SPY", "tradeDate": d.isoformat(), "clsPx": px, "open": px})
            return FakeResp(rows)
        if path == "/hist/cores" and params.get("ticker") == "TST":
            to_date = str(params.get("toDate") or "2025-03-01")[:10]
            end = dt.date.fromisoformat(to_date)
            rows = []
            for i in range(0, 40):
                d = end - dt.timedelta(days=(40 - i))
                rows.append({"ticker": "TST", "tradeDate": d.isoformat(), "iv30": 0.30})
            return FakeResp(rows)
        return FakeResp([])


def test_summary_decision_beta_fields_are_added(monkeypatch):
    monkeypatch.setenv("USE_BETA_POSTERIOR_FOR_DECISIONING", "true")
    monkeypatch.setenv("BETA_PRIOR_ALPHA", "1.0")
    monkeypatch.setenv("BETA_PRIOR_BETA", "1.0")

    out = compute_breach_stats(client=OneBreachClient(), ticker="TST", n=20, years=5, k=1.0, today=dt.date(2025, 3, 1))
    assert out["summary"]["events_used"] == 1
    assert out["summary"]["breach_rate_pct"] == 100.0
    assert out["summary"]["breachRatePct_raw"] == 100.0

    sd = out["summaryDecision"]
    assert isinstance(sd, dict)
    # 1/1 breaches with Beta(1,1) prior -> Beta(2,1) posterior mean = 2/3
    assert abs(float(sd["breachProb_mean_beta"]) - (2.0 / 3.0)) < 1e-6
    ci = sd["breachProb_ci90"]
    assert 0.0 <= float(ci["lo"]) < float(sd["breachProb_mean_beta"]) < float(ci["hi"]) <= 1.0


