from __future__ import annotations

import datetime as dt
import math
from typing import Any, List, Optional, Tuple
from zoneinfo import ZoneInfo

_ET = ZoneInfo("America/New_York")


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if not math.isfinite(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


def _iv_to_pct(v: Any) -> Optional[float]:
    """
    Normalize IV-like values to percent.
    ORATS sometimes returns vols as decimals (0.12 = 12%) or percents (12 = 12%).
    """
    x = _to_float(v)
    if x is None:
        return None
    x = abs(float(x))
    return x * 100.0 if x <= 1.0 else x


def _now_et(now_dt: Optional[dt.datetime] = None) -> dt.datetime:
    """
    Return timezone-aware datetime in US/Eastern.
    - If now_dt is None: uses current wall clock in ET.
    - If now_dt is naive: assumes it is already ET.
    - If now_dt is aware: converts to ET.
    """
    if now_dt is None:
        return dt.datetime.now(tz=_ET)
    if now_dt.tzinfo is None:
        return now_dt.replace(tzinfo=_ET)
    return now_dt.astimezone(_ET)


def _after_cash_close_et(now_et: dt.datetime) -> bool:
    """
    Define when to roll weekly expiry after Friday close.
    We use 4:15pm ET to allow for settlement/prints and to keep behavior stable.
    """
    return now_et.time() >= dt.time(16, 15)


def _normalize_expiry_dates(exp_dates: List[str]) -> List[str]:
    ds = [str(d)[:10] for d in (exp_dates or []) if d]
    return sorted(list(dict.fromkeys(ds)))


def _pick_nearest_expiry_date(exp_dates: List[str], *, today: dt.date) -> Optional[str]:
    """
    Nearest-expiry selector (daily/0DTE behavior):
    - Prefer 0DTE if present
    - Else nearest upcoming
    - Else last known
    """
    ds = _normalize_expiry_dates(exp_dates)
    if not ds:
        return None
    td = _fmt_date(today)
    if td in ds:
        return td
    for d0 in ds:
        try:
            if _parse_date(d0) > today:
                return d0
        except Exception:
            continue
    return ds[-1]


def _pick_weekly_close_expiry_date(
    exp_dates: List[str],
    *,
    today: dt.date,
    now_dt: Optional[dt.datetime] = None,
) -> Optional[str]:
    """
    Weekly trade-management selector:
    - If today is Friday and BEFORE 4:15pm ET: pick today's Friday expiry (if listed)
    - Otherwise: pick the next Friday
    - Holiday week fallback: if no Friday, pick the next Thursday
    - Otherwise fallback: nearest upcoming expiry
    """
    ds = _normalize_expiry_dates(exp_dates)
    if not ds:
        return None

    now_et_val = _now_et(now_dt)
    after_close = _after_cash_close_et(now_et_val)

    td = _fmt_date(today)
    if today.weekday() == 4 and (not after_close) and td in ds:
        return td

    start = today + dt.timedelta(days=1) if after_close else today
    future: List[Tuple[str, dt.date]] = []
    for d0 in ds:
        try:
            dd = _parse_date(d0)
        except Exception:
            continue
        if dd >= start:
            future.append((d0, dd))

    for d0, dd in future:
        if dd.weekday() == 4:
            return d0
    for d0, dd in future:
        if dd.weekday() == 3:
            return d0
    if future:
        return future[0][0]
    return ds[-1]


def _pick_expiry_window(
    exp_dates: List[str],
    *,
    view: str,
    today: dt.date,
    now_dt: Optional[dt.datetime] = None,
    limit: int = 12,
) -> List[str]:
    """
    Pick a small forward window of expiries for visualization (heatmap).

    - view="weekly": prefer Friday expiries (then Thursday holiday fallbacks), starting from
      today (or tomorrow if after cash close).
    - view="nearest": prefer nearest expiries (including 0DTE if present).

    Returns a sorted, unique list of ISO dates (YYYY-MM-DD).
    """
    ds = _normalize_expiry_dates(exp_dates)
    if not ds:
        return []
    lim = max(1, int(limit))

    v = str(view or "weekly").strip().lower()
    now_et_val = _now_et(now_dt)
    after_close = _after_cash_close_et(now_et_val)
    start = today + dt.timedelta(days=1) if after_close else today

    future: List[Tuple[str, dt.date]] = []
    for d0 in ds:
        try:
            dd = _parse_date(d0)
        except Exception:
            continue
        if dd >= start:
            future.append((d0, dd))
    future.sort(key=lambda x: x[1])

    if not future:
        return ds[-lim:]

    if v.startswith("week"):
        fr = [d0 for (d0, dd) in future if dd.weekday() == 4]
        th = [d0 for (d0, dd) in future if dd.weekday() == 3]
        other = [d0 for (d0, dd) in future if dd.weekday() not in (3, 4)]
        out = fr + th + other
        return out[:lim]

    return [d0 for (d0, _) in future[:lim]]


def _pick_spot_from_live_rows(rows: List[dict]) -> Optional[float]:
    for key in ("spotPrice", "spot_price", "spot"):
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            v = _to_float(r.get(key))
            if v and v > 0:
                return float(v)
    for key in ("stockPrice", "stock_price", "underlyingPrice"):
        for r in rows or []:
            if not isinstance(r, dict):
                continue
            v = _to_float(r.get(key))
            if v and v > 0:
                return float(v)
    return None


def _pct_ret(a: float, b: float) -> float:
    return (b / a - 1.0) * 100.0


def _quarter_key(d: dt.date) -> str:
    q = ((d.month - 1) // 3) + 1
    return f"Q{q}"
