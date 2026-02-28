from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse

from backend.deps import (
    LOG,
    get_client,
    get_client_optional,
    get_benzinga_client_optional,
    get_fmp_client_optional,
    get_api_ninjas_client_optional,
    calendar_cache,
    calendar_cache_lock,
)
from backend.config import get_flags
from backend.orats_client import OratsError
from backend.calendar_api import build_calendar_payload
from backend.calendar_snapshot import EARNINGS_SNAPSHOT_KEY, load_earnings_snapshot
from backend.fmp_snapshot import FMP_EARNINGS_SNAPSHOT_KEY, load_fmp_earnings_snapshot
from backend.redis_store import get_store_optional
from backend.api_ninjas_client import ApiNinjasError

router = APIRouter()


@router.get("/api/earnings-calendar")
async def earnings_calendar_api(
    view: str = Query("month", description="month|week"),
    anchor: str = Query("", description="YYYY-MM-DD anchor date"),
):
    """Earnings calendar for $100B+ market-cap companies (EODHD-only)."""
    import calendar as _cal
    from backend.eodhd_earnings_calendar import get_earnings_calendar

    today = dt.date.today()
    try:
        anchor_date = dt.date.fromisoformat(anchor[:10]) if anchor else today
    except Exception:
        anchor_date = today

    if view == "week":
        monday = anchor_date - dt.timedelta(days=anchor_date.weekday())
        start = monday
        end = monday + dt.timedelta(days=6)
        label = f"Week of {start.strftime('%b %d, %Y')}"
    else:
        first_of_month = anchor_date.replace(day=1)
        _, days_in_month = _cal.monthrange(first_of_month.year, first_of_month.month)
        last_of_month = first_of_month.replace(day=days_in_month)
        start = first_of_month - dt.timedelta(days=first_of_month.weekday())
        end = last_of_month + dt.timedelta(days=6 - last_of_month.weekday())
        label = first_of_month.strftime("%B %Y")

    import asyncio
    try:
        loop = asyncio.get_event_loop()
        days = await loop.run_in_executor(None, lambda: get_earnings_calendar(start, end))
    except Exception as exc:
        LOG.exception("Earnings calendar failed")
        raise HTTPException(status_code=500, detail=str(exc)) from exc

    return {
        "view": view,
        "anchor": anchor_date.isoformat(),
        "start": start.isoformat(),
        "end": end.isoformat(),
        "label": label,
        "days": days,
    }


@router.get("/api/calendar")
def calendar(
    view: str = Query("month", description="month|week|day"),
    anchor: str = Query(None, description="YYYY-MM-DD (anchor date)"),
    tz: str = Query("America/New_York"),
    engine1Only: int = Query(0, ge=0, le=1),
    includeEvents: int = Query(1, ge=0, le=1),
    maxTickers: int = Query(12000, ge=200, le=50000),
    minMarketCap: float = Query(0, ge=0, description="Min market cap filter in billions (e.g., 100 = $100B+)"),
):
    """
    Earnings calendar endpoint for the front page.

    Design goals:
    - One response for the visible range (month/week/day)
    - Macro events fetched once per range (Benzinga economics)
    - Earnings data from API Ninjas Premium
    """
    try:
        a = str(anchor or dt.date.today().isoformat())[:10]
        v = str(view or "month").strip().lower()
        if v not in ("month", "week", "day"):
            raise HTTPException(status_code=400, detail="Unsupported view. Allowed: month|week|day")
        e1 = bool(int(engine1Only))
        inc = bool(int(includeEvents))
        min_mcap_b = float(minMarketCap) if minMarketCap else 0.0

        flags_fp = get_flags().cache_fingerprint()
        cache_ttl_s = int(float(os.getenv("CALENDAR_CACHE_TTL_S") or 0))
        key = ("calendar", v, a, str(tz or ""), int(e1), int(inc), int(maxTickers), flags_fp)
        if cache_ttl_s > 0:
            with calendar_cache_lock:
                cached = calendar_cache.get(key)
            if cached is not None:
                return cached

        payload = build_calendar_payload(
            view=v,
            anchor=a,
            tz=tz,
            engine1_only=e1,
            include_events=inc,
            benzinga_client=get_benzinga_client_optional(),
            max_tickers=int(maxTickers),
            min_market_cap_b=min_mcap_b,
            api_ninjas_client=get_api_ninjas_client_optional(),
        )
        if cache_ttl_s > 0:
            with calendar_cache_lock:
                calendar_cache[key] = payload
        return payload
    except HTTPException:
        raise
    except ApiNinjasError as e:
        LOG.exception("API Ninjas failure (calendar)")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        LOG.exception("Unhandled failure (calendar)")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/transcripts/{ticker}")
def get_transcript_list(ticker: str):
    """
    Get list of available earnings call transcripts for a ticker.
    Returns the 4 most recent transcripts.
    """
    try:
        client = get_api_ninjas_client_optional()
        if client is None:
            raise HTTPException(status_code=503, detail="API Ninjas unavailable")

        ticker = str(ticker).upper().strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker required")

        transcripts = client.get_latest_transcripts(ticker, limit=4)
        return {
            "ticker": ticker,
            "transcripts": transcripts,
            "count": len(transcripts),
        }
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception(f"Failed to fetch transcript list for {ticker}")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/transcripts/{ticker}/{year}/{quarter}")
def get_transcript(ticker: str, year: int, quarter: int):
    """
    Get full earnings call transcript for a specific quarter.
    Returns the transcript text.
    """
    try:
        client = get_api_ninjas_client_optional()
        if client is None:
            raise HTTPException(status_code=503, detail="API Ninjas unavailable")

        ticker = str(ticker).upper().strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker required")
        if year < 2000 or year > 2100:
            raise HTTPException(status_code=400, detail="Invalid year")
        if quarter < 1 or quarter > 4:
            raise HTTPException(status_code=400, detail="Quarter must be 1-4")

        transcript = client.get_transcript(ticker, year, quarter)
        if transcript is None:
            raise HTTPException(status_code=404, detail=f"No transcript found for {ticker} {year}Q{quarter}")

        return transcript
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception(f"Failed to fetch transcript for {ticker} {year}Q{quarter}")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/transcripts/{ticker}/{year}/{quarter}/download")
def download_transcript(ticker: str, year: int, quarter: int):
    """
    Download earnings call transcript as a .txt file.
    """
    from fastapi.responses import Response

    try:
        client = get_api_ninjas_client_optional()
        if client is None:
            raise HTTPException(status_code=503, detail="API Ninjas unavailable")

        ticker = str(ticker).upper().strip()
        if not ticker:
            raise HTTPException(status_code=400, detail="Ticker required")
        if year < 2000 or year > 2100:
            raise HTTPException(status_code=400, detail="Invalid year")
        if quarter < 1 or quarter > 4:
            raise HTTPException(status_code=400, detail="Quarter must be 1-4")

        transcript = client.get_transcript(ticker, year, quarter)
        if transcript is None:
            raise HTTPException(status_code=404, detail=f"No transcript found for {ticker} {year}Q{quarter}")

        date_str = transcript.get("date", "Unknown date")
        timing = transcript.get("earnings_timing", "unknown")
        text = transcript.get("transcript", "No transcript available")

        content = f"""EARNINGS CALL TRANSCRIPT
========================
Ticker: {ticker}
Date: {date_str}
Quarter: Q{quarter} {year}
Timing: {timing}

========================
TRANSCRIPT
========================

{text}
"""

        filename = f"{ticker}_Q{quarter}_{year}_transcript.txt"

        return Response(
            content=content,
            media_type="text/plain",
            headers={
                "Content-Disposition": f'attachment; filename="{filename}"'
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception(f"Failed to download transcript for {ticker} {year}Q{quarter}")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/calendar-snapshot-status")
def calendar_snapshot_status():
    """
    Lightweight diagnostics for calendar earnings snapshots in Redis.

    Purpose: quickly confirm whether the calendar is using the FMP snapshot
    or falling back to the legacy ORATS snapshot (which can anchor estimates to Wednesday).
    """
    store = get_store_optional()
    if store is None:
        return {
            "ok": False,
            "redisAvailable": False,
            "error": "Redis unavailable (missing REDIS_URL).",
        }
    if not store.ping():
        return {
            "ok": False,
            "redisAvailable": False,
            "error": "Redis ping failed.",
        }

    def _summarize(snap):
        if not isinstance(snap, dict):
            return {"present": False, "meta": None, "byDateSize": 0}
        meta = snap.get("meta") if isinstance(snap.get("meta"), dict) else None
        by_date = snap.get("byDate") if isinstance(snap.get("byDate"), dict) else {}
        return {
            "present": True,
            "meta": meta,
            "byDateSize": int(len(by_date)),
        }

    fmp = _summarize(load_fmp_earnings_snapshot(store))
    orats = _summarize(load_earnings_snapshot(store))
    return {
        "ok": True,
        "redisAvailable": True,
        "keys": {
            "fmp": {"key": FMP_EARNINGS_SNAPSHOT_KEY, **fmp},
            "orats": {"key": EARNINGS_SNAPSHOT_KEY, **orats},
        },
    }


@router.get("/api/calendar-debug-earnings")
def calendar_debug_earnings(
    ticker: str = Query("TSLA", description="Ticker to probe (optional)"),
    date_from: str = Query(..., description="YYYY-MM-DD"),
    date_to: str = Query(..., description="YYYY-MM-DD"),
    max_rows: int = Query(2000, ge=1, le=20000),
):
    """
    Debug helper to diagnose missing tickers in the calendar.

    Returns a sanitized subset of Benzinga /calendar/earnings rows for the given date range,
    optionally filtered to a specific ticker.
    """
    try:
        bz = get_benzinga_client_optional()
        if bz is None:
            raise HTTPException(status_code=503, detail="Benzinga unavailable or disabled.")

        d0 = str(date_from)[:10]
        d1 = str(date_to)[:10]
        t = str(ticker or "").strip().upper()

        pagesize = 1000
        max_pages = 50
        rows_all: list[dict] = []
        for page in range(max_pages):
            resp = bz.calendar_earnings(
                tickers=(t if t else None),
                date_from=d0,
                date_to=d1,
                pagesize=pagesize,
                page=page,
            )
            batch = resp.rows or []
            rows_all.extend([r for r in batch if isinstance(r, dict)])
            if len(batch) < pagesize:
                break

        out_rows: list[dict] = []
        for r in rows_all:
            sym = str(r.get("ticker") or r.get("symbol") or "").strip().upper()
            if t and sym != t:
                continue
            out_rows.append(
                {
                    "ticker": sym,
                    "date": str(r.get("date") or r.get("earnings_date") or "")[:10],
                    "time": str(r.get("time") or ""),
                    "date_confirmed": r.get("date_confirmed"),
                    "updated": r.get("updated") or r.get("updated_at") or r.get("updatedAt"),
                }
            )
            if len(out_rows) >= int(max_rows):
                break

        by_day: dict[str, int] = {}
        for r in out_rows:
            dd = str(r.get("date") or "")[:10]
            if dd:
                by_day[dd] = int(by_day.get(dd, 0)) + 1

        return {
            "range": {"from": d0, "to": d1},
            "tickerFilter": t or None,
            "counts": {
                "rowsFetchedAll": len(rows_all),
                "rowsReturned": len(out_rows),
                "pagesize": pagesize,
                "maxPages": max_pages,
            },
            "byDay": {k: by_day[k] for k in sorted(by_day.keys())},
            "rows": out_rows,
        }
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Unhandled failure (calendar-debug-earnings)")
        raise HTTPException(status_code=500, detail="Internal error") from e
