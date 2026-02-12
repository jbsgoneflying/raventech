"""EODHD-only Earnings Calendar — mega-cap ($100 B+) universe.

Provides:
- ``get_mega_cap_universe()`` — screener-backed universe, cached 24 h
- ``get_earnings_calendar(start, end)`` — enriched calendar grouped by date
- ``get_earnings_trends(tickers)`` — forward consensus EPS for selected symbols
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional

from cachetools import TTLCache

LOG = logging.getLogger(__name__)

LOGO_URL_TEMPLATE = "https://eodhd.com/img/logos/US/{ticker}.png"

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class CompanyInfo:
    ticker: str
    name: str
    sector: str
    industry: str
    market_cap: float  # USD
    logo_url: str


@dataclass
class EarningsEntry:
    ticker: str
    name: str
    report_date: str  # YYYY-MM-DD
    fiscal_date: str  # YYYY-MM-DD (period end)
    before_after_market: Optional[str]  # "BeforeMarket", "AfterMarket", or None
    timing_label: str  # "BMO", "AMC", "TBD"
    actual: Optional[float]
    estimate: Optional[float]
    surprise_pct: Optional[float]
    sector: str
    industry: str
    market_cap: float
    logo_url: str


# ---------------------------------------------------------------------------
# Caches
# ---------------------------------------------------------------------------

_universe_cache: TTLCache = TTLCache(maxsize=4, ttl=24 * 60 * 60)
_universe_lock = threading.Lock()

_calendar_cache: TTLCache = TTLCache(maxsize=50, ttl=6 * 60 * 60)
_calendar_lock = threading.Lock()


def _cache_get(cache: TTLCache, lock: threading.Lock, key: Any) -> Any:
    with lock:
        return cache.get(key)


def _cache_set(cache: TTLCache, lock: threading.Lock, key: Any, val: Any) -> None:
    with lock:
        cache[key] = val


# ---------------------------------------------------------------------------
# Client singleton
# ---------------------------------------------------------------------------


def _get_client():
    """Return a module-level EodhdClient (lazy)."""
    from backend.eodhd_client import EodhdClient
    token = os.getenv("EODHD_API_TOKEN")
    if not token:
        raise RuntimeError("EODHD_API_TOKEN not set")
    return EodhdClient(token=token)


# ---------------------------------------------------------------------------
# Mega-cap universe  ($100 B+ market cap, US exchange)
# ---------------------------------------------------------------------------

_MIN_MARKET_CAP = 100_000_000_000  # $100 B


def get_mega_cap_universe() -> Dict[str, CompanyInfo]:
    """Return ``{bare_ticker: CompanyInfo}`` for all US stocks with
    market cap >= $100 B.  Result is cached for 24 hours.
    """
    cached = _cache_get(_universe_cache, _universe_lock, "mega")
    if cached is not None:
        return cached

    client = _get_client()
    filters_json = json.dumps([
        ["market_capitalization", ">", _MIN_MARKET_CAP],
        ["exchange", "=", "us"],
    ])

    universe: Dict[str, CompanyInfo] = {}
    offset = 0
    limit = 100
    max_pages = 15  # safety: ~1500 tickers max

    for _ in range(max_pages):
        try:
            resp = client.get_screener(
                filters=filters_json,
                sort="market_capitalization.desc",
                limit=limit,
                offset=offset,
            )
            rows = resp.rows or []
        except Exception as exc:
            LOG.warning("Screener page offset=%d failed: %s", offset, exc)
            break

        if not rows:
            break

        for r in rows:
            code = str(r.get("code") or "").strip()
            if not code:
                continue
            # code comes back as "AAPL" (bare) from screener
            bare = code.split(".")[0].upper()
            universe[bare] = CompanyInfo(
                ticker=bare,
                name=str(r.get("name") or bare),
                sector=str(r.get("sector") or ""),
                industry=str(r.get("industry") or ""),
                market_cap=float(r.get("market_capitalization") or 0),
                logo_url=LOGO_URL_TEMPLATE.format(ticker=bare),
            )

        if len(rows) < limit:
            break
        offset += limit

    LOG.info("Mega-cap universe loaded: %d companies", len(universe))
    _cache_set(_universe_cache, _universe_lock, "mega", universe)
    return universe


# ---------------------------------------------------------------------------
# Earnings calendar
# ---------------------------------------------------------------------------


def _timing_label(bam: Optional[str]) -> str:
    if not bam:
        return "TBD"
    s = str(bam).lower()
    if "before" in s:
        return "BMO"
    if "after" in s:
        return "AMC"
    return "TBD"


def _safe_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        f = float(v)
        return f if f == f else None  # NaN guard
    except (TypeError, ValueError):
        return None


def get_earnings_calendar(
    start: dt.date,
    end: dt.date,
) -> Dict[str, List[dict]]:
    """Return earnings grouped by ``report_date`` for mega-cap companies.

    Each value is a list of serialised ``EarningsEntry`` dicts, sorted by
    market cap descending within each day.
    """
    key = ("cal", start.isoformat(), end.isoformat())
    cached = _cache_get(_calendar_cache, _calendar_lock, key)
    if cached is not None:
        return cached

    universe = get_mega_cap_universe()
    if not universe:
        LOG.warning("Empty mega-cap universe; earnings calendar will be empty")
        return {}

    client = _get_client()
    try:
        resp = client.get_calendar_earnings(
            from_date=start.isoformat(),
            to_date=end.isoformat(),
        )
        raw_earnings = resp.raw  # top-level dict with "earnings" key
    except Exception as exc:
        LOG.warning("Calendar earnings fetch failed: %s", exc)
        return {}

    # Extract the earnings list from the response envelope
    if isinstance(raw_earnings, dict):
        earnings_list = raw_earnings.get("earnings") or []
    elif isinstance(raw_earnings, list):
        earnings_list = raw_earnings
    else:
        earnings_list = []

    days: Dict[str, List[dict]] = {}

    for e in earnings_list:
        if not isinstance(e, dict):
            continue
        code = str(e.get("code") or "").strip()
        if not code:
            continue
        bare = code.split(".")[0].upper()
        company = universe.get(bare)
        if company is None:
            continue  # not in mega-cap universe

        report_date = str(e.get("report_date") or "")[:10]
        if not report_date:
            continue

        bam = e.get("before_after_market")
        entry = EarningsEntry(
            ticker=bare,
            name=company.name,
            report_date=report_date,
            fiscal_date=str(e.get("date") or "")[:10],
            before_after_market=bam,
            timing_label=_timing_label(bam),
            actual=_safe_float(e.get("actual")),
            estimate=_safe_float(e.get("estimate")),
            surprise_pct=_safe_float(e.get("percent")),
            sector=company.sector,
            industry=company.industry,
            market_cap=company.market_cap,
            logo_url=company.logo_url,
        )

        days.setdefault(report_date, []).append(asdict(entry))

    # Sort each day's entries by market cap descending
    for d in days:
        days[d].sort(key=lambda x: x.get("market_cap", 0), reverse=True)

    result = dict(sorted(days.items()))
    _cache_set(_calendar_cache, _calendar_lock, key, result)
    return result


# ---------------------------------------------------------------------------
# Earnings trends (consensus EPS)
# ---------------------------------------------------------------------------


def get_earnings_trends(tickers: List[str]) -> Dict[str, dict]:
    """Fetch forward consensus EPS estimates for a list of bare tickers.

    Returns ``{ticker: {period, eps_avg, eps_low, eps_high, growth, ...}}``.
    Only the current-quarter (``0q``) estimate is returned for brevity.
    """
    if not tickers:
        return {}

    symbols = ",".join(f"{t.upper()}.US" for t in tickers)
    client = _get_client()
    try:
        resp = client.get_calendar_trends(symbols=symbols)
        raw = resp.raw
    except Exception as exc:
        LOG.warning("Calendar trends fetch failed: %s", exc)
        return {}

    # The response shape is { trends: [ [items_for_sym1], [items_for_sym2], ... ] }
    trends_lists = []
    if isinstance(raw, dict):
        trends_lists = raw.get("trends") or []
    elif isinstance(raw, list):
        trends_lists = raw

    out: Dict[str, dict] = {}
    for sym_trends in trends_lists:
        if not isinstance(sym_trends, list):
            continue
        # Pick the current-quarter item (period="0q") if available
        for item in sym_trends:
            if not isinstance(item, dict):
                continue
            code = str(item.get("code") or "").split(".")[0].upper()
            period = str(item.get("period") or "")
            if period == "0q":
                out[code] = {
                    "period": period,
                    "eps_avg": _safe_float(item.get("earningsEstimateAvg")),
                    "eps_low": _safe_float(item.get("earningsEstimateLow")),
                    "eps_high": _safe_float(item.get("earningsEstimateHigh")),
                    "growth": _safe_float(item.get("earningsEstimateGrowth")),
                    "revenue_avg": _safe_float(item.get("revenueEstimateAvg")),
                    "num_analysts": _safe_float(item.get("earningsEstimateNumberOfAnalysts")),
                }
                break  # one per symbol is enough

    return out
