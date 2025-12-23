import datetime as dt

import pytest

from backend.config import FeatureFlags
from backend.spx_ic_engine import (
    backtest_weekly_ic_risk,
    beta_binomial_mean,
    compute_engine2_spx_ic,
    pctile,
)


class FakeResp:
    def __init__(self, rows):
        self.rows = rows
        self.raw = rows


class FakeOratsClient:
    """
    Minimal ORATS client stub for Engine 2:
    - hist_dailies: provides clsPx by date OR a date-range (start,end)
    - hist_monies_implied: provides vol50 by date with dte field (used by backtest_weekly_ic_risk tests)
    """

    def __init__(self):
        self._dailies = {}
        self._iv = {}

    def add_close(self, ticker: str, date: str, close: float):
        self._dailies[(ticker, date)] = {"tradeDate": date, "clsPx": float(close), "close": float(close)}

    def set_iv(self, ticker: str, trade_date: str, dte: int, vol50: float):
        self._iv[(ticker, trade_date)] = {"tradeDate": trade_date, "dte": int(dte), "vol50": float(vol50)}

    def hist_dailies(self, ticker: str, trade_date: str, fields: str):
        td = str(trade_date)
        if "," in td:
            a, b = [x.strip()[:10] for x in td.split(",", 1)]
            rows = []
            for (t, d), row in self._dailies.items():
                if t != ticker:
                    continue
                dd = str(d)[:10]
                if a <= dd <= b:
                    rows.append(row)
            rows.sort(key=lambda r: str(r.get("tradeDate") or ""))
            return FakeResp(rows)
        row = self._dailies.get((ticker, td[:10]))
        return FakeResp([row] if row else [])

    def hist_monies_implied(self, *, ticker: str, trade_date: str, fields: str, dte: str | None = None):
        row = self._iv.get((ticker, trade_date))
        # Return one row; Engine picks nearest dte anyway.
        return FakeResp([row] if row else [])


def test_backtest_weekly_ic_risk_basic():
    c = FakeOratsClient()

    # Build 3 weeks of Mon->Fri closes for SPY in Jan 2025.
    # Week 1: flat
    c.add_close("SPY", "2025-01-06", 100.0)  # Mon
    c.add_close("SPY", "2025-01-10", 100.0)  # Fri
    c.set_iv("SPY", "2025-01-06", dte=4, vol50=20.0)

    # Week 2: +2%
    c.add_close("SPY", "2025-01-13", 100.0)
    c.add_close("SPY", "2025-01-17", 102.0)
    c.set_iv("SPY", "2025-01-13", dte=4, vol50=20.0)

    # Week 3: -3%
    c.add_close("SPY", "2025-01-20", 100.0)
    c.add_close("SPY", "2025-01-24", 97.0)
    c.set_iv("SPY", "2025-01-20", dte=4, vol50=20.0)

    out = backtest_weekly_ic_risk(
        c,
        ticker="SPY",
        years=1,
        entry_dow=0,
        widths=[0.8, 1.0],
        today=dt.date(2025, 1, 25),
    )

    assert out["rowsUsed"] == 3
    assert len(out["byWidth"]) == 2

    # Deterministic: widths sorted ascending
    assert out["byWidth"][0]["w"] == 0.8
    assert out["byWidth"][1]["w"] == 1.0


def test_compute_engine2_spx_ic_enabled_flag_and_proxy_fallback():
    c = FakeOratsClient()

    # Provide SPY closes for "today" probe, but not SPX, so engine falls back to SPY.
    c.add_close("SPY", "2024-12-30", 98.0)
    c.add_close("SPY", "2024-12-31", 99.0)
    c.add_close("SPY", "2025-01-02", 99.5)
    c.add_close("SPY", "2025-01-03", 100.0)
    c.add_close("SPY", "2025-02-03", 500.0)

    # Provide one backtest week so payload is non-empty.
    c.add_close("SPY", "2025-01-06", 100.0)
    c.add_close("SPY", "2025-01-10", 101.0)

    flags = FeatureFlags(ENABLE_ENGINE2_SPX_IC=True)
    out = compute_engine2_spx_ic(
        client=c,
        benzinga_client=None,
        flags=flags,
        entry_day="mon",
        years=1,
        widths=[1.0],
        risk_target_breach_pct=25.0,
        seasonality_mode="none",
        today=dt.date(2025, 2, 3),
    )

    assert out["enabled"] is True
    assert out["underlying"]["symbol"] == "SPY"
    assert out["underlying"]["isProxy"] is True


def test_beta_binomial_mean_and_pctile_helpers():
    assert beta_binomial_mean(k=0, n=10, alpha=1, beta=1) == pytest.approx(1 / 12)
    assert beta_binomial_mean(k=10, n=10, alpha=1, beta=1) == pytest.approx(11 / 12)
    assert pctile([1, 2, 3, 4, 5], 0) == 1
    assert pctile([1, 2, 3, 4, 5], 100) == 5
    assert pctile([1, 2, 3, 4, 5], 50) == 3


