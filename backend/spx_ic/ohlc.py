from __future__ import annotations

import datetime as dt
import logging
import math
import threading
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from backend.orats_client import OratsClient
from backend.spx_ic.utils import (
    _cache_get,
    _cache_set,
    _first_row,
    _fmt_date,
    _iv_to_pct,
    _parse_date,
    _row_dte_days,
    _to_float,
)

LOG = logging.getLogger("spx_ic.ohlc")


# ---- Daily bars (OHLC) ----

@dataclass(frozen=True)
class DailyOHLC:
    trade_date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float] = None
    vwap: Optional[float] = None


_ohlc_cache = TTLCache(maxsize=250_000, ttl=24 * 60 * 60)
_ohlc_lock = threading.Lock()


def _sniff_daily_volume(row: dict) -> Optional[float]:
    """
    ORATS /hist/dailies volume field names can vary by entitlement/plan.
    Try common keys first, then sniff any plausible *share volume* key.
    """
    if not isinstance(row, dict):
        return None

    v = _to_float(
        row.get("volume") or
        row.get("stockVolume") or
        row.get("vol") or
        row.get("totalVolume") or
        row.get("total_volume") or
        row.get("shareVolume") or
        row.get("shares") or
        row.get("sharesTraded")
    )
    if v is not None and math.isfinite(float(v)) and float(v) > 0:
        return float(v)

    keys = list(row.keys())
    candidates: List[Tuple[float, str]] = []
    for k in keys:
        kk = str(k).lower()
        if "vol" not in kk and "share" not in kk:
            continue
        if any(bad in kk for bad in ("iv", "implied", "vwap", "volatility", "rv", "var", "volga")):
            continue
        x = _to_float(row.get(k))
        if x is None or not math.isfinite(float(x)) or float(x) <= 0:
            continue
        candidates.append((float(x), str(k)))

    if not candidates:
        return None

    candidates.sort(key=lambda t: t[0], reverse=True)
    best_val, _best_key = candidates[0]
    if best_val < 100.0:
        return None
    return float(best_val)


def fetch_daily_ohlc(client: OratsClient, *, ticker: str, date: dt.date) -> Optional[DailyOHLC]:
    """Best-effort OHLC fetch for a single trade date.

    Primary: EODHD via PriceService.  Fallback: ORATS /hist/dailies.
    """
    key = ("ohlc", ticker, _fmt_date(date))
    cached = _cache_get(_ohlc_cache, _ohlc_lock, key)
    if cached is not None:
        return cached

    out: Optional[DailyOHLC] = None

    from backend.price_service import get_price_service
    ps = get_price_service()
    if ps is not None:
        try:
            bars = ps.fetch_daily_bars(ticker, date, date)
            if bars:
                b = bars[0]
                out = DailyOHLC(
                    trade_date=b.trade_date, open=b.open, high=b.high,
                    low=b.low, close=b.close, volume=b.volume, vwap=None,
                )
        except Exception:
            pass

    if out is None:
        try:
            fields = "ticker,tradeDate,open,opPx,hiPx,loPx,clsPx,close,high,low,volume,vol,stockVolume,vwap"
            resp = client.hist_dailies(ticker=ticker, trade_date=_fmt_date(date), fields=fields)
            row = _first_row(resp.rows)
        except Exception:
            row = None
        if row:
            td = str(row.get("tradeDate") or _fmt_date(date))[:10]
            o = _to_float(row.get("open") or row.get("opPx") or row.get("op_px"))
            h = _to_float(row.get("hiPx") or row.get("high") or row.get("hi") or row.get("hPx"))
            l = _to_float(row.get("loPx") or row.get("low") or row.get("lo") or row.get("lPx"))
            c = _to_float(row.get("clsPx") or row.get("close") or row.get("cls_px"))
            vol = _sniff_daily_volume(row)
            vwap = _to_float(row.get("vwap"))
            out = DailyOHLC(trade_date=td, open=o, high=h, low=l, close=c, volume=vol, vwap=vwap)

    _cache_set(_ohlc_cache, _ohlc_lock, key, out)
    return out


def fetch_close_px(client: OratsClient, *, ticker: str, date: dt.date) -> Optional[float]:
    """Fetch close price for ticker on a given trade date (EOD)."""
    bar = fetch_daily_ohlc(client, ticker=ticker, date=date)
    return None if bar is None else bar.close


def fetch_open_px(client: OratsClient, *, ticker: str, date: dt.date) -> Optional[float]:
    bar = fetch_daily_ohlc(client, ticker=ticker, date=date)
    return None if bar is None else bar.open


def fetch_high_low(client: OratsClient, *, ticker: str, date: dt.date) -> Tuple[Optional[float], Optional[float]]:
    bar = fetch_daily_ohlc(client, ticker=ticker, date=date)
    if bar is None:
        return None, None
    return bar.high, bar.low


def fetch_trading_closes(
    client: OratsClient,
    *,
    ticker: str,
    end: dt.date,
    n: int = 320,
    max_calendar_scan: int = 520,
) -> List[Tuple[str, float]]:
    """
    Build a trailing close series by walking back calendar days and keeping dates where
    ORATS provides a close. This avoids needing a range endpoint.
    """
    series: List[Tuple[str, float]] = []
    d = end
    scanned = 0
    while len(series) < n and scanned < max_calendar_scan:
        px = fetch_close_px(client, ticker=ticker, date=d)
        if px is not None and px > 0:
            series.append((_fmt_date(d), float(px)))
        d = d - dt.timedelta(days=1)
        scanned += 1
    series.reverse()
    return series


# ---- IV caches ----

_atm_iv_cache = TTLCache(maxsize=50_000, ttl=24 * 60 * 60)
_atm_iv_lock = threading.Lock()

_iv_curve_cache = TTLCache(maxsize=50_000, ttl=24 * 60 * 60)
_iv_curve_lock = threading.Lock()


# ---- Trading-day navigation ----

def find_trading_day(client: OratsClient, *, ticker: str, start: dt.date, step: int, max_steps: int = 10) -> Optional[dt.date]:
    """Walk calendar days until we find a date with a close price."""
    d = start
    for _ in range(max_steps):
        if fetch_close_px(client, ticker=ticker, date=d) is not None:
            return d
        d = d + dt.timedelta(days=step)
    return None


def next_trading_day(client: OratsClient, *, ticker: str, date: dt.date) -> Optional[dt.date]:
    return find_trading_day(client, ticker=ticker, start=date, step=+1, max_steps=10)


def prior_trading_day(client: OratsClient, *, ticker: str, date: dt.date) -> Optional[dt.date]:
    return find_trading_day(client, ticker=ticker, start=date, step=-1, max_steps=10)


# ---- IV fetching ----

def fetch_atm_iv_pct(
    client: OratsClient,
    *,
    ticker: str,
    trade_date: dt.date,
    dte_target: int,
) -> Optional[float]:
    """
    Approximate ATM IV from ORATS monies implied surface: use vol50 (call-delta 50) for nearest expiry to dte_target.
    Returns IV as a percent (e.g., 15.2).
    """
    key = ("atm_iv50", ticker, _fmt_date(trade_date), int(dte_target))
    cached = _cache_get(_atm_iv_cache, _atm_iv_lock, key)
    if cached is not None:
        return cached

    lo = max(1, int(dte_target) - 2)
    hi = int(dte_target) + 7
    iv = None
    try:
        fields = "ticker,tradeDate,expirDate,dte,stockPrice,vol50"
        resp = client.hist_monies_implied(ticker=ticker, trade_date=_fmt_date(trade_date), fields=fields, dte=f"{lo},{hi}")
        rows = resp.rows or []
        best = None
        best_dist = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            dte = _row_dte_days(r, trade_date=trade_date)
            v = _iv_to_pct(r.get("vol50"))
            if dte is None or v is None:
                continue
            dist = abs(float(dte) - float(dte_target))
            if best is None or (best_dist is not None and dist < best_dist):
                best = r
                best_dist = dist
        if best is not None:
            iv = _iv_to_pct(best.get("vol50"))
    except Exception:
        iv = None

    _cache_set(_atm_iv_cache, _atm_iv_lock, key, iv)
    return iv


def fetch_iv_curve(
    client: OratsClient,
    *,
    ticker: str,
    trade_date: dt.date,
    dte_targets: List[int],
) -> Dict[int, Optional[float]]:
    """
    Fetch vol50 for several DTE targets (best-effort) in one ORATS call.
    Returns mapping dte_target -> vol50 (percent).
    """
    key = ("iv_curve", ticker, _fmt_date(trade_date), tuple(int(x) for x in dte_targets))
    cached = _cache_get(_iv_curve_cache, _iv_curve_lock, key)
    if cached is not None:
        return cached

    out: Dict[int, Optional[float]] = {int(x): None for x in dte_targets}
    if not dte_targets:
        _cache_set(_iv_curve_cache, _iv_curve_lock, key, out)
        return out

    lo = max(1, min(int(x) for x in dte_targets) - 2)
    hi = max(int(x) for x in dte_targets) + 7
    try:
        fields = "ticker,tradeDate,expirDate,dte,vol50"
        resp = client.hist_monies_implied(ticker=ticker, trade_date=_fmt_date(trade_date), fields=fields, dte=f"{lo},{hi}")
        rows = resp.rows or []
    except Exception:
        rows = []

    for target in dte_targets:
        best = None
        best_dist = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            dte = _row_dte_days(r, trade_date=trade_date)
            v = _iv_to_pct(r.get("vol50"))
            if dte is None or v is None:
                continue
            dist = abs(float(dte) - float(target))
            if best is None or best_dist is None or dist < best_dist:
                best = r
                best_dist = dist
        out[int(target)] = None if best is None else _iv_to_pct(best.get("vol50"))

    _cache_set(_iv_curve_cache, _iv_curve_lock, key, out)
    return out


def fetch_iv_pack(
    client: OratsClient,
    *,
    ticker: str,
    trade_date: dt.date,
    dte_targets: List[int],
) -> Dict[int, Optional[float]]:
    """
    Fetch vol50 once and pick best matches for multiple DTE targets.
    Returns mapping dte_target -> vol50 (percent).
    """
    key = ("iv_pack", ticker, _fmt_date(trade_date), tuple(int(x) for x in dte_targets))
    cached = _cache_get(_iv_curve_cache, _iv_curve_lock, key)
    if cached is not None:
        return cached

    out: Dict[int, Optional[float]] = {int(x): None for x in dte_targets}
    if not dte_targets:
        _cache_set(_iv_curve_cache, _iv_curve_lock, key, out)
        return out

    lo = max(1, min(int(x) for x in dte_targets) - 2)
    hi = max(int(x) for x in dte_targets) + 7
    try:
        fields = "ticker,tradeDate,expirDate,dte,vol50"
        resp = client.hist_monies_implied(ticker=ticker, trade_date=_fmt_date(trade_date), fields=fields, dte=f"{lo},{hi}")
        rows = resp.rows or []
    except Exception:
        rows = []

    for target in dte_targets:
        best = None
        best_dist = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            dte = _row_dte_days(r, trade_date=trade_date)
            v = _iv_to_pct(r.get("vol50"))
            if dte is None or v is None:
                continue
            dist = abs(float(dte) - float(target))
            if best is None or best_dist is None or dist < best_dist:
                best = r
                best_dist = dist
        out[int(target)] = None if best is None else _iv_to_pct(best.get("vol50"))

    _cache_set(_iv_curve_cache, _iv_curve_lock, key, out)
    return out


def _iv_pack_from_rows(*, rows: List[dict], trade_date: dt.date, dte_targets: List[int]) -> Dict[int, Optional[float]]:
    """
    Pick best vol50 matches for multiple DTE targets from a pre-fetched rows list.
    Returns mapping dte_target -> vol50 (percent).
    """
    out: Dict[int, Optional[float]] = {int(x): None for x in dte_targets}
    if not rows or not dte_targets:
        return out

    for target in dte_targets:
        best = None
        best_dist = None
        for r in rows:
            if not isinstance(r, dict):
                continue
            dte = _row_dte_days(r, trade_date=trade_date)
            v = _iv_to_pct(r.get("vol50"))
            if dte is None or v is None:
                continue
            dist = abs(float(dte) - float(target))
            if best is None or best_dist is None or dist < best_dist:
                best = r
                best_dist = dist
        out[int(target)] = None if best is None else _iv_to_pct(best.get("vol50"))

    return out


# ---- Range fetchers ----

def fetch_monies_implied_range(
    client: OratsClient,
    *,
    ticker: str,
    start: dt.date,
    end: dt.date,
    dte_lo: int,
    dte_hi: int,
) -> List[dict]:
    """
    Best-effort bulk fetch for ORATS /hist/monies/implied using tradeDate ranges if supported.
    If the endpoint doesn't support ranges for your entitlement, this returns [] and callers
    should fall back to per-date fetch_iv_pack().
    """
    if end < start:
        return []
    td = f"{_fmt_date(start)},{_fmt_date(end)}"
    try:
        fields = "ticker,tradeDate,expirDate,dte,vol50"
        resp = client.hist_monies_implied(ticker=ticker, trade_date=td, fields=fields, dte=f"{int(dte_lo)},{int(dte_hi)}")
        return [r for r in (resp.rows or []) if isinstance(r, dict)]
    except Exception:
        return []


def fetch_hist_cores_range(
    client: OratsClient,
    *,
    ticker: str,
    start: dt.date,
    end: dt.date,
    fields: str,
) -> List[dict]:
    """
    Range fetch for ORATS /hist/cores using fromDate/toDate (fast).
    """
    if end < start:
        return []
    get_fn = getattr(client, "get", None)
    if not callable(get_fn):
        return []
    resp = get_fn(
        "/hist/cores",
        {"ticker": ticker, "fromDate": _fmt_date(start), "toDate": _fmt_date(end), "fields": fields},
    )
    return [r for r in (resp.rows or []) if isinstance(r, dict)]


def iv_to_em1sigma_pct(*, iv_pct: float, dte_calendar_days: int) -> float:
    """Convert annualized IV (%) to a 1-sigma expected move (%) over T calendar days."""
    t = max(1, int(dte_calendar_days)) / 365.0
    return float(iv_pct) * math.sqrt(t)


# ---- Bulk bar fetchers ----

def fetch_trading_bars(
    client: OratsClient,
    *,
    ticker: str,
    end: dt.date,
    n: int = 900,
    max_calendar_scan: int = 1400,
) -> List[DailyOHLC]:
    """
    Pull up to `n` most recent trading-day OHLC bars up to `end` (inclusive), walking back calendar days.
    """
    out: List[DailyOHLC] = []
    d = end
    scanned = 0
    while len(out) < n and scanned < max_calendar_scan:
        b = fetch_daily_ohlc(client, ticker=ticker, date=d)
        if b and b.close is not None and b.close > 0:
            out.append(b)
        d = d - dt.timedelta(days=1)
        scanned += 1
    out.reverse()
    return out


def fetch_dailies_ohlc_range(
    client: OratsClient,
    *,
    ticker: str,
    start: dt.date,
    end: dt.date,
) -> List[DailyOHLC]:
    """Fetch daily OHLC bars for a date range.

    Primary: EODHD via PriceService.  Fallback: ORATS /hist/dailies.
    """
    if end < start:
        return []

    from backend.price_service import get_price_service
    ps = get_price_service()
    if ps is not None:
        try:
            bars = ps.fetch_daily_bars(ticker, start, end)
            return [
                DailyOHLC(
                    trade_date=b.trade_date, open=b.open, high=b.high,
                    low=b.low, close=b.close, volume=b.volume, vwap=None,
                )
                for b in bars
            ]
        except Exception:
            pass

    try:
        td = f"{_fmt_date(start)},{_fmt_date(end)}"
        flds = "ticker,tradeDate,open,opPx,hiPx,loPx,clsPx,close,high,low,volume,vol,stockVolume,vwap"
        resp = client.hist_dailies(ticker=ticker, trade_date=td, fields=flds)
        rows = resp.rows or []
    except Exception:
        rows = []
    out: List[DailyOHLC] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        td0 = str(r.get("tradeDate") or "")[:10]
        if not td0:
            continue
        o = _to_float(r.get("open") or r.get("opPx") or r.get("op_px"))
        h = _to_float(r.get("hiPx") or r.get("high") or r.get("hi") or r.get("hPx"))
        l = _to_float(r.get("loPx") or r.get("low") or r.get("lo") or r.get("lPx"))
        c = _to_float(r.get("clsPx") or r.get("close") or r.get("cls_px"))
        if c is None or c <= 0:
            continue
        vol = _sniff_daily_volume(r)
        vwap = _to_float(r.get("vwap"))
        out.append(DailyOHLC(trade_date=td0, open=o, high=h, low=l, close=c, volume=vol, vwap=vwap))
    out.sort(key=lambda b: b.trade_date)
    return out


def fetch_close_map_range(
    client: OratsClient,
    *,
    ticker: str,
    start: dt.date,
    end: dt.date,
) -> Dict[str, float]:
    """Convenience: date->close using range pull."""
    out: Dict[str, float] = {}
    for b in fetch_dailies_ohlc_range(client, ticker=ticker, start=start, end=end):
        if b.close is None:
            continue
        out[b.trade_date] = float(b.close)
    return out
