from __future__ import annotations

import datetime as dt
import json
import logging
import threading

from cachetools import TTLCache
from fastapi import APIRouter, HTTPException, Query

from backend.deps import (
    LOG,
    get_client,
    get_client_optional,
    get_benzinga_client_optional,
    get_fmp_client_optional,
    get_api_ninjas_client_optional,
    condor_rank_cache,
    condor_rank_cache_lock,
    macro_stats_cache,
    macro_stats_cache_lock,
)
from backend.config import get_flags
from backend.orats_client import OratsError
from backend.earnings_logic import BreachInputError
from backend.condor_rank import compute_condor_rank
from backend.macro_event_stats import compute_macro_event_stats

router = APIRouter()

_news_risk_cache: TTLCache = TTLCache(maxsize=10, ttl=30 * 60)
_news_risk_cache_lock = threading.Lock()


@router.get("/api/condor-rank")
def condor_rank(
    ticker: str = Query(..., description="US equity ticker"),
    n: int = Query(20, ge=5, le=50),
    years: int = Query(5, ge=1, le=10),
):
    """
    Iron Condor Rank endpoint (lightweight, cached).
    """
    try:
        t = str(ticker or "").strip().upper()
        key = (t, int(n), int(years), get_flags().cache_fingerprint())
        with condor_rank_cache_lock:
            cached = condor_rank_cache.get(key)
        if cached is not None:
            return cached

        payload = compute_condor_rank(get_client(), ticker=t, n=int(n), years=int(years))
        with condor_rank_cache_lock:
            condor_rank_cache[key] = payload
        return payload
    except BreachInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except OratsError as e:
        LOG.exception("ORATS failure (condor-rank)")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        LOG.exception("Unhandled failure (condor-rank)")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/macro-event-stats")
def macro_event_stats(
    key: str = Query(..., description="Macro event key (e.g., CPI, FOMC_RATE_DECISION, NFP)"),
    lookback_years: int = Query(5, ge=1, le=10),
    max_events: int = Query(60, ge=10, le=200),
):
    """
    On-demand macro event reaction stats (risk-only).
    Uses Benzinga economics history + SPY close-to-close returns.
    Cached to avoid repeated computation.
    """
    try:
        k = str(key or "").strip().upper()
        if not k:
            raise HTTPException(status_code=400, detail="Missing key.")
        cache_key = (k, int(lookback_years), int(max_events))
        with macro_stats_cache_lock:
            cached = macro_stats_cache.get(cache_key)
        if cached is not None:
            return cached

        bz = get_benzinga_client_optional()
        if bz is None:
            raise HTTPException(status_code=503, detail="Benzinga unavailable or disabled.")
        client = get_client_optional()
        if client is None:
            raise HTTPException(status_code=503, detail="ORATS unavailable (missing ORATS_TOKEN).")

        payload = compute_macro_event_stats(
            key=k,
            bz=bz,
            orats=client,
            lookback_years=int(lookback_years),
            max_events=int(max_events),
        )
        with macro_stats_cache_lock:
            macro_stats_cache[cache_key] = payload
        return payload
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Unhandled failure (macro-event-stats)")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/news-risk")
def news_risk(
    week_offset: int = Query(0, ge=-12, le=12, description="Week offset: 0=current, 1=next, -1=last"),
):
    """
    News Risk Engine: Weekly view of macro events, analyst ratings, and news headlines
    with historical SPX impact data for event risk planning.
    """
    from backend.news_risk import build_news_risk_payload

    try:
        cache_key = ("news_risk", int(week_offset))
        with _news_risk_cache_lock:
            cached = _news_risk_cache.get(cache_key)
        if cached is not None:
            return cached

        bz = get_benzinga_client_optional()
        if bz is None:
            raise HTTPException(status_code=503, detail="Benzinga unavailable or disabled.")

        orats = get_client_optional()
        if orats is None:
            raise HTTPException(status_code=503, detail="ORATS unavailable (missing ORATS_TOKEN).")

        payload = build_news_risk_payload(
            bz=bz,
            orats=orats,
            week_offset=int(week_offset),
        )

        with _news_risk_cache_lock:
            _news_risk_cache[cache_key] = payload
        return payload
    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Unhandled failure (news-risk)")
        raise HTTPException(status_code=500, detail="Internal error") from e
