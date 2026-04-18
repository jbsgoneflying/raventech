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
import json
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
    user_em_multiple,
)
from backend.engine14.chain_replay import FillModel, expiry_payoff, reprice_ic
from backend.engine14.exit_rules import optimize_exit_rules
from backend.engine14.simulator import (
    AnaloguePath,
    IcScenarioRequest,
    _bootstrap_outcome_ci,
    _build_mtm_timeline,
    _ohlc_mae_proxy_pct,
    _summarize_outcomes,
)
from backend.spx_ic.ohlc import DailyOHLC


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
    """Credit 2.00, net debit to close 1.00 => +50% pnl.

    This regression pins the legacy mid-only math. Fill realism tests
    live in `test_reprice_ic_nbbo_vs_mid_fill_realism`.
    """
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
        fill_model=FillModel(mode="mid"),
    )
    assert priced is not None
    # net_debit = (sp + sc) - (lp + lc) = (0.25+0.25) - (0.10+0.10) = 0.30
    assert priced.net_debit_to_close == pytest.approx(0.30, abs=1e-6)
    assert priced.pnl_vs_credit == pytest.approx(1.70, abs=1e-6)
    assert priced.pnl_pct_of_credit == pytest.approx(85.0, abs=1e-3)


def test_reprice_ic_nbbo_vs_mid_fill_realism():
    """NBBO mode must produce a less-favorable close than pure mid.

    Each synthetic leg is quoted with bid=mid*0.95 and ask=mid*1.05, so
    NBBO close costs (ask on shorts, bid on longs) > mid close on all
    four legs. PnL must therefore be strictly lower under NBBO.
    """
    chain = _synthetic_chain_rows(
        trade_date="2025-06-03", expiry="2025-06-06", spot=5800.0,
        strikes=[5600.0, 5700.0, 5800.0, 5900.0, 6000.0],
        put_mid_fn=lambda k: 0.25 if k == 5700.0 else (0.10 if k == 5600.0 else 0.50),
        call_mid_fn=lambda k: 0.25 if k == 5900.0 else (0.10 if k == 6000.0 else 0.50),
    )
    mid = reprice_ic(
        chain=chain,
        short_put_strike=5700.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00, snap_max_pts=5.0,
        fill_model=FillModel(mode="mid"),
    )
    nbbo = reprice_ic(
        chain=chain,
        short_put_strike=5700.0, long_put_strike=5600.0,
        short_call_strike=5900.0, long_call_strike=6000.0,
        entry_credit=2.00, snap_max_pts=5.0,
        fill_model=FillModel(mode="nbbo"),
    )
    assert mid is not None and nbbo is not None
    # Buying back shorts at ask + selling longs at bid widens the debit.
    assert nbbo.net_debit_to_close > mid.net_debit_to_close + 1e-9
    assert nbbo.pnl_pct_of_credit < mid.pnl_pct_of_credit - 1e-6
    assert nbbo.fill_mode == "nbbo"
    assert mid.fill_mode == "mid"
    # Leg-level fill source is recorded.
    assert nbbo.short_put.fill_source == "nbbo"
    assert nbbo.long_call.fill_source == "nbbo"
    # Expected NBBO math (hand-check):
    #   sp ask=0.2625, sc ask=0.2625, lp bid=0.095, lc bid=0.095
    #   debit = (0.2625+0.2625) - (0.095+0.095) = 0.335
    assert nbbo.net_debit_to_close == pytest.approx(0.335, abs=1e-6)


def test_reprice_ic_mid_penalty_falls_between_mid_and_nbbo():
    """mid_penalty with small penalty should sit between pure mid and NBBO."""
    chain = _synthetic_chain_rows(
        trade_date="2025-06-03", expiry="2025-06-06", spot=5800.0,
        strikes=[5600.0, 5700.0, 5800.0, 5900.0, 6000.0],
        put_mid_fn=lambda k: 0.25 if k == 5700.0 else (0.10 if k == 5600.0 else 0.50),
        call_mid_fn=lambda k: 0.25 if k == 5900.0 else (0.10 if k == 6000.0 else 0.50),
    )
    mid = reprice_ic(chain=chain, short_put_strike=5700.0, long_put_strike=5600.0,
                    short_call_strike=5900.0, long_call_strike=6000.0,
                    entry_credit=2.0, snap_max_pts=5.0, fill_model=FillModel("mid"))
    penalty = reprice_ic(chain=chain, short_put_strike=5700.0, long_put_strike=5600.0,
                        short_call_strike=5900.0, long_call_strike=6000.0,
                        entry_credit=2.0, snap_max_pts=5.0,
                        fill_model=FillModel("mid_penalty", penalty_pct=25.0))
    nbbo = reprice_ic(chain=chain, short_put_strike=5700.0, long_put_strike=5600.0,
                     short_call_strike=5900.0, long_call_strike=6000.0,
                     entry_credit=2.0, snap_max_pts=5.0, fill_model=FillModel("nbbo"))
    assert mid is not None and penalty is not None and nbbo is not None
    assert mid.net_debit_to_close < penalty.net_debit_to_close < nbbo.net_debit_to_close


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


def test_count_trading_sessions_handles_future_dates():
    """Regression: when entry/expiry are beyond the historical bar series,
    we must still return a session count that matches what the window
    enumerator will emit for the same weekday shape. Otherwise the ±2
    DTE filter drops every real analogue and we return 0 results."""
    from backend.engine14.simulator import _count_trading_sessions

    # Historical series ends on a Friday before the user's trade.
    hist = {"2026-04-13": 5000.0, "2026-04-14": 5010.0, "2026-04-15": 5020.0,
            "2026-04-16": 5030.0, "2026-04-17": 5040.0}

    # Mon -> Fri of next week (both future): expect 5 sessions, not 1.
    assert _count_trading_sessions("2026-04-20", "2026-04-24", hist) == 5

    # Fri (historical) -> Mon (future overnight): expect 2 sessions.
    assert _count_trading_sessions("2026-04-17", "2026-04-20", hist) == 2

    # Same-day degenerate: still returns 1.
    assert _count_trading_sessions("2026-04-17", "2026-04-17", hist) == 1

    # Weekend-only range in the future: heuristic says 0 → floored to 1.
    assert _count_trading_sessions("2026-04-25", "2026-04-26", hist) == 1


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


def test_user_em_multiple_is_average_abs_sigma():
    # Spot=100, EM=1% => every 1% from spot is 1σ.
    # short_put at 98  -> |z|=2.0, short_call at 103 -> |z|=3.0 -> avg 2.5.
    z = user_em_multiple(user_spot=100.0, user_em_pct=1.0,
                        short_put=98.0, short_call=103.0)
    assert z == pytest.approx(2.5, abs=1e-6)
    # Symmetric 1.5σ IC.
    z2 = user_em_multiple(user_spot=5800.0, user_em_pct=1.0,
                         short_put=5713.0, short_call=5887.0)
    assert z2 == pytest.approx(1.5, abs=1e-3)
    # Invalid inputs return None.
    assert user_em_multiple(user_spot=0.0, user_em_pct=1.0,
                            short_put=98.0, short_call=103.0) is None
    assert user_em_multiple(user_spot=100.0, user_em_pct=0.0,
                            short_put=98.0, short_call=103.0) is None


def test_filter_analogues_em_multiple_filter_drops_out_of_range():
    """Analogues whose option chain doesn't cover the user's |z| band
    must be rejected when the EM-multiple filter is enabled."""
    def win(entry: str, coverage):
        return AnalogueWindow(
            entry_date=entry, expiry_date=entry, dte_sessions=5, dte_calendar_days=4,
            entry_close=5800.0, entry_em_pct=1.0, entry_iv_pct=18.0,
            rv20=0.15, rv20_pct=0.4, regime_bucket="MODERATE",
            season={"quarter": "Q1", "month": "01", "isSummer": "NO", "isOpex": "NO"},
            short_strike_em_coverage=coverage,
        )
    universe = [
        win("2024-01-08", (0.5, 2.0)),   # covers z in [0.5, 2.0]
        win("2024-01-15", (0.25, 1.5)),  # covers [0.25, 1.5]
        win("2024-01-22", (3.0, 5.0)),   # far outside user's 1.5σ IC
        win("2024-01-29", None),         # no coverage info -> admitted
    ]
    # Filter OFF: all four pass the regime/DTE gate.
    crit_off = MatchCriteria(
        target_regime="MODERATE", target_dte_sessions=5, regime_bucket_tol=12.0,
        season_mode="none",
        target_em_multiple=1.5, em_multiple_tol=0.25,
        enable_em_multiple_filter=False,
    )
    assert len(filter_analogues(universe, criteria=crit_off)) == 4

    # Filter ON: the "3-5σ coverage" analogue should be dropped; "None" admitted.
    crit_on = MatchCriteria(
        target_regime="MODERATE", target_dte_sessions=5, regime_bucket_tol=12.0,
        season_mode="none",
        target_em_multiple=1.5, em_multiple_tol=0.25,
        enable_em_multiple_filter=True,
    )
    kept = filter_analogues(universe, criteria=crit_on)
    kept_dates = sorted(w.entry_date for w in kept)
    assert "2024-01-22" not in kept_dates
    assert "2024-01-29" in kept_dates  # None coverage admitted (no info to reject)
    assert len(kept) == 3


# ---------------------------------------------------------------------------
# MAE proxy (Phase A2)
# ---------------------------------------------------------------------------

def test_ohlc_mae_proxy_is_more_conservative_than_eod():
    """A day whose intraday low breached the short-put strike should yield
    a worse (more-negative) MAE proxy than the EOD MAE that missed it."""
    # Short put 5700 / long put 5600 / short call 5900 / long call 6000.
    # Entry credit 2.00, so a full -100% = -$2.00 / credit.
    mapped = (5700.0, 5600.0, 5900.0, 6000.0)
    # Day 1: underlying closed at 5805 (no breach), but traded down to 5650
    # intraday -- below the short put by $50 => payoff loses ~ (50 - 2.0*credit).
    # The OHLC proxy treats that low as if it had been expiry.
    ohlc = {
        "2024-01-09": DailyOHLC(trade_date="2024-01-09", open=5800.0,
                                high=5820.0, low=5650.0, close=5805.0),
        "2024-01-10": DailyOHLC(trade_date="2024-01-10", open=5805.0,
                                high=5815.0, low=5790.0, close=5810.0),
    }
    eod_mae_pct = -5.0   # EOD close was benign -> tiny adverse excursion.
    proxy_pct = _ohlc_mae_proxy_pct(
        ohlc_by_date=ohlc,
        trade_days=["2024-01-09", "2024-01-10"],
        mapped_strikes=mapped,
        entry_credit=2.00,
        entry_eod_mae=eod_mae_pct,
    )
    # Proxy must be strictly worse than the EOD MAE (more negative).
    assert proxy_pct < eod_mae_pct
    # At the 5650 low, expiry_payoff = credit - (5700-5650) = 2.00 - 50 = -48.
    # -48 / 2.00 * 100 = -2400%. Verify the proxy captures a deep drawdown.
    assert proxy_pct <= -100.0


def test_ohlc_mae_proxy_falls_back_when_no_bars():
    """When there are no OHLC bars for the replay window we return the
    EOD MAE untouched (no spurious conservatism)."""
    mapped = (5700.0, 5600.0, 5900.0, 6000.0)
    proxy_pct = _ohlc_mae_proxy_pct(
        ohlc_by_date={},
        trade_days=["2024-01-09", "2024-01-10"],
        mapped_strikes=mapped,
        entry_credit=2.00,
        entry_eod_mae=-15.0,
    )
    assert proxy_pct == pytest.approx(-15.0, abs=1e-9)


# ---------------------------------------------------------------------------
# Phase C2 — KNN regime match
# ---------------------------------------------------------------------------

def _mk_window(entry: str, regime: str, *, dte: int = 5, q: str = "Q1") -> AnalogueWindow:
    return AnalogueWindow(
        entry_date=entry, expiry_date=entry,
        dte_sessions=dte, dte_calendar_days=4,
        entry_close=5800.0, entry_em_pct=1.0, entry_iv_pct=18.0,
        rv20=0.15, rv20_pct=0.4, regime_bucket=regime,
        season={"quarter": q, "month": "01", "isSummer": "NO", "isOpex": "NO"},
    )


def test_knn_regime_picks_closest_feature_vector(tmp_path, monkeypatch):
    """Feature-space KNN must rank analogues by weighted L2 distance,
    overriding the legacy RV20 bucket gate."""
    from backend.engine14 import regime_features as rf
    from backend.engine14.analogue_matcher import filter_analogues as _fa
    from backend.engine14.regime_knn import knn_top_n

    # Four windows — two structurally similar to user (vix≈15), two that are
    # nothing alike (vix=30). Bucket labels intentionally "wrong" so the
    # legacy gate would keep the wrong ones.
    universe = [
        _mk_window("2024-01-08", "MODERATE"),
        _mk_window("2024-01-15", "MODERATE"),
        _mk_window("2024-01-22", "MODERATE"),
        _mk_window("2024-01-29", "MODERATE"),
    ]
    cand_features = {
        "2024-01-08": rf.RegimeFeatures(trade_date="2024-01-08", vix=15.0, vix9d=14.0,
                                        vvix=85.0, term_slope=-1.0, rv20=0.12,
                                        credit_stress_score=45.0),
        "2024-01-15": rf.RegimeFeatures(trade_date="2024-01-15", vix=30.0, vix9d=28.0,
                                        vvix=120.0, term_slope=-2.0, rv20=0.25,
                                        credit_stress_score=90.0),
        "2024-01-22": rf.RegimeFeatures(trade_date="2024-01-22", vix=16.0, vix9d=15.2,
                                        vvix=88.0, term_slope=-0.8, rv20=0.13,
                                        credit_stress_score=47.0),
        "2024-01-29": rf.RegimeFeatures(trade_date="2024-01-29", vix=31.0, vix9d=29.0,
                                        vvix=125.0, term_slope=-2.2, rv20=0.28,
                                        credit_stress_score=92.0),
    }
    user = rf.RegimeFeatures(trade_date="2025-01-06", vix=15.5, vix9d=14.5,
                             vvix=86.0, term_slope=-1.0, rv20=0.12,
                             credit_stress_score=48.0)

    crit = MatchCriteria(
        target_regime="MODERATE", target_dte_sessions=5,
        regime_bucket_tol=12.0, season_mode="none",
        enable_knn_regime=True, knn_top_n=2,
    )
    kept, quality = _fa(
        universe, criteria=crit,
        user_features=user, candidate_features=cand_features,
        return_match_quality=True,
    )
    assert [w.entry_date for w in kept] == ["2024-01-08", "2024-01-22"]
    assert quality is not None
    assert quality["source"] == "knn"
    assert quality["kKnn"] == 2
    # Distances are non-negative, ordered ascending on the returned list.
    scores = knn_top_n(user=user, candidates=cand_features, k=4)
    assert all(scores[i].distance <= scores[i + 1].distance for i in range(len(scores) - 1))


def test_knn_regime_falls_back_to_bucket_when_features_missing(tmp_path):
    """Analogues without a features row should pass through the legacy
    bucket gate instead of being silently dropped."""
    from backend.engine14 import regime_features as rf
    from backend.engine14.analogue_matcher import filter_analogues as _fa

    universe = [
        _mk_window("2024-01-08", "MODERATE"),
        _mk_window("2024-01-15", "ELEVATED"),   # neighbor bucket — admitted
        _mk_window("2024-01-22", "EXTREME"),    # far bucket — rejected
    ]
    # Only one window has features; the other two must fall through.
    cand_features = {
        "2024-01-08": rf.RegimeFeatures(trade_date="2024-01-08", vix=15.0, rv20=0.12),
    }
    user = rf.RegimeFeatures(trade_date="2025-01-06", vix=15.0, rv20=0.12)

    crit = MatchCriteria(
        target_regime="MODERATE", target_dte_sessions=5,
        regime_bucket_tol=12.0, season_mode="none",
        enable_knn_regime=True, knn_top_n=10,
    )
    kept, quality = _fa(
        universe, criteria=crit,
        user_features=user, candidate_features=cand_features,
        return_match_quality=True,
    )
    dates = sorted(w.entry_date for w in kept)
    assert "2024-01-08" in dates  # KNN-scored
    assert "2024-01-15" in dates  # bucket fallback (MODERATE neighbor ELEVATED)
    assert "2024-01-22" not in dates  # EXTREME is >1 bucket away
    assert quality["source"] == "knn"
    assert quality["kBucketFallback"] == 1
    assert quality["kKnn"] == 1


# ---------------------------------------------------------------------------
# Phase C1 — regime features store
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_regime_db(tmp_path, monkeypatch):
    from backend.engine14 import regime_features as rf
    db = tmp_path / "regime_features.db"
    monkeypatch.setattr(rf, "_resolve_db_path", lambda: str(db))
    yield str(db)


def test_regime_features_upsert_and_range(tmp_regime_db):
    from backend.engine14 import regime_features as rf

    rows = [
        rf.RegimeFeatures(
            trade_date="2025-06-02", spx_close=5800.0,
            vix=14.1, vix9d=13.2, vvix=85.0, term_slope=-0.9,
            rv20=0.12, credit_stress_label="Neutral", credit_stress_score=50.0,
        ),
        rf.RegimeFeatures(
            trade_date="2025-06-03", spx_close=5820.0,
            vix=14.5, vix9d=13.5, vvix=84.0, term_slope=-1.0,
            rv20=0.12, credit_stress_label="Risk-On", credit_stress_score=35.0,
        ),
        rf.RegimeFeatures(
            trade_date="2025-06-04", spx_close=5780.0,
            vix=16.1, vix9d=16.5, vvix=92.0, term_slope=0.4,
            rv20=0.13, credit_stress_label="Risk-Off", credit_stress_score=72.0,
        ),
    ]
    n = rf.upsert_features_many(rows)
    assert n == 3

    fetched = rf.fetch_features("2025-06-03")
    assert fetched is not None
    assert fetched.vix == pytest.approx(14.5)
    assert fetched.credit_stress_label == "Risk-On"
    assert fetched.term_slope == pytest.approx(-1.0)
    # Feature vector exposes the numeric slice used by KNN.
    vec = fetched.feature_vector()
    assert vec[0] == 14.5 and vec[1] == 13.5 and vec[2] == 84.0
    assert vec[4] == pytest.approx(0.12)

    # Range query sorts ascending.
    window = rf.fetch_features_range(start="2025-06-02", end="2025-06-03")
    assert [r.trade_date for r in window] == ["2025-06-02", "2025-06-03"]

    # Upsert is idempotent and updates values in place.
    rf.upsert_features(rf.RegimeFeatures(
        trade_date="2025-06-03", spx_close=5825.0,
        vix=14.8, vix9d=13.9, vvix=83.5, term_slope=-0.9, rv20=0.12,
        credit_stress_label="Risk-On", credit_stress_score=33.0,
    ))
    re_fetched = rf.fetch_features("2025-06-03")
    assert re_fetched is not None and re_fetched.vix == pytest.approx(14.8)


def test_regime_features_coverage_reports_field_pct(tmp_regime_db):
    from backend.engine14 import regime_features as rf

    rf.upsert_features(rf.RegimeFeatures(trade_date="2025-06-02", vix=14.0, rv20=0.1))
    rf.upsert_features(rf.RegimeFeatures(trade_date="2025-06-03", vix=14.5, vix9d=13.7))
    rf.upsert_features(rf.RegimeFeatures(trade_date="2025-06-04"))   # all-NULL

    cov = rf.coverage()
    assert cov["daysCovered"] == 3
    assert cov["firstDate"] == "2025-06-02"
    assert cov["lastDate"] == "2025-06-04"
    # 2 of 3 have vix, 1 of 3 has vix9d.
    assert cov["fieldCoverage"]["vix"] == pytest.approx(66.7, abs=0.1)
    assert cov["fieldCoverage"]["vix9d"] == pytest.approx(33.3, abs=0.1)
    assert cov["fieldCoverage"]["creditStress"] == 0.0


def test_compute_rv20_matches_manual_stdev(tmp_regime_db):
    from backend.engine14 import regime_features as rf
    import math
    import statistics as _stats

    # Construct a 21-point close series with known log-return stdev.
    closes = [100.0]
    for i in range(1, 21):
        closes.append(closes[-1] * (1.0 + (0.01 if i % 2 == 0 else -0.01)))
    rv = rf._compute_rv20(closes)
    # Manual annualized stdev of log-returns.
    logs = [math.log(closes[i] / closes[i - 1]) for i in range(1, 21)]
    expected = _stats.stdev(logs) * math.sqrt(252.0)
    assert rv == pytest.approx(expected, abs=1e-12)


# ---------------------------------------------------------------------------
# Phase B — modifier coefficients loader
# ---------------------------------------------------------------------------

def test_modifier_coefficients_fallback_when_missing(tmp_path, monkeypatch):
    """If the JSON doesn't exist we fall back to hand-coded defaults and
    still surface the hand-coded tables to callers."""
    from backend.engine14 import conditioning

    conditioning._reset_coefficients_cache_for_tests()
    monkeypatch.setenv("ENGINE14_MODIFIER_COEFFICIENTS_PATH",
                       str(tmp_path / "does-not-exist.json"))
    try:
        coeffs = conditioning.load_modifier_coefficients(force_reload=True)
        # Hand-coded fallback must carry the same 13 calendar keywords.
        kws = coeffs["calendar"]["keywords"]
        assert len(kws) >= 13
        assert all(r.get("source") == "hand_coded" for r in kws)
        # creditStress must carry Neutral bucket.
        assert "Neutral" in coeffs["creditStress"]
        # Classifier still returns the seeded FOMC row.
        cls = conditioning._classify_event("FOMC Rate Decision")
        assert cls is not None
        sev, bump, wr = cls
        assert sev == "extreme"
        assert bump == pytest.approx(0.45, abs=1e-6)
    finally:
        conditioning._reset_coefficients_cache_for_tests()


def test_modifier_coefficients_empirical_overrides_hand_coded(tmp_path, monkeypatch):
    """A written JSON must override the hand-coded defaults used by
    `_classify_event` and `_credit_stress_row`."""
    from backend.engine14 import conditioning

    path = tmp_path / "coeffs.json"
    payload = {
        "version": 1,
        "generator": "test",
        "generatedAt": "2026-04-18T00:00:00Z",
        "calendar": {
            "keywords": [
                # Empirically "learned" that FOMC is LESS scary than expected.
                {"keyword": "FOMC", "severity": "elevated",
                 "tailBump": 0.11, "wrShift": -1.1,
                 "source": "empirical", "n": 42},
            ],
            "tailBumpCapTotal": 1.2,
            "wrShiftFloorTotal": -18.0,
        },
        "dealerGamma": {
            "NEUTRAL": {"tailMult": 1.0, "wrShift": 0.0, "severity": "none",
                        "source": "hand_coded", "n": 0},
        },
        "creditStress": {
            "Stressed": {"tailMult": 1.77, "wrShift": -7.7, "severity": "extreme",
                         "source": "empirical", "n": 55},
        },
        "gapRegime": {
            "extreme": {"absGapFloor": 2.5, "tailMult": 1.99, "wrShift": -9.9,
                        "severity": "extreme", "source": "empirical", "n": 88},
        },
    }
    path.write_text(json.dumps(payload))
    monkeypatch.setenv("ENGINE14_MODIFIER_COEFFICIENTS_PATH", str(path))

    conditioning._reset_coefficients_cache_for_tests()
    try:
        # calendar: learned FOMC replaces hand-coded 0.45 → 0.11.
        cls = conditioning._classify_event("FOMC Rate Decision")
        assert cls is not None
        sev, bump, wr = cls
        assert sev == "elevated"
        assert bump == pytest.approx(0.11, abs=1e-6)
        assert wr == pytest.approx(-1.1, abs=1e-6)
        # credit stress: Stressed becomes more penal than seed.
        sev, tail, wrs = conditioning._credit_stress_row("Stressed")
        assert sev == "extreme"
        assert tail == pytest.approx(1.77, abs=1e-6)
        assert wrs == pytest.approx(-7.7, abs=1e-6)
        # gap regime: extreme bucket picks up new values.
        sev, tail, wrs = conditioning._gap_regime_row(abs_pct=3.0)
        assert sev == "extreme"
        assert tail == pytest.approx(1.99, abs=1e-6)
        assert wrs == pytest.approx(-9.9, abs=1e-6)
    finally:
        conditioning._reset_coefficients_cache_for_tests()


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
    # Grid must stay under the ~50-cell cap.
    assert len(out["grid"]) <= 50
    assert out["gridSize"] == len(out["grid"])
    assert out["extendedGrid"] is True
    # Best rule should pick a profit target <= 50% (35 or 50 are the base options).
    assert out["recommendedProfitTarget"] <= 50.0
    assert out["deltaFromDefault"]["avgPnlPct"] > 0
    assert out["deltaFromDefault"]["winRatePct"] > 0
    # New fields exist even when unused.
    for k in ("recommendedPerDtePt", "recommendedTrailStopPct", "recommendedTimeStopDte"):
        assert k in out


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
    assert out["gridSize"] == 0
    assert out["recommendedPerDtePt"] is None


def test_optimize_exit_rules_grid_respects_cell_cap():
    """The extended grid must stay within the requested cap even with all axes on."""
    from backend.engine14.exit_rules import _build_grid
    cells_default = _build_grid(extended_grid=True, max_cells=50)
    assert len(cells_default) <= 50
    cells_small = _build_grid(extended_grid=True, max_cells=20)
    assert len(cells_small) <= 20
    # Every baseline (None-only) combination must be preserved.
    baselines = [c for c in cells_small if c[2] is None and c[3] is None and c[4] is None]
    assert len(baselines) == 3 * 3  # 3 pt × 3 sl baseline grid
    cells_flat = _build_grid(extended_grid=False, max_cells=50)
    assert len(cells_flat) == 9  # 3×3 baseline only


def test_optimize_exit_rules_trail_stop_locks_in_runups():
    """A trailing stop should materially beat a flat default when paths
    run up then give everything back."""
    # Each path peaks at +55% then collapses to -45% at expiry.
    paths = [
        _path([(5, 0.0), (4, 25.0), (3, 55.0), (2, 30.0), (1, -45.0)])
        for _ in range(8)
    ]
    out = optimize_exit_rules(
        paths=paths, default_profit_target_pct=75.0, default_stop_loss_pct=200.0,
        extended_grid=True,
    )
    # Either profit-target or trailing-stop must fire — avg P&L > default
    # (which holds to expiry and loses 45%).
    assert out["deltaFromDefault"]["avgPnlPct"] > 0
    # Trailing stop or lowered profit target should be among recommendations.
    recommended = (
        out["recommendedTrailStopPct"] is not None
        or out["recommendedProfitTarget"] < 75.0
    )
    assert recommended


def test_optimize_exit_rules_time_stop_beats_holding_to_expiry():
    """A time stop at 1 DTE should dominate when all paths crater on the last day."""
    paths = [
        _path([(5, 0.0), (4, 10.0), (3, 15.0), (2, 12.0), (1, -40.0)])
        for _ in range(8)
    ]
    out = optimize_exit_rules(
        paths=paths, default_profit_target_pct=75.0, default_stop_loss_pct=300.0,
        extended_grid=True,
    )
    assert out["deltaFromDefault"]["avgPnlPct"] > 0
    # Either the time stop fires directly, or a tight pt/trail catches it.
    assert (
        out["recommendedTimeStopDte"] is not None
        or out["recommendedProfitTarget"] < 75.0
        or out["recommendedTrailStopPct"] is not None
    )


def test_bs_greeks_signs_and_magnitudes():
    from backend.engine14.greeks import bs_greeks
    atm_call = bs_greeks(spot=5800.0, strike=5800.0, years_to_expiry=5/365,
                         iv=0.18, is_call=True)
    atm_put  = bs_greeks(spot=5800.0, strike=5800.0, years_to_expiry=5/365,
                         iv=0.18, is_call=False)
    # ATM call delta ≈ 0.5, put delta ≈ -0.5.
    assert 0.45 < atm_call.delta < 0.55
    assert -0.55 < atm_put.delta < -0.45
    # Gamma positive and identical for ATM call & put (put-call symmetry).
    assert atm_call.gamma > 0
    assert abs(atm_call.gamma - atm_put.gamma) < 1e-9
    # Theta negative for long options (positive in our convention means long-theta).
    assert atm_call.theta < 0
    assert atm_put.theta  < 0
    # Vega positive, same for ATM call/put.
    assert atm_call.vega > 0
    assert abs(atm_call.vega - atm_put.vega) < 1e-9


def test_ic_net_greeks_short_position_is_negative_gamma_and_positive_theta():
    from backend.engine14.greeks import ic_net_greeks
    g = ic_net_greeks(
        spot=5800.0, iv=0.18, years_to_expiry=5/365,
        short_put_strike=5700.0, long_put_strike=5650.0,
        short_call_strike=5900.0, long_call_strike=5950.0,
    )
    # Short IC: short gamma (bad when things move), long theta (good when time passes).
    assert g.gamma < 0
    assert g.theta > 0
    # Short vega (bad when vol goes up).
    assert g.vega < 0


def test_attribute_path_sums_to_realized_pnl():
    from backend.engine14.greeks import attribute_path
    att = attribute_path(
        entry_date="2024-01-08", entry_credit=1.50,
        entry_spot=5800.0, exit_spot=5820.0,
        entry_iv=0.18, exit_iv=0.20,
        days_held=3, years_to_expiry=5/365,
        mapped_strikes=(5700.0, 5650.0, 5900.0, 5950.0),
        realized_pnl_pct=45.0,
    )
    # Residual = realized - (delta+gamma+theta+vega). Total_pct is exactly realized.
    assert att.total_pct == 45.0
    reconstructed = att.delta_pct + att.gamma_pct + att.theta_pct + att.vega_pct + att.residual_pct
    assert reconstructed == pytest.approx(45.0, abs=0.1)


def test_aggregate_attribution_produces_shares():
    from backend.engine14.greeks import aggregate_attribution, PathAttribution
    parts = [
        PathAttribution("a", delta_pct=-5.0, gamma_pct=-2.0, theta_pct=40.0,
                        vega_pct=-3.0, residual_pct=10.0, total_pct=40.0),
        PathAttribution("b", delta_pct=2.0, gamma_pct=-1.0, theta_pct=30.0,
                        vega_pct=-4.0, residual_pct=3.0, total_pct=30.0),
    ]
    out = aggregate_attribution(parts)
    assert out["n"] == 2
    assert out["thetaPct"] == pytest.approx((40 + 30) / 2, abs=0.1)
    shares = out["shareOfAbsPnl"]
    assert 0 <= shares["theta"] <= 100
    # Shares must sum to ~100.
    total = sum(shares.values())
    assert 99.0 <= total <= 101.0


def test_sizing_kelly_matches_closed_form():
    from backend.engine14.sizing import kelly_fraction
    # 60% wins @ +30, 40% losses @ -60. b=0.5, p=0.6, raw=(0.6*1.5-1)/0.5=-0.2 -> 0.
    pnls = [30.0] * 6 + [-60.0] * 4
    out = kelly_fraction(pnls)
    assert out["fraction"] == 0.0
    # Flip asymmetry: 60% wins @ +50, 40% losses @ -25 -> b=2, raw=(0.6*3-1)/2=0.4
    # Clamped to 0.25 then halved to 0.125 for half-Kelly.
    pnls = [50.0] * 6 + [-25.0] * 4
    out = kelly_fraction(pnls, half_kelly=True)
    assert out["fraction"] == pytest.approx(0.125, abs=1e-3)
    assert out["clamp"] is True
    assert out["halfKelly"] is True


def test_sizing_fixed_fractional_caps_worst_case_loss():
    from backend.engine14.sizing import fixed_fractional
    # worst loss 50% of credit, credit/equity=5% -> worst_equity=2.5%.
    # risk/trade=2.5% -> size = 2.5 / 2.5 = 1.0.
    pnls = [40.0, 30.0, -50.0, 20.0]
    out = fixed_fractional(pnls, risk_per_trade_pct=2.5, credit_to_equity_pct=5.0)
    assert out["fraction"] == pytest.approx(1.0, abs=1e-3)
    assert out["worstLossPctCredit"] == 50.0
    # Halve risk appetite -> size halves too.
    out = fixed_fractional(pnls, risk_per_trade_pct=1.25, credit_to_equity_pct=5.0)
    assert out["fraction"] == pytest.approx(0.5, abs=1e-3)


def test_sizing_empirical_max_dd_counts_consecutive_losses():
    from backend.engine14.sizing import empirical_max_dd, _max_consecutive_loss
    # Sequence with a 3-trade losing run of -20, -30, -10 -> dd=60.
    pnls = [10.0, -20.0, -30.0, -10.0, 5.0, -5.0]
    assert _max_consecutive_loss(pnls) == 60.0
    # credit/equity=5% -> dd_equity = 60% * 5% = 3%; max_dd_pct=10% -> size = 10/3 -> 1 clamp
    out = empirical_max_dd(pnls, max_drawdown_pct=10.0, credit_to_equity_pct=5.0)
    assert out["empiricalDdPctCredit"] == 60.0
    assert 0.0 < out["fraction"] <= 1.0


def test_sizing_consensus_is_minimum_and_applies_account_equity():
    from backend.engine14.sizing import compute_sizing

    class _P:
        def __init__(self, pnl): self.exit_pnl_pct = pnl

    pnls = [30, 40, -20, 25, -50, 35, 20, -15, 45, -10]
    paths = [_P(p) for p in pnls]
    out = compute_sizing(paths, credit_to_equity_pct=5.0, risk_per_trade_pct=2.0,
                         max_drawdown_pct=8.0, account_equity_usd=100000.0)
    assert out["n"] == len(pnls)
    parts = [out["kelly"]["fraction"], out["fixedFractional"]["fraction"],
             out["empiricalMaxDd"]["fraction"]]
    assert out["consensusFraction"] == pytest.approx(min(parts), abs=1e-6)
    assert out["recommendedAllocationUsd"] == pytest.approx(100000.0 * min(parts), abs=1e-2)
    assert out["riskPerTradeUsd"] == pytest.approx(2000.0, abs=1e-2)


def test_per_dte_profit_target_clamps_and_scales():
    """_per_dte_target must clamp to [10, 95] and scale linearly in between."""
    from backend.engine14.exit_rules import _per_dte_target
    assert _per_dte_target(dte_remaining=0, base_pt=50.0, slope_per_dte=5.0) == 50.0
    assert _per_dte_target(dte_remaining=4, base_pt=50.0, slope_per_dte=5.0) == 70.0
    # Clamp at 95 upper.
    assert _per_dte_target(dte_remaining=50, base_pt=50.0, slope_per_dte=10.0) == 95.0
    # Clamp at 10 lower.
    assert _per_dte_target(dte_remaining=1, base_pt=5.0, slope_per_dte=-10.0) == 10.0


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


def test_bootstrap_outcome_ci_flags_thin_sample_and_brackets_point_estimate():
    # Build a concrete sample: 6 fullCollect, 3 earlyTarget, 1 whiteKnuckle → N=10 (thin).
    paths = []
    for i in range(6):
        paths.append(AnaloguePath(entry_date=f"fc-{i}", expiry_date="x",
            dte_sessions=5, mapped_strikes=(0, 0, 0, 0),
            daily_pnl_pct=[(0, 60.0)], outcome="fullCollect",
            exit_day=0, exit_pnl_pct=60.0, max_adverse_excursion_pct=-5.0, breached=False))
    for i in range(3):
        paths.append(AnaloguePath(entry_date=f"et-{i}", expiry_date="x",
            dte_sessions=5, mapped_strikes=(0, 0, 0, 0),
            daily_pnl_pct=[(0, 20.0)], outcome="earlyTarget",
            exit_day=1, exit_pnl_pct=20.0, max_adverse_excursion_pct=-15.0, breached=False))
    paths.append(AnaloguePath(entry_date="wk-0", expiry_date="x",
        dte_sessions=5, mapped_strikes=(0, 0, 0, 0),
        daily_pnl_pct=[(0, -50.0)], outcome="whiteKnuckle",
        exit_day=2, exit_pnl_pct=-50.0, max_adverse_excursion_pct=-70.0, breached=False))

    ci = _bootstrap_outcome_ci(paths, iterations=400, confidence=0.90, seed=42)

    # Thin-sample flag must fire for N<20.
    assert ci["_meta"]["thinSample"] is True
    assert ci["_meta"]["n"] == 10
    assert ci["_meta"]["iterations"] == 400

    # 90% bracket for the observed 60% fullCollect rate should envelope the point.
    fc = ci["fullCollect"]
    assert fc["pctLow"] <= 60.0 <= fc["pctHigh"]
    # Bracket width must be meaningful for thin samples (no collapse to zero).
    assert fc["pctHigh"] - fc["pctLow"] >= 10.0
    # P&L bracket should contain the sampled avg P&L (60% @ 60, 30% @ 20, 10% @ -50).
    assert fc["pnlLow"] <= 60.0 <= fc["pnlHigh"]


def test_bootstrap_outcome_ci_handles_empty_paths():
    ci = _bootstrap_outcome_ci([], iterations=50)
    assert ci["_meta"]["n"] == 0
    assert ci["_meta"]["thinSample"] is True
    for o in ("earlyTarget", "fullCollect", "whiteKnuckle", "stopOut", "breach"):
        assert ci[o]["pctLow"] == 0.0
        assert ci[o]["pctHigh"] == 0.0


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
