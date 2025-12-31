from __future__ import annotations

import datetime as dt
import statistics
from typing import Any, Dict, List, Optional, Tuple

from backend.benzinga_client import BenzingaClient
from backend.orats_client import OratsClient


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except Exception:
        return None


def _pct(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x) * 100.0, 3)

def _pts(x: Optional[float], spot: Optional[float]) -> Optional[float]:
    """
    Convert a return (fraction, e.g. 0.0023) into index points using the provided spot close.
    """
    if x is None or spot is None or spot <= 0:
        return None
    return round(float(x) * float(spot), 2)


def _median(xs: List[float]) -> Optional[float]:
    ys = [float(x) for x in xs if x is not None]
    if not ys:
        return None
    ys.sort()
    m = len(ys) // 2
    return ys[m] if (len(ys) % 2 == 1) else (ys[m - 1] + ys[m]) / 2.0


def _pctl(xs: List[float], p: float) -> Optional[float]:
    ys = [float(x) for x in xs if x is not None]
    if not ys:
        return None
    ys.sort()
    t = max(0.0, min(1.0, float(p) / 100.0))
    i = int(t * (len(ys) - 1))
    return ys[i]


def _event_key_from_name(name: str) -> Optional[str]:
    """
    Stable Top10-ish macro keys. This mirrors calendar-side mapping and intentionally stays simple.
    """
    n = str(name or "").strip().lower()
    if not n:
        return None
    if "cpi" in n and "core" in n:
        return "CPI"  # treat core/headline together for now
    if "cpi" in n:
        return "CPI"
    if "ppi" in n:
        return "PPI"
    if "retail" in n and "sales" in n:
        return "RETAIL_SALES"
    if "nonfarm" in n or "payroll" in n or "nfp" in n:
        return "NFP"
    if "jobless" in n or "claims" in n:
        return "JOBLESS_CLAIMS"
    if "pmi" in n or "ism" in n:
        return "PMI_ISM"
    if "fomc" in n and "minutes" in n:
        return "FOMC_MINUTES"
    if "fomc" in n or "interest rate decision" in n or "rate decision" in n:
        return "FOMC_RATE_DECISION"
    if "refunding" in n:
        return "TREASURY_REFUNDING"
    if "auction" in n or "treasury" in n or "t-bill" in n or "note auction" in n or "bond auction" in n:
        return "TREASURY_AUCTION"
    return None


def _fetch_benzinga_history(
    bz: BenzingaClient,
    *,
    key: str,
    start: dt.date,
    end: dt.date,
    pagesize: int = 1000,
    max_pages: int = 8,
    country: str = "US",
) -> List[dict]:
    rows_all: List[dict] = []
    for page in range(int(max_pages)):
        resp = bz.calendar_economics(date_from=_fmt_date(start), date_to=_fmt_date(end), pagesize=int(pagesize), page=int(page), country=country)
        batch = resp.rows or []
        rows_all.extend([r for r in batch if isinstance(r, dict)])
        if len(batch) < int(pagesize):
            break

    out: List[dict] = []
    for r in rows_all:
        d = _parse_date(r.get("date"))
        if d is None or d < start or d > end:
            continue
        name = str(r.get("event_name") or r.get("name") or "").strip()
        if not name:
            continue
        k = _event_key_from_name(name)
        if k != str(key).upper():
            continue
        out.append(r)
    # stable ordering
    out.sort(key=lambda x: str(x.get("date") or ""))
    return out


def _fetch_spy_closes(orats: OratsClient, *, start: dt.date, end: dt.date) -> Dict[str, float]:
    """
    Fetch SPY daily closes from ORATS once for a date range, keyed by YYYY-MM-DD.
    """
    fields = "ticker,tradeDate,clsPx,close"
    td = f"{_fmt_date(start)},{_fmt_date(end)}"
    rows = orats.hist_dailies("SPY", td, fields=fields).rows or []
    out: Dict[str, float] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        d0 = str(r.get("tradeDate") or "")[:10]
        c = _to_float(r.get("clsPx") or r.get("close"))
        if d0 and c is not None and c > 0:
            out[d0] = float(c)
    return out


def compute_macro_event_stats(
    *,
    key: str,
    bz: BenzingaClient,
    orats: OratsClient,
    lookback_years: int = 5,
    max_events: int = 60,
) -> Dict[str, Any]:
    """
    Compute simple reaction stats around a macro event type, using:
    - Benzinga economics history to get event dates
    - ORATS SPY daily closes to compute close->close returns
    """
    k = str(key or "").strip().upper()
    if not k:
        raise ValueError("Missing key")

    today = dt.date.today()
    start = today - dt.timedelta(days=int(lookback_years) * 365 + 30)
    end = today + dt.timedelta(days=3)

    ev_rows = _fetch_benzinga_history(bz, key=k, start=start, end=end)
    if not ev_rows:
        return {"enabled": False, "key": k, "notes": ["No events found for this key in the lookback window."]}

    # Use the most recent max_events (ensures deterministic size and reduces noise)
    ev_rows = ev_rows[-int(max_events) :]
    dates = [str(r.get("date") or "")[:10] for r in ev_rows if str(r.get("date") or "")[:10]]
    dates = sorted(list(dict.fromkeys(dates)))
    if len(dates) < 3:
        return {"enabled": False, "key": k, "notes": ["Insufficient event history to compute stats."]}

    # Pull SPY closes across the full range once.
    d0 = _parse_date(dates[0])
    d1 = _parse_date(dates[-1])
    if d0 is None or d1 is None:
        return {"enabled": False, "key": k, "notes": ["Invalid event dates returned by Benzinga."]}
    closes = _fetch_spy_closes(orats, start=(d0 - dt.timedelta(days=10)), end=(d1 + dt.timedelta(days=10)))
    trade_dates = sorted(closes.keys())
    spy_spot_close = float(closes.get(trade_dates[-1])) if trade_dates else None

    # Compute returns for each event date where neighboring closes exist.
    # We use close-to-close (event day and next day) since it is robust and deterministic.
    event_rets: List[float] = []
    next_rets: List[float] = []
    prev_rets: List[float] = []
    used_dates: List[str] = []

    # Build a sorted list of available trading dates to find prev/next close.
    idx = {d: i for i, d in enumerate(trade_dates)}

    for d in dates:
        i = idx.get(d)
        if i is None:
            continue
        if i - 1 < 0 or i + 1 >= len(trade_dates):
            continue
        d_prev = trade_dates[i - 1]
        d_next = trade_dates[i + 1]
        c_prev = closes.get(d_prev)
        c0 = closes.get(d)
        c_next = closes.get(d_next)
        if not c_prev or not c0 or not c_next:
            continue
        # prev-day drift (prev close vs prev2 close) if possible
        if i - 2 >= 0:
            d_prev2 = trade_dates[i - 2]
            c_prev2 = closes.get(d_prev2)
            if c_prev2 and c_prev2 > 0:
                prev_rets.append((c_prev / c_prev2) - 1.0)
        event_rets.append((c0 / c_prev) - 1.0)
        next_rets.append((c_next / c0) - 1.0)
        used_dates.append(d)

    n = len(event_rets)
    if n < 5:
        return {"enabled": False, "key": k, "notes": ["Too few matched events with trading-day closes to compute stable stats."]}

    def _summ(xs: List[float]) -> Dict[str, Any]:
        abs_xs = [abs(float(x)) for x in xs]
        med = _median(xs)
        p10 = _pctl(xs, 10)
        p90 = _pctl(xs, 90)
        med_abs = _median(abs_xs)
        p90_abs = _pctl(abs_xs, 90)
        return {
            "n": int(len(xs)),
            "medianPct": _pct(med),
            "p10Pct": _pct(p10),
            "p90Pct": _pct(p90),
            "medianAbsPct": _pct(med_abs),
            "p90AbsPct": _pct(p90_abs),
            "medianPts": _pts(med, spy_spot_close),
            "medianAbsPts": _pts(med_abs, spy_spot_close),
            "p90AbsPts": _pts(p90_abs, spy_spot_close),
            "stdevPct": _pct(statistics.stdev(xs)) if len(xs) >= 2 else None,
            "stdevPts": _pts((statistics.stdev(xs) if len(xs) >= 2 else None), spy_spot_close),
        }

    return {
        "enabled": True,
        "key": k,
        "lookbackYears": int(lookback_years),
        "eventsConsidered": int(len(dates)),
        "eventsUsed": int(n),
        "spySpotClose": (None if spy_spot_close is None else round(float(spy_spot_close), 2)),
        "spy": {
            "eventDayCloseToClose": _summ(event_rets),
            "nextDayCloseToClose": _summ(next_rets),
            "priorDayCloseToClose": _summ(prev_rets) if prev_rets else {"n": 0},
        },
        "notes": [
            "Reaction stats are computed on SPY close-to-close returns (risk-only).",
            "Use as context; not a prediction.",
        ],
    }


