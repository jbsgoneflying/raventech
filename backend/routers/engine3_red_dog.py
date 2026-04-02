"""Engine 3 (backend) / Engine 4 (UI): Red Dog Reversal Scanner routes.

Backend module is named engine3 for historical reasons. Users see this as
Engine 4 in the navigation. See ENGINE_REGISTRY in config.py.
"""
from __future__ import annotations

import logging
import threading
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request

from backend.config import get_flags
from backend.deps import (
    LOG,
    get_client,
    get_client_optional,
    engine3_cache,
    engine3_cache_lock,
)
from backend.engine3_screener import compute_engine3_scan, compute_single_ticker_scan
from backend.gating import gate_scan_results, summarize_gates
from backend.orats_client import OratsError

router = APIRouter()


def _get_gate_context(flags) -> dict:
    """Gather regime and vol context for gating decisions."""
    ctx: dict = {
        "regime_label": "",
        "vol_direction": "",
        "gamma_ctx": None,
        "high_events_within_days": 0,
    }
    try:
        from backend.redis_store import get_store_optional

        store = get_store_optional()
        if store and flags.ENABLE_ENGINE5_LEAD_LAG:
            from backend.engine5_snapshot import select_best_snapshot

            snap = select_best_snapshot(
                store,
                max_age_days=flags.ENGINE5_SNAPSHOT_BEST_MAX_AGE_DAYS,
                snapshot_ttl=flags.ENGINE5_SNAPSHOT_TTL_S,
            )
            if snap:
                data = snap.get("data", {})
                regime = data.get("regime", {})
                ctx["regime_label"] = regime.get("label") or regime.get("current_label") or ""
                vol = data.get("volLeadLag", {})
                ctx["vol_direction"] = vol.get("global_vol_direction") or vol.get("globalVolDirection") or ""
    except Exception:
        pass
    return ctx


@router.get("/api/engine3-red-dog")
def engine3_red_dog_scan(
    request: Request,
    date: Optional[str] = Query(None, description="Scan date (YYYY-MM-DD), defaults to today"),
    min_score: int = Query(50, ge=0, le=100, description="Minimum score to include"),
    direction: Optional[str] = Query(None, description="Filter by direction: bullish, bearish, or both"),
):
    """
    Engine 3: Red Dog Reversal Scanner

    Scans SP500 + Nasdaq100 (516 tickers) for Red Dog Reversal setups with A+ quality scoring.

    Returns setups categorized by grade:
    - aPlus: Score >= 75 (high-quality setups)
    - standard: Score 50-74 (decent setups)
    - watchlist: Combined and sorted by score
    """
    flags = get_flags()
    if not flags.ENABLE_ENGINE3_RED_DOG:
        raise HTTPException(
            status_code=503,
            detail="Engine 3 (Red Dog Reversal) is disabled. Set ENABLE_ENGINE3_RED_DOG=1 to enable.",
        )

    try:
        client = get_client_optional()
        if client is None:
            raise HTTPException(status_code=503, detail="ORATS unavailable (missing ORATS_TOKEN).")

        dir_filter = None
        if direction:
            d = str(direction).strip().lower()
            if d in ("bullish", "bull", "long"):
                dir_filter = "bullish"
            elif d in ("bearish", "bear", "short"):
                dir_filter = "bearish"

        cache_key = (date, min_score, dir_filter)
        with engine3_cache_lock:
            cached = engine3_cache.get(cache_key)
        if cached is not None:
            return cached

        result = compute_engine3_scan(
            client,
            as_of_date=date,
            min_score=min_score,
            direction=dir_filter,
            max_workers=flags.ENGINE3_MAX_WORKERS,
            use_cache=True,
        )

        if flags.ENABLE_GATING and isinstance(result, dict):
            try:
                gate_ctx = _get_gate_context(flags)
                for key in ("aPlus", "standard", "watchlist"):
                    setups = result.get(key)
                    if isinstance(setups, list):
                        gate_scan_results(
                            scan_results=setups,
                            engine="engine3_red_dog",
                            **gate_ctx,
                        )
                gs = summarize_gates(
                    (result.get("aPlus") or []) + (result.get("standard") or [])
                )
                result["gateSummary"] = gs
                result["gateContext"] = gate_ctx
            except Exception as gate_err:
                LOG.warning(f"Gate injection failed for engine3: {gate_err}")

        with engine3_cache_lock:
            engine3_cache[cache_key] = result

        return result

    except HTTPException:
        raise
    except OratsError as e:
        LOG.exception("ORATS failure (engine3-red-dog)")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        LOG.exception("Unhandled failure (engine3-red-dog)")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/engine3-red-dog/{ticker}")
def engine3_red_dog_ticker(
    request: Request,
    ticker: str,
    date: Optional[str] = Query(None, description="Scan date (YYYY-MM-DD), defaults to today"),
):
    """
    Engine 3: Single ticker Red Dog analysis

    Analyzes a specific ticker for Red Dog Reversal setup with full indicator details.
    """
    flags = get_flags()
    if not flags.ENABLE_ENGINE3_RED_DOG:
        raise HTTPException(
            status_code=503,
            detail="Engine 3 (Red Dog Reversal) is disabled. Set ENABLE_ENGINE3_RED_DOG=1 to enable.",
        )

    try:
        client = get_client_optional()
        if client is None:
            raise HTTPException(status_code=503, detail="ORATS unavailable (missing ORATS_TOKEN).")

        t = str(ticker or "").strip().upper()
        if not t:
            raise HTTPException(status_code=400, detail="Missing ticker.")

        result = compute_single_ticker_scan(
            client,
            ticker=t,
            as_of_date=date,
        )

        return result

    except HTTPException:
        raise
    except OratsError as e:
        LOG.exception(f"ORATS failure (engine3-red-dog/{ticker})")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        LOG.exception(f"Unhandled failure (engine3-red-dog/{ticker})")
        raise HTTPException(status_code=500, detail="Internal error") from e
