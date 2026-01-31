from __future__ import annotations

import datetime as dt
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.benzinga_client import BenzingaClient
from backend.fmp_client import FmpClient, FmpError
from backend.orats_client import OratsClient
from backend.earnings_calendar import infer_timing_from_time_str
from backend.market_calendar import market_structure_events_by_date, opex_events_by_date
from backend.macro_events import macro_events_by_date
from backend.universe import load_universe_sp500_and_nasdaq100
from backend.earnings_logic import classify_timing
from backend.redis_store import RedisStore
from backend.calendar_snapshot import load_earnings_snapshot

LOG = logging.getLogger(__name__)


def _parse_date(s: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _clamp_int(v: Any, lo: int, hi: int, default: int) -> int:
    try:
        x = int(float(v))
        return max(int(lo), min(int(hi), x))
    except Exception:
        return int(default)


def _first_of_month(d: dt.date) -> dt.date:
    return dt.date(d.year, d.month, 1)


def _last_of_month(d: dt.date) -> dt.date:
    if d.month == 12:
        nxt = dt.date(d.year + 1, 1, 1)
    else:
        nxt = dt.date(d.year, d.month + 1, 1)
    return nxt - dt.timedelta(days=1)


def _start_of_week_monday(d: dt.date) -> dt.date:
    # Monday=0
    return d - dt.timedelta(days=int(d.weekday()))


def _trading_weekdays(d0: dt.date, d1: dt.date) -> List[dt.date]:
    out: List[dt.date] = []
    cur = d0
    while cur <= d1:
        if cur.weekday() <= 4:  # Mon..Fri
            out.append(cur)
        cur += dt.timedelta(days=1)
    return out


@dataclass(frozen=True)
class Engine1UniversePolicy:
    # Kept for backwards compatibility; no longer used by the calendar snapshot path.
    min_price: float = 50.0
    min_market_cap: float = 10_000_000_000.0
    min_avg_dollar_vol_20d: float = 200_000_000.0


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:
            return None
        return f
    except Exception:
        return None


def _pick_first_float(row: dict, keys: List[str]) -> Optional[float]:
    for k in keys:
        v = _to_float(row.get(k))
        if v is not None:
            return v
    return None


def engine1_is_eligible_from_orats_row(row: dict, *, policy: Engine1UniversePolicy) -> Tuple[bool, Dict[str, Any]]:
    """
    Determine Engine-1 eligibility using ORATS /cores snapshot fields.

    We use a flexible set of key names to stay robust to field naming differences
    across ORATS plans / versions.
    """
    px = _pick_first_float(row, ["stockPrice", "spotPrice", "underlyingPrice", "price", "close"])
    mcap = _pick_first_float(row, ["marketCap", "mktCap", "marketcap", "market_cap"])

    avg_vol = _pick_first_float(row, ["avgVol20", "avgVolume20", "avgVol", "avgVolume", "volumeAvg20"])
    avg_dvol = _pick_first_float(row, ["avgDollarVol20", "avgDolVol20", "avgDVol20", "avgDollarVolume20", "dollarVol20"])
    if avg_dvol is None and avg_vol is not None and px is not None and avg_vol > 0 and px > 0:
        avg_dvol = avg_vol * px

    ok_price = (px is not None) and (px >= float(policy.min_price))
    ok_mcap = (mcap is not None) and (mcap >= float(policy.min_market_cap))
    ok_dvol = (avg_dvol is not None) and (avg_dvol >= float(policy.min_avg_dollar_vol_20d))

    # Conservative-but-practical behavior:
    # - Always enforce price minimum.
    # - If we have mcap or dvol, require at least one to clear.
    # - If we have neither, allow based on price alone (keeps the calendar usable even if fields are missing).
    if not ok_price:
        return False, {"price": px, "marketCap": mcap, "avgDollarVol20d": avg_dvol, "reason": "price_below_min"}

    if mcap is None and avg_dvol is None:
        return True, {"price": px, "marketCap": None, "avgDollarVol20d": None, "reason": "price_only_missing_cap_liq"}

    if ok_mcap or ok_dvol:
        return True, {"price": px, "marketCap": mcap, "avgDollarVol20d": avg_dvol, "reason": "pass"}

    return False, {"price": px, "marketCap": mcap, "avgDollarVol20d": avg_dvol, "reason": "cap_liq_below_min"}


def fetch_engine1_eligibility(
    client: OratsClient,
    *,
    ticker: str,
    policy: Engine1UniversePolicy,
) -> Tuple[bool, Dict[str, Any]]:
    fields = ",".join(
        [
            "ticker",
            "stockPrice",
            "spotPrice",
            "marketCap",
            "avgDollarVol20",
            "avgVol20",
        ]
    )
    try:
        rows = client.cores(ticker=str(ticker).upper(), fields=fields).rows or []
        row = rows[0] if rows else {}
    except Exception as e:
        # Be permissive: the calendar should still show names even if ORATS snapshot is unavailable.
        # We'll label this in diagnostics and keep the name in the calendar.
        return True, {"reason": f"orats_error_allow:{type(e).__name__}"}
    ok, diag = engine1_is_eligible_from_orats_row(row if isinstance(row, dict) else {}, policy=policy)
    return ok, diag


def _fetch_benzinga_earnings_range(
    bz: BenzingaClient,
    *,
    start: dt.date,
    end: dt.date,
    max_pages: int = 250,
    pagesize: int = 1000,
) -> Tuple[List[dict], Dict[str, Any]]:
    rows_all: List[dict] = []
    pages = 0
    truncated = False
    for page in range(int(max_pages)):
        resp = bz.calendar_earnings(
            date_from=_fmt_date(start),
            date_to=_fmt_date(end),
            pagesize=int(pagesize),
            page=int(page),
        )
        batch = resp.rows or []
        pages += 1
        rows_all.extend([r for r in batch if isinstance(r, dict)])
        if len(batch) < int(pagesize):
            break
        if page == int(max_pages) - 1 and len(batch) >= int(pagesize):
            truncated = True
    return rows_all, {"pagesFetched": int(pages), "maxPages": int(max_pages), "pageSize": int(pagesize), "truncated": bool(truncated)}


def _build_day_skeleton(view: str, *, start: dt.date, end: dt.date) -> List[dt.date]:
    v = str(view or "").lower().strip()
    if v == "day":
        return [start]
    if v == "week":
        # trading week only (Mon..Fri)
        return _trading_weekdays(start, end)
    # month: all days in month (including weekends to match a full calendar grid)
    out: List[dt.date] = []
    cur = start
    while cur <= end:
        out.append(cur)
        cur += dt.timedelta(days=1)
    return out


def _event_sort_key(ev: dict) -> Tuple[int, str]:
    kind = str(ev.get("kind") or "").upper()
    # priority: holidays/early close first for planning, then Fed/Econ/Treasury, then OPEX
    pri = 50
    if kind == "HOLIDAY":
        pri = 10
    elif kind == "EARLY_CLOSE":
        pri = 12
    elif kind == "FED":
        pri = 20
    elif kind == "ECON":
        pri = 22
    elif kind == "TREASURY":
        pri = 24
    elif kind == "OPEX":
        pri = 30
    return pri, str(ev.get("title") or "")


def build_calendar_payload(
    *,
    view: str,
    anchor: str,
    tz: str,
    engine1_only: bool,
    include_events: bool,
    benzinga_client: Optional[BenzingaClient],
    fmp_client: Optional[FmpClient],
    max_tickers: int,
    redis_store: Optional[RedisStore] = None,
) -> Dict[str, Any]:
    v = str(view or "month").lower().strip()
    if v not in ("month", "week", "day"):
        raise ValueError("Unsupported view. Allowed: month|week|day")

    a = _parse_date(anchor) or dt.date.today()
    if v == "month":
        m0 = _first_of_month(a)
        m1 = _last_of_month(a)
        start = _start_of_week_monday(m0)
        end = m1 + dt.timedelta(days=int(6 - m1.weekday()))
    elif v == "week":
        start = _start_of_week_monday(a)
        end = start + dt.timedelta(days=4)  # Mon..Fri (trading week)
    else:
        start = a
        end = a

    # Build list of calendar days we will return.
    days = _build_day_skeleton(v, start=start, end=end)
    day_keys = {_fmt_date(d) for d in days}

    # Precompute events (market structure + macro) once for the requested range.
    events_by_date: Dict[str, List[dict]] = {k: [] for k in day_keys}
    sources: List[str] = []
    notes: List[str] = []

    if include_events:
        # market structure (holidays + early close) is deterministic
        ms = market_structure_events_by_date(start=start, end=end)
        for d0, evs in ms.items():
            if d0 in events_by_date:
                events_by_date[d0].extend(evs)
        ox = opex_events_by_date(start=start, end=end)
        for d0, evs in ox.items():
            if d0 in events_by_date:
                events_by_date[d0].extend(evs)

        # macro events (Benzinga economics)
        if benzinga_client is not None:
            mx, mx_sources, mx_notes = macro_events_by_date(bz=benzinga_client, start=start, end=end)
            sources.extend(mx_sources)
            notes.extend(mx_notes)
            for d0, evs in mx.items():
                if d0 in events_by_date:
                    events_by_date[d0].extend(evs)
        else:
            notes.append("Benzinga unavailable or disabled; macro events omitted.")

    for k in list(events_by_date.keys()):
        events_by_date[k] = sorted([e for e in (events_by_date.get(k) or []) if isinstance(e, dict)], key=_event_sort_key)

    # Earnings - Try ORATS snapshot first, fall back to FMP live
    earnings_by_date: Dict[str, Dict[str, List[dict]]] = {k: {"BMO": [], "AMC": [], "UNK": []} for k in day_keys}
    debug_counts: Dict[str, Any] = {
        "earningsSource": None,
        "earningsRangeFrom": _fmt_date(start),
        "earningsRangeTo": _fmt_date(end),
        "universeMode": "sp500_nasdaq100",
        "universeSize": None,
        "earningsRowsFetched": 0,
        "earningsRowsUsed": 0,
        "earningsRowsDroppedUniverse": 0,
        "tickersInRange": 0,
        "snapshotUsed": False,
        "snapshotDate": None,
    }

    # Universe filter (default: sp500+nasdaq100). Use env override if you want "all".
    universe_mode = str(os.getenv("FMP_EARNINGS_UNIVERSE") or "sp500_nasdaq100").strip().lower()
    if not universe_mode:
        universe_mode = "sp500_nasdaq100"
    debug_counts["universeMode"] = universe_mode
    universe: Set[str] = set()
    if universe_mode != "all":
        universe = set(load_universe_sp500_and_nasdaq100())
    debug_counts["universeSize"] = int(len(universe)) if universe else None

    def _normalize_symbol_to_universe(sym: str) -> str:
        s = str(sym or "").strip().upper()
        if not s or not universe:
            return s
        if s in universe:
            return s
        if "-" in s:
            s2 = s.replace("-", ".")
            if s2 in universe:
                return s2
        if "." in s:
            s2 = s.replace(".", "-")
            if s2 in universe:
                return s2
        return s

    # --- ORATS SNAPSHOT PATH (preferred) ---
    snapshot_used = False
    if redis_store is not None:
        try:
            snapshot = load_earnings_snapshot(redis_store)
            if snapshot and isinstance(snapshot, dict):
                by_date = snapshot.get("byDate")
                meta = snapshot.get("meta") or {}
                if isinstance(by_date, dict) and by_date:
                    # Snapshot has data - count tickers in visible range
                    tickers_in_range = 0
                    for date_key in day_keys:
                        day_data = by_date.get(date_key)
                        if not day_data or not isinstance(day_data, dict):
                            continue
                        for timing in ("BMO", "AMC", "UNK"):
                            tickers = day_data.get(timing) or []
                            for ticker in tickers:
                                if isinstance(ticker, str) and ticker:
                                    earnings_by_date[date_key][timing].append({"ticker": ticker, "time": ""})
                                    tickers_in_range += 1
                    
                    # Only use snapshot if it has meaningful data (at least 5 tickers in range)
                    # Otherwise fall back to FMP which may have better coverage
                    MIN_SNAPSHOT_TICKERS = 5
                    if tickers_in_range >= MIN_SNAPSHOT_TICKERS:
                        debug_counts["earningsSource"] = "orats_snapshot"
                        debug_counts["snapshotUsed"] = True
                        debug_counts["snapshotDate"] = str(meta.get("etDate") or "")[:10]
                        debug_counts["tickersInRange"] = tickers_in_range
                        debug_counts["earningsRowsUsed"] = tickers_in_range
                        snapshot_used = True
                        LOG.info(f"Calendar: loaded {tickers_in_range} earnings from ORATS snapshot (date: {debug_counts['snapshotDate']})")
                    else:
                        # Snapshot too sparse - clear and fall back to FMP
                        for date_key in day_keys:
                            earnings_by_date[date_key] = {"BMO": [], "AMC": [], "UNK": []}
                        LOG.info(f"Calendar: ORATS snapshot too sparse ({tickers_in_range} tickers), falling back to FMP")
                        notes.append(f"ORATS snapshot sparse ({tickers_in_range} tickers in range), using FMP")
        except Exception as e:
            LOG.warning(f"Calendar: failed to load ORATS snapshot: {type(e).__name__}: {str(e)[:100]}")
            notes.append(f"ORATS snapshot load failed, falling back to FMP: {type(e).__name__}")

    # --- FMP LIVE FALLBACK ---
    if not snapshot_used:
        def _coerce_ticker_fmp(row: dict) -> str:
            for k in ("symbol", "ticker", "Symbol", "Ticker"):
                v = row.get(k)
                if v:
                    return str(v).strip().upper()
            return ""

        def _coerce_date_fmp(row: dict) -> Optional[dt.date]:
            for k in ("date", "earningDate", "earningsDate", "reportedDate", "Date"):
                v = row.get(k)
                d = _parse_date(v)
                if d is not None:
                    return d
            return None

        def _coerce_timing_fmp(row: dict) -> str:
            for k in ("time", "session", "when", "timing", "timeOfDay"):
                v = row.get(k)
                if v is None:
                    continue
                t = classify_timing(v)
                if t in ("AMC", "BMO"):
                    return t
                s = str(v).strip().lower()
                if "after" in s or "close" in s or s in ("amc", "afterclose", "post", "pm", "postmarket", "afterhours"):
                    return "AMC"
                if "before" in s or "open" in s or s in ("bmo", "beforeopen", "pre", "am", "premarket"):
                    return "BMO"
            return "UNK"

        if fmp_client is None:
            notes.append("FMP unavailable or disabled; earnings omitted.")
        else:
            try:
                resp = fmp_client.earnings_calendar(date_from=_fmt_date(start), date_to=_fmt_date(end), limit=int(max_tickers))
                rows = resp.rows or []
                debug_counts["earningsRowsFetched"] = int(len(rows))
                debug_counts["earningsSource"] = "fmp_live"

                # De-dupe (ticker+date+timing) and apply universe filter.
                uniq: Dict[Tuple[str, str, str], dict] = {}
                dropped = 0
                for r in rows:
                    if not isinstance(r, dict):
                        continue
                    sym0 = _coerce_ticker_fmp(r)
                    sym = _normalize_symbol_to_universe(sym0)
                    d = _coerce_date_fmp(r)
                    if not sym or d is None:
                        continue
                    if universe and sym not in universe:
                        dropped += 1
                        continue
                    tm = _coerce_timing_fmp(r)
                    uniq[(sym, _fmt_date(d), tm)] = r

                debug_counts["earningsRowsDroppedUniverse"] = int(dropped)

                tickers_in_range = 0
                for (sym, d0, tm), _r in uniq.items():
                    if d0 not in day_keys:
                        continue
                    earnings_by_date[d0][tm].append({"ticker": sym, "time": ""})
                    tickers_in_range += 1

                debug_counts["tickersInRange"] = int(tickers_in_range)
                debug_counts["earningsRowsUsed"] = int(tickers_in_range)
            except FmpError as e:
                notes.append(f"FMP earnings fetch failed: {str(e)[:180]}")
            except Exception as e:
                notes.append(f"FMP earnings fetch failed: {type(e).__name__}: {str(e)[:180]}")

    # Stable sort per day by ticker.
    for d0 in earnings_by_date.keys():
        for k in ("BMO", "AMC", "UNK"):
            earnings_by_date[d0][k] = sorted(earnings_by_date[d0][k], key=lambda x: str(x.get("ticker") or ""))

    out_days: List[Dict[str, Any]] = []
    for d in days:
        k = _fmt_date(d)
        out_days.append(
            {
                "date": k,
                "events": events_by_date.get(k) or [],
                "earnings": earnings_by_date.get(k) or {"BMO": [], "AMC": [], "UNK": []},
            }
        )

    return {
        "view": v,
        "tz": str(tz or "America/New_York"),
        "anchor": str(anchor)[:10],
        "range": {"start": _fmt_date(start), "end": _fmt_date(end)},
        "days": out_days,
        "meta": {
            "generatedAt": _fmt_date(dt.date.today()),
            "engine1Only": bool(engine1_only),
            "sourcesUsed": sorted(list(dict.fromkeys([*sources]))),
            "counts": debug_counts,
            "notes": notes,
        },
    }


