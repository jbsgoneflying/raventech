from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from backend.benzinga_client import BenzingaClient
from backend.macro_playbook import get_playbook


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(float(v))
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


def _macro_key(name: str) -> Optional[str]:
    """
    Stable Top10-ish macro keys (used for playbook + stats lookups).
    Keep this explainable and resilient to naming differences.
    """
    n = str(name or "").strip().lower()
    if not n:
        return None
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


def _coerce_time_et(row: dict) -> Optional[str]:
    """
    Best-effort time-of-day extraction for display (ET).
    Benzinga economics commonly includes time fields like 'time' or 'time_us'.
    """
    for k in ("time", "time_us", "timeET", "time_et", "timeOfDay"):
        v = row.get(k)
        if not v:
            continue
        s = str(v).strip()
        # Normalize "HH:MM" if we got "HHMM"
        if len(s) == 4 and s.isdigit():
            return f"{s[:2]}:{s[2:]}"
        return s
    return None


def _coerce_number(row: dict, keys: List[str]) -> Optional[float]:
    for k in keys:
        v = _to_float(row.get(k))
        if v is not None:
            return float(v)
    return None


def _classify_macro(name: str) -> str:
    """
    Map Benzinga economics event_name into FED / TREASURY / ECON.
    Keep this simple + explainable; can be expanded later.
    """
    n = str(name or "").strip().lower()
    if not n:
        return "ECON"

    fed_hits = [
        "fomc",
        "fed",
        "powell",
        "press conference",
        "minutes",
        "beige book",
        "interest rate decision",
        "rate decision",
    ]
    if any(x in n for x in fed_hits):
        return "FED"

    tsy_hits = [
        "treasury",
        "refunding",
        "auction",
        "t-bill",
        "t bill",
        "bill auction",
        "note auction",
        "bond auction",
        "10-year",
        "10 year",
        "30-year",
        "30 year",
        "2-year",
        "2 year",
        "3-year",
        "3 year",
        "5-year",
        "5 year",
        "7-year",
        "7 year",
    ]
    if any(x in n for x in tsy_hits):
        return "TREASURY"

    return "ECON"


def _short_label(kind: str, name: str) -> str:
    n = str(name or "").strip()
    if not n:
        return kind.title()
    # keep calendar readable: use common acronyms when present
    up = n.upper()
    for k in ("CPI", "PCE", "NFP", "FOMC", "GDP", "PMI", "ISM"):
        if k in up:
            return k
    # otherwise truncate gently
    return n if len(n) <= 22 else (n[:21] + "…")


def macro_events_by_date(
    *,
    bz: BenzingaClient,
    start: dt.date,
    end: dt.date,
    pagesize: int = 1000,
    max_pages: int = 10,
    importance_min: int = 3,
    country: str = "US",
) -> Tuple[Dict[str, List[dict]], List[str], List[str]]:
    """
    Fetch Benzinga economics calendar once for a date range and return events grouped by date.
    """
    out: Dict[str, List[dict]] = {}
    sources: List[str] = []
    notes: List[str] = []
    try:
        rows_all: List[dict] = []
        for page in range(int(max_pages)):
            resp = bz.calendar_economics(
                date_from=_fmt_date(start),
                date_to=_fmt_date(end),
                pagesize=int(pagesize),
                page=int(page),
                # Be permissive on server-side filters; some plans/periods return sparse future data
                # when strict filters are applied. We'll filter client-side for US + importance.
                importance=None,
                country=None,
            )
            sources.append("benzinga:/calendar/economics")
            batch = resp.rows or []
            rows_all.extend([r for r in batch if isinstance(r, dict)])
            if len(batch) < int(pagesize):
                break

        for r in rows_all:
            d0 = str(r.get("date") or "")[:10]
            d = _parse_date(d0)
            if d is None or d < start or d > end:
                continue
            imp = _safe_int(r.get("importance")) or 0
            if imp < int(importance_min):
                continue
            ctry = str(r.get("country") or "").upper()
            if ctry and ctry not in ("US", "UNITED STATES", "USA"):
                continue
            name = str(r.get("event_name") or r.get("name") or "").strip()
            if not name:
                continue

            kind = _classify_macro(name)
            # Optional: importance>=4 "catch-all" as ECON even if classifier fails
            if kind not in ("FED", "TREASURY", "ECON"):
                kind = "ECON"
            kind_out = "ECON" if kind == "ECON" else kind
            title = name
            short = _short_label(kind_out, name)
            key = _macro_key(name)
            time_et = _coerce_time_et(r)

            # Forecast/actual/previous are optional and may not exist for all events.
            forecast = _coerce_number(r, ["forecast", "consensus", "estimate"])
            previous = _coerce_number(r, ["previous", "prior", "prev"])
            actual = _coerce_number(r, ["actual"])
            unit = str(r.get("unit") or r.get("units") or "").strip() or None
            period = str(r.get("period") or r.get("time_period") or "").strip() or None
            updated = r.get("updated") or r.get("updated_at") or r.get("updatedAt")

            ev = {
                "kind": kind_out,
                "title": title,
                "short": short,
                "importance": imp,
                "key": (str(key).upper() if key else None),
                "timeEt": time_et,
                "forecast": forecast,
                "previous": previous,
                "actual": actual,
                "unit": unit,
                "period": period,
                "updated": updated,
                "playbook": (get_playbook(key=str(key)) if key else None),
                "source": "benzinga",
            }
            out.setdefault(d0, []).append(ev)

        # Stable order within date: higher importance first, then title
        for d0 in list(out.keys()):
            out[d0] = sorted(out[d0], key=lambda e: (-(int(e.get("importance") or 0)), str(e.get("title") or "")))
    except Exception as e:
        notes.append(f"macro events unavailable: {type(e).__name__}: {e}")

    return out, sorted(list(dict.fromkeys(sources))), notes


