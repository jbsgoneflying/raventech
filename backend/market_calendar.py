from __future__ import annotations

import datetime as dt
from typing import Dict, List, Optional, Tuple


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _nth_weekday_of_month(year: int, month: int, weekday: int, n: int) -> dt.date:
    """weekday: Mon=0..Sun=6; n=1..5"""
    d = dt.date(year, month, 1)
    # advance to weekday
    while d.weekday() != weekday:
        d += dt.timedelta(days=1)
    # nth occurrence
    return d + dt.timedelta(days=7 * (n - 1))


def _last_weekday_of_month(year: int, month: int, weekday: int) -> dt.date:
    d = dt.date(year, month + 1, 1) - dt.timedelta(days=1) if month < 12 else dt.date(year + 1, 1, 1) - dt.timedelta(days=1)
    while d.weekday() != weekday:
        d -= dt.timedelta(days=1)
    return d


def _easter_sunday(year: int) -> dt.date:
    """Anonymous Gregorian algorithm (Meeus/Jones/Butcher)."""
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month = (h + l - 7 * m + 114) // 31
    day = ((h + l - 7 * m + 114) % 31) + 1
    return dt.date(year, month, day)


def _observed_fixed_holiday(d: dt.date) -> dt.date:
    # NYSE observed: if Sat -> Fri, if Sun -> Mon, else same day
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


def nyse_holidays(year: int) -> Dict[str, str]:
    """
    Deterministic NYSE full-day holiday set (common, modern).
    Returns {date: title}.

    Note: This is a pragmatic planner calendar, not a compliance-grade exchange schedule.
    """
    holidays: List[Tuple[dt.date, str]] = []

    # Fixed-date (observed)
    holidays.append((_observed_fixed_holiday(dt.date(year, 1, 1)), "New Year’s Day"))
    holidays.append((_observed_fixed_holiday(dt.date(year, 6, 19)), "Juneteenth National Independence Day"))
    holidays.append((_observed_fixed_holiday(dt.date(year, 7, 4)), "Independence Day"))
    holidays.append((_observed_fixed_holiday(dt.date(year, 12, 25)), "Christmas Day"))

    # Floating holidays
    holidays.append((_nth_weekday_of_month(year, 1, weekday=0, n=3), "Martin Luther King Jr. Day"))
    holidays.append((_nth_weekday_of_month(year, 2, weekday=0, n=3), "Presidents’ Day"))
    holidays.append((_last_weekday_of_month(year, 5, weekday=0), "Memorial Day"))
    holidays.append((_nth_weekday_of_month(year, 9, weekday=0, n=1), "Labor Day"))
    holidays.append((_nth_weekday_of_month(year, 11, weekday=3, n=4), "Thanksgiving Day"))

    # Good Friday (Friday before Easter Sunday)
    easter = _easter_sunday(year)
    holidays.append((easter - dt.timedelta(days=2), "Good Friday"))

    out: Dict[str, str] = {}
    for d, title in holidays:
        out[_fmt_date(d)] = title
    return out


def nyse_early_closes(year: int) -> Dict[str, str]:
    """
    Common NYSE early close days (1pm ET) used for trading/vol planning.
    Returns {date: title}.
    """
    out: Dict[str, str] = {}

    # Day after Thanksgiving (Friday)
    tg = _nth_weekday_of_month(year, 11, weekday=3, n=4)
    out[_fmt_date(tg + dt.timedelta(days=1))] = "Day After Thanksgiving (Early Close 1:00pm ET)"

    # Christmas Eve (when weekday and not a holiday)
    xmas_eve = dt.date(year, 12, 24)
    if xmas_eve.weekday() <= 4:
        out[_fmt_date(xmas_eve)] = "Christmas Eve (Early Close 1:00pm ET)"

    # July 3rd (when weekday)
    july3 = dt.date(year, 7, 3)
    if july3.weekday() <= 4:
        out[_fmt_date(july3)] = "Pre‑Independence Day (Early Close 1:00pm ET)"

    return out


def market_structure_events_by_date(*, start: dt.date, end: dt.date) -> Dict[str, List[dict]]:
    years = sorted({start.year, end.year})
    out: Dict[str, List[dict]] = {}
    hol: Dict[str, str] = {}
    ec: Dict[str, str] = {}
    for y in range(years[0], years[-1] + 1):
        hol.update(nyse_holidays(y))
        ec.update(nyse_early_closes(y))

    cur = start
    while cur <= end:
        d0 = _fmt_date(cur)
        evs: List[dict] = []
        if d0 in hol:
            evs.append({"kind": "HOLIDAY", "title": f"{hol[d0]} — Market Closed", "short": hol[d0], "source": "local"})
        if d0 in ec:
            evs.append({"kind": "EARLY_CLOSE", "title": ec[d0], "short": "Early Close", "source": "local"})
        if evs:
            out[d0] = evs
        cur += dt.timedelta(days=1)
    return out


def _third_friday(year: int, month: int) -> dt.date:
    return _nth_weekday_of_month(year, month, weekday=4, n=3)


def opex_events_by_date(*, start: dt.date, end: dt.date) -> Dict[str, List[dict]]:
    out: Dict[str, List[dict]] = {}
    # iterate months in range
    cur = dt.date(start.year, start.month, 1)
    while cur <= end:
        tf = _third_friday(cur.year, cur.month)
        if start <= tf <= end:
            d0 = _fmt_date(tf)
            out.setdefault(d0, []).append({"kind": "OPEX", "title": "Monthly OpEx", "short": "OpEx", "source": "local"})
        # next month
        if cur.month == 12:
            cur = dt.date(cur.year + 1, 1, 1)
        else:
            cur = dt.date(cur.year, cur.month + 1, 1)
    return out


