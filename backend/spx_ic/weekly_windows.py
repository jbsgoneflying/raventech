from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import List, Optional

from backend.orats_client import OratsClient
from backend.spx_ic.ohlc import fetch_close_px, next_trading_day
from backend.spx_ic.utils import _fmt_date


@dataclass(frozen=True)
class WeeklyWindow:
    entry_date: dt.date
    expiry_date: dt.date
    dte_sessions: int
    dte_calendar_days: int


def count_trading_sessions(client: OratsClient, *, ticker: str, start: dt.date, end: dt.date) -> int:
    """Count trading sessions between start and end inclusive (best-effort)."""
    if end < start:
        return 0
    n = 0
    d = start
    while d <= end and n < 30:
        if fetch_close_px(client, ticker=ticker, date=d) is not None:
            n += 1
        d += dt.timedelta(days=1)
    return n


def build_weekly_windows(
    client: OratsClient,
    *,
    ticker: str,
    start: dt.date,
    end: dt.date,
    entry_dow: int,  # 0=Mon
    max_weeks: int = 260,
) -> List[WeeklyWindow]:
    """
    Build (entry_date, expiry_date) weekly windows for IC backtests.
    entry_dow controls which day we enter (Mon/Tue/Wed).
    Expiry is the Friday of the same calendar week.
    """
    d = start
    while d.weekday() != 0:
        d += dt.timedelta(days=1)

    out: List[WeeklyWindow] = []
    while d <= end and len(out) < max_weeks:
        entry_anchor = d + dt.timedelta(days=int(entry_dow))
        expiry_anchor = d + dt.timedelta(days=4)  # Friday
        entry_td = next_trading_day(client, ticker=ticker, date=entry_anchor)
        exp_td = next_trading_day(client, ticker=ticker, date=expiry_anchor)
        if entry_td and exp_td and entry_td < exp_td:
            dte = (exp_td - entry_td).days
            dte_sessions = count_trading_sessions(client, ticker=ticker, start=entry_td, end=exp_td)
            out.append(WeeklyWindow(entry_date=entry_td, expiry_date=exp_td, dte_sessions=int(dte_sessions), dte_calendar_days=int(dte)))
        d += dt.timedelta(days=7)
    return out


def build_weekly_windows_from_trade_dates(
    *,
    trade_dates: List[str],
    start: dt.date,
    end: dt.date,
    entry_dow: int,  # 0=Mon
    max_weeks: int = 260,
) -> List[WeeklyWindow]:
    """
    Build (entry_date, expiry_date) weekly windows without per-day ORATS calls.

    This uses the already-fetched OHLC trade_dates (EOD) to:
    - find the next available trading day on/after entry anchor
    - find the next available trading day on/after Friday anchor
    - count trading sessions via index range (fast)
    """
    if not trade_dates:
        return []

    dates_sorted = sorted([str(d)[:10] for d in trade_dates if d])
    date_set = set(dates_sorted)
    idx = {d: i for i, d in enumerate(dates_sorted)}

    def _next_td(d: dt.date, *, max_steps: int = 10) -> Optional[dt.date]:
        x = d
        for _ in range(max_steps):
            k = _fmt_date(x)
            if k in date_set:
                return x
            x += dt.timedelta(days=1)
        return None

    d = start
    while d.weekday() != 0:
        d += dt.timedelta(days=1)

    out: List[WeeklyWindow] = []
    while d <= end and len(out) < max_weeks:
        entry_anchor = d + dt.timedelta(days=int(entry_dow))
        expiry_anchor = d + dt.timedelta(days=4)  # Friday
        entry_td = _next_td(entry_anchor, max_steps=10)
        exp_td = _next_td(expiry_anchor, max_steps=10)
        if entry_td and exp_td and entry_td < exp_td:
            ek = _fmt_date(entry_td)
            fk = _fmt_date(exp_td)
            i0 = idx.get(ek)
            i1 = idx.get(fk)
            dte_sessions = (int(i1) - int(i0) + 1) if (i0 is not None and i1 is not None and i1 >= i0) else 0
            dte_calendar = int((exp_td - entry_td).days)
            if dte_sessions > 0:
                out.append(
                    WeeklyWindow(
                        entry_date=entry_td,
                        expiry_date=exp_td,
                        dte_sessions=int(dte_sessions),
                        dte_calendar_days=int(dte_calendar),
                    )
                )
        d += dt.timedelta(days=7)

    return out
