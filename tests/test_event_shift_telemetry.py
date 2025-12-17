import datetime as dt

from backend.earnings_logic import compute_breach_stats


class FakeResp:
    def __init__(self, rows):
        self.rows = rows
        self.raw = rows


class TelemetryClient:
    """
    Minimal fake ORATS client to exercise:
    - realized-window probing telemetry (shift days)
    - strict-window behavior requiring the needed fields
    - cores fallback telemetry (pricingDateShiftDays)
    """

    def __init__(self):
        self._earnings = [{"earnDate": "2025-02-05", "anncTod": "0830"}]  # BMO

        # Dailies:
        # - 2025-02-04 exists but missing clsPx (this is the key strict-mode case)
        # - 2025-02-03 is usable for close
        # - 2025-02-05 open is available for BMO open
        self._dailies = {
            ("TST", "2025-02-04"): {"tradeDate": "2025-02-04", "clsPx": None, "open": 100.0},
            ("TST", "2025-02-03"): {"tradeDate": "2025-02-03", "clsPx": 100.0, "open": 100.0},
            ("TST", "2025-02-05"): {"tradeDate": "2025-02-05", "clsPx": 99.0, "open": 98.0},
        }

        # Cores:
        # - missing on 2025-02-03 to force a 1-day fallback to 2025-02-02
        self._cores = {
            ("TST", "2025-02-02"): {"tradeDate": "2025-02-02", "impErnMv": 4.0},
        }

    def hist_earnings(self, ticker: str):
        return FakeResp(self._earnings if ticker == "TST" else [])

    def hist_dailies(self, ticker: str, trade_date: str, fields: str):
        row = self._dailies.get((ticker, trade_date))
        return FakeResp([row] if row else [])

    def hist_cores(self, ticker: str, trade_date: str, fields: str):
        row = self._cores.get((ticker, trade_date))
        return FakeResp([row] if row else [])


def test_shift_telemetry_and_strict_mode_changes_usability(monkeypatch):
    client = TelemetryClient()

    # Non-strict: prior_bar will “hit” 2025-02-04 (because bar has open), but clsPx is missing,
    # so realized cannot be computed and the event is unusable.
    monkeypatch.setenv("STRICT_REALIZED_WINDOW", "0")
    out = compute_breach_stats(client=client, ticker="TST", n=20, years=5, k=1.0, today=dt.date(2025, 3, 1))
    assert out["summary"]["events_found"] == 1
    assert out["summary"]["events_used"] == 0
    ev = out["events"][0]
    assert ev["timing"] == "BMO"
    # No close date selected => no realized-window telemetry for the close leg
    assert ev.get("realizedWindowShiftDays") is None

    # Strict: event remains unusable because the realized window shifts away from the spec anchor date
    # (earnDate-1 probing start). However, telemetry still surfaces the shift magnitude.
    monkeypatch.setenv("STRICT_REALIZED_WINDOW", "1")
    out2 = compute_breach_stats(client=client, ticker="TST", n=20, years=5, k=1.0, today=dt.date(2025, 3, 1))
    assert out2["summary"]["events_used"] == 0
    ev2 = out2["events"][0]
    assert ev2["closeDateUsed"] == "2025-02-03"
    # Shift is measured from the first probed date (earnDate-1 = 2025-02-04) to the bar used (2025-02-03) => 1 day.
    assert ev2.get("realizedWindowShiftDays") == 1
    # Cores fallback from 2025-02-03 to 2025-02-02 => 1 day.
    assert ev2.get("pricingDateShiftDays") == 1
    assert out2["skipped"] and out2["skipped"][0]["reason"].startswith("shifted BMO realized window")


