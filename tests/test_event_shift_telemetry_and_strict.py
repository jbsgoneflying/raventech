import datetime as dt

from backend.earnings_logic import compute_breach_stats


class FakeResp:
    def __init__(self, rows):
        self.rows = rows
        self.raw = rows


class ShiftedWindowClient:
    """
    Single AMC event where the next trading day open is missing, but exists the day after.
    This forces realized-window probing to shift by +1 calendar day.
    """

    def __init__(self):
        self._earnings = [{"earnDate": "2025-03-01", "anncTod": "1630"}]  # AMC
        self._dailies = {
            ("TST", "2025-03-01"): {"tradeDate": "2025-03-01", "clsPx": 100.0, "open": 100.0},
            # ("TST", "2025-03-02") missing -> forces shift
            ("TST", "2025-03-03"): {"tradeDate": "2025-03-03", "clsPx": 102.0, "open": 104.0},
        }
        self._cores = {("TST", "2025-03-01"): {"tradeDate": "2025-03-01", "impErnMv": 5.0}}

    def hist_earnings(self, ticker: str):
        return FakeResp(self._earnings if ticker == "TST" else [])

    def hist_dailies(self, ticker: str, trade_date: str, fields: str):
        row = self._dailies.get((ticker, trade_date))
        return FakeResp([row] if row else [])

    def hist_cores(self, ticker: str, trade_date: str, fields: str):
        row = self._cores.get((ticker, trade_date))
        return FakeResp([row] if row else [])

    # Provide minimal range-mode support so regime overlay/backtest does not fall back to slow probing.
    def get(self, path: str, params: dict):
        # Only the range calls used by regime_overlay are supported here.
        if path == "/hist/dailies" and params.get("ticker") == "SPY":
            # Minimal synthetic SPY series with enough points for RV20 and percentiles to compute.
            to_date = str(params.get("toDate") or "2025-03-01")[:10]
            end = dt.date.fromisoformat(to_date)
            rows = []
            px = 100.0
            for i in range(0, 60):
                d = end - dt.timedelta(days=(60 - i))
                px *= 1.001
                rows.append({"ticker": "SPY", "tradeDate": d.isoformat(), "clsPx": px, "open": px})
            return FakeResp(rows)
        if path == "/hist/cores" and params.get("ticker") in ("TST", "SPY"):
            to_date = str(params.get("toDate") or "2025-03-01")[:10]
            end = dt.date.fromisoformat(to_date)
            rows = []
            iv = 0.3
            for i in range(0, 60):
                d = end - dt.timedelta(days=(60 - i))
                iv = min(1.0, max(0.05, iv + (0.001 if i % 3 == 0 else -0.0005)))
                rows.append({"ticker": params.get("ticker"), "tradeDate": d.isoformat(), "iv30": iv})
            return FakeResp(rows)
        return FakeResp([])


def test_realized_window_shift_telemetry_is_reported(monkeypatch):
    monkeypatch.setenv("STRICT_REALIZED_WINDOW", "false")
    monkeypatch.setenv("ADD_EVENT_SHIFT_TELEMETRY", "true")

    out = compute_breach_stats(client=ShiftedWindowClient(), ticker="TST", n=20, years=5, k=1.0, today=dt.date(2025, 3, 1))
    assert out["summary"]["events_used"] == 1
    ev = out["events"][0]
    assert ev["timing"] == "AMC"
    assert ev["openDateUsed"] == "2025-03-03"
    assert ev["realizedWindowShiftDays"] == 1
    assert out["summary"]["eventsWithRealizedWindowShift"] == 1
    assert out["summary"]["realizedWindowShiftDaysMax"] == 1


def test_strict_realized_window_rejects_shifted_window(monkeypatch):
    monkeypatch.setenv("STRICT_REALIZED_WINDOW", "true")
    monkeypatch.setenv("ADD_EVENT_SHIFT_TELEMETRY", "true")

    out = compute_breach_stats(client=ShiftedWindowClient(), ticker="TST", n=20, years=5, k=1.0, today=dt.date(2025, 3, 1))
    assert out["summary"]["events_used"] == 0
    assert out["skipped"] and out["skipped"][0]["reason"].startswith("shifted AMC realized window")
    # Telemetry is still present for auditability
    ev = out["events"][0]
    assert ev["realizedWindowShiftDays"] == 1


