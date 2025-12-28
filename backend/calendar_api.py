from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.benzinga_client import BenzingaClient
from backend.orats_client import OratsClient
from backend.earnings_calendar import infer_timing_from_time_str
from backend.market_calendar import market_structure_events_by_date, opex_events_by_date
from backend.macro_events import macro_events_by_date


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
        return False, {"reason": f"orats_error:{type(e).__name__}"}
    ok, diag = engine1_is_eligible_from_orats_row(row if isinstance(row, dict) else {}, policy=policy)
    return ok, diag


def _fetch_benzinga_earnings_range(
    bz: BenzingaClient,
    *,
    start: dt.date,
    end: dt.date,
    max_pages: int = 25,
    pagesize: int = 1000,
) -> List[dict]:
    rows_all: List[dict] = []
    for page in range(int(max_pages)):
        resp = bz.calendar_earnings(
            date_from=_fmt_date(start),
            date_to=_fmt_date(end),
            pagesize=int(pagesize),
            page=int(page),
        )
        batch = resp.rows or []
        rows_all.extend([r for r in batch if isinstance(r, dict)])
        if len(batch) < int(pagesize):
            break
    return rows_all


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
    orats_client: OratsClient,
    benzinga_client: Optional[BenzingaClient],
    policy: Engine1UniversePolicy,
    eligibility_cache: Dict[str, Tuple[bool, Dict[str, Any]]],
    max_tickers_considered: int = 2000,
) -> Dict[str, Any]:
    v = str(view or "month").lower().strip()
    if v not in ("month", "week", "day"):
        v = "month"

    a = _parse_date(anchor) or dt.date.today()
    if v == "month":
        m0 = _first_of_month(a)
        m1 = _last_of_month(a)
        # Pad to a full Mon..Sun grid so the month UI aligns to weekdays.
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

    # Earnings (Benzinga calendar)
    earnings_by_date: Dict[str, Dict[str, List[dict]]] = {k: {"BMO": [], "AMC": [], "UNK": []} for k in day_keys}
    earn_sources: List[str] = []

    if benzinga_client is None:
        notes.append("Benzinga unavailable or disabled; earnings calendar omitted.")
    else:
        try:
            rows = _fetch_benzinga_earnings_range(benzinga_client, start=start, end=end)
            earn_sources.append("benzinga:/calendar/earnings")
        except Exception as e:
            rows = []
            notes.append(f"earnings fetch failed: {type(e).__name__}: {e}")

        # Normalize + pre-filter to our visible days.
        norm: List[dict] = []
        tickers: Set[str] = set()
        for r in rows:
            d0 = str(r.get("date") or r.get("earnings_date") or "")[:10]
            if d0 not in day_keys:
                continue
            t = str(r.get("ticker") or r.get("symbol") or "").strip().upper()
            if not t:
                continue
            norm.append(r)
            tickers.add(t)
            if len(tickers) >= int(max_tickers_considered):
                notes.append(f"ticker cap hit ({max_tickers_considered}); results may be incomplete.")
                break

        eligible: Set[str] = set(tickers)
        eligibility_diags: Dict[str, Any] = {}
        if engine1_only and tickers:
            eligible = set()
            for t in sorted(tickers):
                cached = eligibility_cache.get(t)
                if cached is None:
                    ok, diag = fetch_engine1_eligibility(orats_client, ticker=t, policy=policy)
                    eligibility_cache[t] = (ok, diag)
                else:
                    ok, diag = cached
                if ok:
                    eligible.add(t)
                # keep a small diagnostic sample
                if len(eligibility_diags) < 12:
                    eligibility_diags[t] = diag

        # Populate per-day groups.
        for r in norm:
            d0 = str(r.get("date") or r.get("earnings_date") or "")[:10]
            t = str(r.get("ticker") or r.get("symbol") or "").strip().upper()
            if not t or t not in eligible:
                continue
            timing = infer_timing_from_time_str(r.get("time"))
            if timing not in ("AMC", "BMO"):
                timing = "UNK"
            earnings_by_date[d0][timing].append({"ticker": t, "time": str(r.get("time") or "")})

        # Stable sort per day by ticker.
        for d0 in earnings_by_date.keys():
            for k in ("BMO", "AMC", "UNK"):
                earnings_by_date[d0][k] = sorted(earnings_by_date[d0][k], key=lambda x: str(x.get("ticker") or ""))

        if engine1_only:
            notes.append("Engine‑1 filter applied using ORATS /cores snapshot (cached).")
            if eligibility_diags:
                # Helpful for debugging in early rollout; keep small.
                notes.append(f"eligibilitySample={eligibility_diags}")

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
            "sourcesUsed": sorted(list(dict.fromkeys([*sources, *earn_sources]))),
            "notes": notes,
        },
    }


