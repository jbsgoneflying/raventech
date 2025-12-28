from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from backend.benzinga_client import BenzingaClient


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
                importance=int(importance_min),
                country=str(country),
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
            ev = {
                "kind": kind_out,
                "title": title,
                "short": short,
                "importance": imp,
                "source": "benzinga",
            }
            out.setdefault(d0, []).append(ev)

        # Stable order within date: higher importance first, then title
        for d0 in list(out.keys()):
            out[d0] = sorted(out[d0], key=lambda e: (-(int(e.get("importance") or 0)), str(e.get("title") or "")))
    except Exception as e:
        notes.append(f"macro events unavailable: {type(e).__name__}: {e}")

    return out, sorted(list(dict.fromkeys(sources))), notes


