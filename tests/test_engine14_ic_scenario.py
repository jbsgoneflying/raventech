"""Engine 14 — IC Scenario Simulator unit tests.

Golden-payload tests for:
  * chain_cache          (schema roundtrip, upsert, coverage, manifest)
  * chain_replay         (reprice_ic, expiry_payoff, strike snapping)
  * analogue_matcher     (EM-distance strike mapping, weekly window enum,
                           regime bucketing, percentile math)
  * exit_rules           (optimizer grid, no-noise default policy)
  * simulator            (end-to-end run with synthetic chain + bars)

All tests run offline: we monkeypatch the SQLite path into a tmp dir and
stub out the ORATS client.
"""

from __future__ import annotations

import datetime as dt
import math
import os
from pathlib import Path

import pytest

from backend.engine14 import chain_cache
from backend.engine14.analogue_matcher import (
    REGIME_BUCKETS,
    AnalogueWindow,
    MatchCriteria,
    _build_weekly_windows,
    _regime_from_rv_pct,
    build_analogue_universe,
    filter_analogues,
    map_user_strikes_to_analogue,
)
from backend.engine14.chain_replay import expiry_payoff, reprice_ic
from backend.engine14.exit_rules import optimize_exit_rules
from backend.engine14.simulator import (
    AnaloguePath,
    IcScenarioRequest,
    _build_mtm_timeline,
    _summarize_outcomes,
)


# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_chain_db(tmp_path, monkeypatch):
    """Isolate the chain_cache SQLite file to a per-test tmp directory."""
    db = tmp_path / "engine14_chains.db"
    monkeypatch.setattr(chain_cache, "_resolve_db_path", lambda: str(db))
    yield str(db)


def _chain_row(**kw) -> dict:
    """Build a synthetic ORATS-shaped row that _row_to_rec can ingest."""
    defaults = {
        "ticker": "SPX",
        "tradeDate": "2025-06-02",
        "expirDate": "2025-06-06",
        "strike": 5800.0,
        "stockPrice": 5800.0,
        "callBidPrice": 1.0,
        "callAskPrice": 1.2,
        "callMidPrice": 1.1,
        "callMidIv": 0.18,
        "putBidPrice": 1.0,
        "putAskPrice": 1.2,
        "putMidPrice": 1.1,
        "putMidIv": 0.18,
        "callOpenInterest": 10,
        "putOpenInterest": 10,
    }
    defaults.update(kw)
    return defaults


def _build_chain(
    *,
    trade_date: str,
    expiry: str,
    spot: float,
    strikes: list[float],
    iv: float = 0.18,
    put_mid: callable | None = None,
    call_mid: callable | None = None,
) -> list[dict]:
    """Generate a tiny option chain centered on spot. `put_mid`/`call_mid`
    can be callables (strike -> mid) to inject custom pricing for P&L tests."""
    rows = []
    for k in strikes:
        pm = float(put_mid(k)) if put_mid else max(0.05, max(0.0, spot - k) * 0.05 + 1.0)
        cm = float(call_mid(k)) if call_mid else max(0.05, max(0.0, k - spot) * 0.05 + 1.0)
        rows.append(_chain_row(
            tradeDate=trade_date,
            expirDate=expiry,
            strike=float(k),
            stockPrice=float(spot),
            callMidPrice=cm, callBidPrice=cm * 0.95, callAskPrice=cm * 1.05, callMidIv=iv,
            putMidPrice=pm, putBidPrice=pm * 0.95, putAskPrice=pm * 1.05, putMidIv=iv,
        ))
    return rows


# ---------------------------------------------------------------------------
# chain_cache
# ---------------------------------------------------------------------------

def test_chain_cache_roundtrip_and_manifest(tmp_chain_db):
    rows = _build_chain(
        trade_date="2025-06-02",
        expiry="2025-06-06",
        spot=5800.0,
        strikes=[5700.0, 5750.0, 5800.0, 5850.0, 5900.0],
    )
    n = chain_cache.upsert_chain(ticker="SPX", trade_date="2025-06-02", rows=rows)
    assert n == 5

    slice_ = chain_cache.fetch_chain_slice(
        ticker="SPX", trade_date="2025-06-02", expiry="2025-06-06"
    )
    assert [r.strike for r in slice_] == [5700.0, 5750.0, 5800.0, 5850.0, 5900.0]
    r_atm = next(r for r in slice_ if r.strike == 5800.0)
    assert r_atm.call_mid_px() == pytest.approx(1.0)
    assert r_atm.put_mid_px() == pytest.approx(1.0)
    assert r_atm.call_iv == pytest.approx(0.18)

    cov = chain_cache.cache_coverage(ticker="SPX")
    assert cov["daysCovered"] == 1
    assert cov["minDate"] == cov["maxDate"] == "2025-06-02"
    assert cov["totalRows"] == 5

    assert chain_cache.has_trade_date(ticker="SPX", trade_date="2025-06-02") is True
    assert chain_cache.has_trade_date(ticker="SPX", trade_date="1999-01-01") is False

    # Re-upserting the same day should wipe-then-insert idempotently.
    chain_cache.upsert_chain(ticker="SPX", trade_date="2025-06-02", rows=rows[:3])
    s2 = chain_cache.fetch_chain_slice(ticker="SPX", trade_date="2025-06-02", expiry="2025-06-06")
    assert len(s2) == 3

    dates = chain_cache.fetch_cached_trade_dates(ticker="SPX")
    assert dates == ["2025-06-02"]


def test_chain_cache_purge_scoped(tmp_chain_db):
    chain_cache.upsert_chain(
        ticker="SPX", trade_date="2025-06-02",
        rows=_build_chain(trade_date="2025-06-02", expiry="2025-06-06",
                          spot=5800.0, strikes=[5700.0, 5800.0, 5900.0]),
    )
    chain_cache.upsert_chain(
        ticker="SPY", trade_date="2025-06-02",
        rows=_build_chain(trade_date="2025-06-02", expiry="2025-06-06",
                          spot=580.0, strikes=[570.0, 580.0, 590.0]),
    )
    assert chain_cache.cache_coverage(ticker="SPX")["daysCovered"] == 1
    assert chain_cache.cache_coverage(ticker="SPY")["daysCovered"] == 1
    chain_cache.purge(ticker="SPY")
    assert chain_cache.cache_coverage(ticker="SPX")["daysCovered"] == 1
    assert chain_cache.cache_coverage(ticker="SPY")["daysCovered"] == 0


# ---------------------------------------------------------------------------
# chain_replay
# ---------------------------------------------------------------------------

def _synthetic_chain_rows(*, trade_date: str, expiry: str, spot: float, strikes: list[float],
                         put_mid_fn=None, call_mid_fn=None):
    rows_raw = _build_chain(
        trade_date=trade_date, expiry=expiry, spot=spot, strikes=strikes,
        put_mid=put_mid_fn, call_mid=call_mid_fn,
    )
    return [chain_cache._rec_to_chainrow(
        chain_cache._row_to_rec(r, ticker="SPX", trade_date=trade_date)
    ) for r in rows_raw]


def test_reprice_ic_profitable_midlife():
    """Credit 2.00, net debit to close 1.00 => +50% pnl."""
    chain = _synthetic_chain_rows(
        trade_date="2025-06-03", expiry="2025-06-06", spot=5800.0,
        strikes=[5600.0, 5700.0, 5800.0, 5900.0, 6000.0],
        put_mid_fn=lambda k: 0.25 if k == 5700.0 else (0.10 if k == 5600.0 else 0.50),
        call_mid_fn=lambda k: 0.25 if k == 5900.0 else (0.10 if k == 6000.0 else 0.50),
    )
    priced = reprice_ic(
        chain=chain,
        short_put_strike=5700.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00,
        snap_max_pts=5.0,
    )
    assert priced is not None
    # net_debit = (sp + sc) - (lp + lc) = (0.25+0.25) - (0.10+0.10) = 0.30
    assert priced.net_debit_to_close == pytest.approx(0.30, abs=1e-6)
    assert priced.pnl_vs_credit == pytest.approx(1.70, abs=1e-6)
    assert priced.pnl_pct_of_credit == pytest.approx(85.0, abs=1e-3)


def test_reprice_ic_strike_snap_respects_tolerance():
    chain = _synthetic_chain_rows(
        trade_date="2025-06-03", expiry="2025-06-06", spot=5800.0,
        strikes=[5700.0, 5800.0, 5900.0],
    )
    # Request a strike 50pts off listed; max_pts=5 means snap must fail.
    result = reprice_ic(
        chain=chain,
        short_put_strike=5650.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00, snap_max_pts=5.0,
    )
    assert result is None

    # With 100pt tolerance everything snaps to the nearest listed strike
    # and we get a valid price (the snapped long/short may collapse).
    result2 = reprice_ic(
        chain=chain,
        short_put_strike=5650.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00, snap_max_pts=100.0,
    )
    assert result2 is not None
    # Every leg should have snapped to one of the listed strikes.
    for leg in (result2.short_put, result2.long_put, result2.short_call, result2.long_call):
        assert leg.strike_snapped in (5700.0, 5800.0, 5900.0)


def test_expiry_payoff_scenarios():
    # Full collect: spot stays inside short strikes, all legs expire worthless.
    p_full = expiry_payoff(
        expiry_spot=5800.0,
        short_put_strike=5700.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00,
    )
    assert p_full == pytest.approx(2.00, abs=1e-6)

    # Breach put side: spot below short-put, inside wing.
    p_br = expiry_payoff(
        expiry_spot=5650.0,
        short_put_strike=5700.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00,
    )
    # sp_val=50, lp_val=0 -> debit=50; pnl = 2 - 50 = -48
    assert p_br == pytest.approx(-48.0, abs=1e-6)

    # Max loss: spot at/through long-put.
    p_max = expiry_payoff(
        expiry_spot=5500.0,
        short_put_strike=5700.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00,
    )
    # sp_val=200, lp_val=100 -> debit=100; pnl = 2 - 100 = -98
    assert p_max == pytest.approx(-98.0, abs=1e-6)


# ---------------------------------------------------------------------------
# analogue_matcher
# ---------------------------------------------------------------------------

def test_em_distance_strike_mapping_preserves_sigma():
    """Translating a strike in EM-distance units then back should be a fixed point."""
    user_spot = 7100.0
    user_em = 1.2  # 1.2%
    analogue_spot = 5800.0
    analogue_em = 0.8  # 0.8%
    user_strikes = (6950.0, 6900.0, 7250.0, 7300.0)  # sp, lp, sc, lc
    mapped = map_user_strikes_to_analogue(
        user_spot=user_spot, user_em_pct=user_em,
        analogue_spot=analogue_spot, analogue_em_pct=analogue_em,
        user_strikes=user_strikes,
    )
    # Check sigma-distance is invariant for each leg.
    def sigma(K, spot, em):
        return ((K / spot) - 1.0) * 100.0 / em
    for Ku, Ka in zip(user_strikes, mapped):
        # Impl rounds Ka to 2 decimals so ~1e-3 σ tolerance is expected.
        assert sigma(Ku, user_spot, user_em) == pytest.approx(
            sigma(Ka, analogue_spot, analogue_em), abs=1e-2
        )
    # Short put (slightly below spot) must remain below analogue spot.
    assert mapped[0] < analogue_spot
    assert mapped[2] > analogue_spot  # short call above analogue spot
    assert mapped[1] < mapped[0]      # long put below short put
    assert mapped[3] > mapped[2]      # long call above short call


def test_map_user_strikes_rejects_bad_inputs():
    with pytest.raises(ValueError):
        map_user_strikes_to_analogue(
            user_spot=0.0, user_em_pct=1.0,
            analogue_spot=100.0, analogue_em_pct=1.0,
            user_strikes=(95.0, 90.0, 105.0, 110.0),
        )
    with pytest.raises(ValueError):
        map_user_strikes_to_analogue(
            user_spot=100.0, user_em_pct=-1.0,
            analogue_spot=100.0, analogue_em_pct=1.0,
            user_strikes=(95.0, 90.0, 105.0, 110.0),
        )


def test_build_weekly_windows_basic():
    # Mon-Fri trading days for 3 weeks in Jan 2025.
    dates = []
    d = dt.date(2025, 1, 6)  # Monday
    for _ in range(3):
        for off in range(5):
            dates.append(((d + dt.timedelta(days=off)).isoformat(), 100.0 + off))
        d += dt.timedelta(days=7)
    windows = _build_weekly_windows(dates, entry_dow=0)
    # 3 clean Mon->Fri windows.
    assert len(windows) == 3
    for entry, expiry, dte_s, dte_c in windows:
        assert dt.date.fromisoformat(entry).weekday() == 0
        assert dt.date.fromisoformat(expiry).weekday() == 4
        assert dte_s == 5
        assert dte_c == 4


def test_build_matching_windows_fri_to_mon_overnight():
    """Regression: Friday→Monday 1-session trades must produce analogue
    windows (old enumerator collapsed them to empty)."""
    from backend.engine14.analogue_matcher import _build_matching_windows

    dates = []
    d = dt.date(2025, 1, 6)  # Monday
    for _ in range(4):
        for off in range(5):  # Mon..Fri
            dates.append(((d + dt.timedelta(days=off)).isoformat(), 100.0))
        d += dt.timedelta(days=7)

    windows = _build_matching_windows(dates, entry_dow=4, target_dte_calendar=3)
    # Expect a window for each Friday that has a Monday trading day ahead
    # (3 of 4 weeks in this fixture — the final Friday has no follow-on Mon).
    assert len(windows) == 3
    for entry, expiry, dte_s, dte_c in windows:
        assert dt.date.fromisoformat(entry).weekday() == 4  # Fri
        assert dt.date.fromisoformat(expiry).weekday() == 0  # Mon
        assert dte_s == 2  # Fri + Mon inclusive
        assert dte_c == 3


def test_regime_from_rv_pct_boundaries():
    assert _regime_from_rv_pct(0.0) == "LOW"
    assert _regime_from_rv_pct(0.25) == "LOW"
    assert _regime_from_rv_pct(0.40) == "MODERATE"
    assert _regime_from_rv_pct(0.60) == "ELEVATED"
    assert _regime_from_rv_pct(0.99) == "NO_TRADE"


def test_filter_analogues_regime_and_dte():
    def win(entry: str, regime: str, dte_s: int, q: str = "Q1") -> AnalogueWindow:
        return AnalogueWindow(
            entry_date=entry, expiry_date=entry, dte_sessions=dte_s, dte_calendar_days=4,
            entry_close=5800.0, entry_em_pct=1.0, entry_iv_pct=18.0, rv20=0.15, rv20_pct=0.4,
            regime_bucket=regime,
            season={"quarter": q, "month": "01", "isSummer": "NO", "isOpex": "NO"},
        )
    universe = [
        win("2024-01-08", "LOW", 5),
        win("2024-01-15", "MODERATE", 5),
        win("2024-01-22", "ELEVATED", 5),
        win("2024-01-29", "MODERATE", 10),
        win("2024-02-05", "MODERATE", 5, q="Q2"),
    ]
    # Default tol=12 pts -> tol_buckets=1 (adjacent allowed).
    crit = MatchCriteria(target_regime="MODERATE", target_dte_sessions=5,
                         regime_bucket_tol=12.0, season_mode="none")
    keep = filter_analogues(universe, criteria=crit)
    labels = sorted(w.regime_bucket for w in keep)
    # LOW + MODERATE + MODERATE + ELEVATED (all dte=5) = 4 candidates.
    # 10-dte window is excluded (±2 DTE filter).
    assert labels == ["ELEVATED", "LOW", "MODERATE", "MODERATE"]

    # Season filter Q2 only keeps last window.
    crit_q2 = MatchCriteria(target_regime="MODERATE", target_dte_sessions=5,
                            regime_bucket_tol=12.0, season_mode="quarter", season_value="Q2")
    keep_q2 = filter_analogues(universe, criteria=crit_q2)
    assert [w.entry_date for w in keep_q2] == ["2024-02-05"]


# ---------------------------------------------------------------------------
# exit_rules
# ---------------------------------------------------------------------------

def _path(daily: list[tuple[int, float]]) -> AnaloguePath:
    return AnaloguePath(
        entry_date="2024-01-08", expiry_date="2024-01-12",
        dte_sessions=5, mapped_strikes=(5700.0, 5600.0, 5900.0, 6000.0),
        daily_pnl_pct=daily,
        outcome="fullCollect", exit_day=len(daily) - 1,
        exit_pnl_pct=daily[-1][1] if daily else 0.0,
        max_adverse_excursion_pct=min((p for _, p in daily), default=0.0),
        breached=False,
    )


def test_optimize_exit_rules_recommends_when_improvement_clear():
    # Mix of paths: some peak at ~40% then fall to a loss (-30 / -40),
    # some stay modest. A low profit target (25-35%) locks in profits
    # before the late-cycle collapse -> bigger avg AND higher win-rate.
    paths = [
        _path([(5, 0.0), (4, 20.0), (3, 42.0), (2, 15.0), (1, -30.0)]),
        _path([(5, 0.0), (4, 25.0), (3, 40.0), (2, 18.0), (1, -40.0)]),
        _path([(5, 0.0), (4, 10.0), (3, 30.0), (2, 45.0), (1, -20.0)]),
        _path([(5, 0.0), (4, 5.0),  (3, 22.0), (2, 38.0), (1, -15.0)]),
        _path([(5, 0.0), (4, 2.0),  (3, 5.0),  (2, 6.0),  (1, 5.0)]),
        _path([(5, 0.0), (4, 3.0),  (3, 4.0),  (2, 4.5),  (1, 5.0)]),
    ]
    out = optimize_exit_rules(
        paths=paths, default_profit_target_pct=50.0, default_stop_loss_pct=200.0,
    )
    # Grid should cover all default + alternate cells.
    assert len(out["grid"]) == 6 * 6
    # Best rule should pick a profit target <= 45% (25, 35, or 45).
    assert out["recommendedProfitTarget"] <= 45.0
    assert out["deltaFromDefault"]["avgPnlPct"] > 0
    assert out["deltaFromDefault"]["winRatePct"] > 0


def test_optimize_exit_rules_keeps_default_when_no_material_win():
    # All paths trivially finish at +5% — no rule materially beats default.
    paths = [_path([(5, 0.0), (4, 3.0), (3, 4.0), (2, 4.5), (1, 5.0)]) for _ in range(8)]
    out = optimize_exit_rules(
        paths=paths, default_profit_target_pct=50.0, default_stop_loss_pct=200.0,
    )
    assert out["recommendedProfitTarget"] == 50.0
    assert out["recommendedStopLoss"] == 200.0
    assert out["deltaFromDefault"] == {"winRatePct": 0.0, "avgPnlPct": 0.0}


def test_optimize_exit_rules_empty_paths():
    out = optimize_exit_rules(paths=[], default_profit_target_pct=50.0, default_stop_loss_pct=200.0)
    assert out["recommendedProfitTarget"] == 50.0
    assert out["recommendedStopLoss"] == 200.0
    assert out["grid"] == []


# ---------------------------------------------------------------------------
# simulator aggregation helpers (pure)
# ---------------------------------------------------------------------------

def test_summarize_outcomes_counts_and_averages():
    paths = [
        AnaloguePath(entry_date="a", expiry_date="b", dte_sessions=5,
                     mapped_strikes=(0, 0, 0, 0), daily_pnl_pct=[(0, 80.0)],
                     outcome="fullCollect", exit_day=0, exit_pnl_pct=80.0,
                     max_adverse_excursion_pct=-5.0, breached=False),
        AnaloguePath(entry_date="c", expiry_date="d", dte_sessions=5,
                     mapped_strikes=(0, 0, 0, 0), daily_pnl_pct=[(0, -30.0)],
                     outcome="whiteKnuckle", exit_day=3, exit_pnl_pct=-30.0,
                     max_adverse_excursion_pct=-60.0, breached=False),
    ]
    summary = _summarize_outcomes(paths)
    assert summary["fullCollect"]["n"] == 1
    assert summary["whiteKnuckle"]["n"] == 1
    assert summary["fullCollect"]["pct"] == 50.0
    assert summary["whiteKnuckle"]["pct"] == 50.0
    assert summary["fullCollect"]["avgPnlPct"] == 80.0


def test_build_mtm_timeline_orders_entry_to_expiry():
    paths = [
        AnaloguePath(entry_date="a", expiry_date="b", dte_sessions=3,
                     mapped_strikes=(0, 0, 0, 0),
                     daily_pnl_pct=[(2, 10.0), (1, 20.0), (0, 30.0)],
                     outcome="fullCollect", exit_day=2, exit_pnl_pct=30.0,
                     max_adverse_excursion_pct=10.0, breached=False),
        AnaloguePath(entry_date="c", expiry_date="d", dte_sessions=3,
                     mapped_strikes=(0, 0, 0, 0),
                     daily_pnl_pct=[(2, -5.0), (1, 0.0), (0, 40.0)],
                     outcome="fullCollect", exit_day=2, exit_pnl_pct=40.0,
                     max_adverse_excursion_pct=-5.0, breached=False),
    ]
    rows = _build_mtm_timeline(paths)
    # Ordered from highest dte (entry) to 0 (expiry).
    assert [r["dte"] for r in rows] == [2, 1, 0]
    # p50 at dte=0 is median of (30, 40) = 35
    row0 = next(r for r in rows if r["dte"] == 0)
    assert row0["p50"] == pytest.approx(35.0, abs=1e-6)
    assert row0["n"] == 2


# ---------------------------------------------------------------------------
# IcScenarioRequest ergonomics
# ---------------------------------------------------------------------------

def test_ic_scenario_request_dte_and_width():
    req = IcScenarioRequest(
        underlying="SPX", entry_date="2025-06-02", expiry="2025-06-06",
        short_put=5700.0, long_put=5600.0,
        short_call=5900.0, long_call=6000.0,
        credit_received=2.00,
    )
    assert req.dte_calendar() == 4
    assert req.wing_width() == pytest.approx(100.0)
    assert req.strike_tuple() == (5700.0, 5600.0, 5900.0, 6000.0)


# ---------------------------------------------------------------------------
# simulator end-to-end (requires a fake client + seeded cache)
# ---------------------------------------------------------------------------

class _FakeBar:
    def __init__(self, td, close):
        self.trade_date = td
        self.open = close
        self.high = close
        self.low = close
        self.close = close
        self.volume = 1.0
        self.vwap = close


def _generate_trading_days(start: dt.date, end: dt.date) -> list[dt.date]:
    out = []
    d = start
    while d <= end:
        if d.weekday() < 5:
            out.append(d)
        d += dt.timedelta(days=1)
    return out


def test_simulator_runs_end_to_end(tmp_chain_db, monkeypatch):
    """Smoke test: seed synthetic bars + chains for 6 Mon/Fri windows and
    verify run_scenario produces a populated payload."""
    from backend.engine14 import simulator as sim_mod

    # ~8 months of flat SPX bars so the 180-bar guard passes.
    start = dt.date(2024, 10, 1)
    end = dt.date(2025, 6, 6)
    tdays = _generate_trading_days(start, end)
    bars = [_FakeBar(d.isoformat(), 5800.0 + (i % 7) * 2.0) for i, d in enumerate(tdays)]

    monkeypatch.setattr(sim_mod, "fetch_dailies_ohlc_range",
                        lambda client, *, ticker, start, end: bars)

    # Also lock "today" so the lookback window contains all synthetic bars.
    class _FakeDate(dt.date):
        @classmethod
        def today(cls):
            return dt.date(2025, 6, 6)
    monkeypatch.setattr(sim_mod.dt, "date", _FakeDate)

    # Seed chains for the weekly Mon->Fri windows in the lookback. We only
    # need enough filled windows to clear ENGINE14_MIN_ANALOGUES (default 20).
    # To keep the test fast, seed 26 windows: every Monday for 6 months.
    seed_monday = dt.date(2024, 12, 2)
    for w in range(26):
        mon = seed_monday + dt.timedelta(days=7 * w)
        fri = mon + dt.timedelta(days=4)
        for i in range(5):  # 5 days per window
            td = (mon + dt.timedelta(days=i)).isoformat()
            chain = _build_chain(
                trade_date=td, expiry=fri.isoformat(),
                spot=5800.0,
                strikes=[5600.0, 5650.0, 5700.0, 5750.0, 5800.0,
                         5850.0, 5900.0, 5950.0, 6000.0],
                iv=0.18,
                put_mid=lambda k: max(0.05, max(0.0, 5700.0 - k) * 0.05 + 0.50),
                call_mid=lambda k: max(0.05, max(0.0, k - 5900.0) * 0.05 + 0.50),
            )
            chain_cache.upsert_chain(ticker="SPX", trade_date=td, rows=chain)

    # FeatureFlags is frozen; build a relaxed copy for this test so we
    # don't depend on an exact bucket match at the default threshold.
    import dataclasses
    from backend.config import get_flags
    flags = dataclasses.replace(get_flags(), ENGINE14_MIN_ANALOGUES=5)

    req = IcScenarioRequest(
        underlying="SPX",
        entry_date="2025-06-02",  # Monday
        expiry="2025-06-06",
        short_put=5700.0, long_put=5600.0,
        short_call=5900.0, long_call=6000.0,
        credit_received=2.00,
        profit_target_pct=50.0, stop_loss_pct=200.0,
    )

    class _DummyClient:
        pass

    payload = sim_mod.run_scenario(req, client=_DummyClient(), flags=flags)
    assert payload["engine"] == 14
    # Either we got analogues or we got a conditioningNote explaining why.
    if payload["analoguesUsed"] > 0:
        assert "outcomeDistribution" in payload
        assert any(v["n"] >= 0 for v in payload["outcomeDistribution"].values())
        assert isinstance(payload["mtmTimeline"], list)
        assert "exitRulesOptimization" in payload
        assert payload["entryState"]["userEmPct"] > 0
    else:
        assert payload["conditioningNotes"], "empty payload must explain why"
