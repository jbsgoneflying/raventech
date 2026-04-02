"""Engine 4 (backend) / Engine 5 (UI): Ichimoku Cloud Continuation Scanner routes.

Backend module is named engine4 for historical reasons. Users see this as
Engine 5 in the navigation. See ENGINE_REGISTRY in config.py.
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
    get_benzinga_client_optional,
    engine4_cache,
    engine4_cache_lock,
)
from backend.engine4_screener import (
    run_universe_scan as compute_engine4_scan,
    scan_single_ticker as compute_engine4_single_ticker,
    get_all_signals as get_engine4_signals,
    refresh_signal_statuses as refresh_engine4_statuses,
)
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


@router.get("/api/engine4-ichimoku")
def engine4_ichimoku_scan(
    request: Request,
    date: Optional[str] = Query(None, description="Scan date (YYYY-MM-DD), defaults to today"),
    min_score: int = Query(50, ge=0, le=100, description="Minimum score to include"),
    direction: Optional[str] = Query(None, description="Filter by direction: bullish, bearish, or both"),
):
    """
    Engine 4: Ichimoku Cloud Continuation Scanner

    Scans SP500 + Nasdaq100 for Ichimoku continuation setups (Kijun pullback + Tenkan reclaim)
    with A+ quality scoring.

    Returns setups categorized by grade:
    - aPlus: Score >= 75 (high-quality setups)
    - others: Score 50-74 (decent setups)

    Features:
    - Standard Ichimoku settings (9/26/52)
    - Trend qualification (price vs cloud, Kijun slope)
    - Pullback detection (past Tenkan, near Kijun)
    - Entry triggers (Tenkan reclaim with candle quality)
    - Dealer gamma context (SPX for S&P, NDX for Nasdaq)
    - Earnings filter (downgrade if within 5 sessions)
    """
    flags = get_flags()
    if not flags.ENABLE_ENGINE4_ICHIMOKU:
        raise HTTPException(
            status_code=503,
            detail="Engine 4 (Ichimoku Continuation) is disabled. Set ENABLE_ENGINE4_ICHIMOKU=1 to enable.",
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
        with engine4_cache_lock:
            cached = engine4_cache.get(cache_key)
        if cached is not None:
            return cached

        benzinga_client = get_benzinga_client_optional()

        result = compute_engine4_scan(
            client,
            as_of_date=date,
            min_score=min_score,
            direction=dir_filter,
            benzinga_client=benzinga_client,
            max_workers=flags.ENGINE4_MAX_WORKERS,
        )

        if flags.ENABLE_GATING and isinstance(result, dict):
            try:
                gate_ctx = _get_gate_context(flags)
                for key in ("actionable", "structure", "watchlist"):
                    setups = result.get(key)
                    if isinstance(setups, list):
                        gate_scan_results(
                            scan_results=setups,
                            engine="engine4_ichimoku",
                            **gate_ctx,
                        )
                gs = summarize_gates(
                    (result.get("actionable") or []) + (result.get("structure") or [])
                )
                result["gateSummary"] = gs
                result["gateContext"] = gate_ctx
            except Exception as gate_err:
                LOG.warning(f"Gate injection failed for engine4: {gate_err}")

        with engine4_cache_lock:
            engine4_cache[cache_key] = result

        return result

    except HTTPException:
        raise
    except OratsError as e:
        LOG.exception("ORATS failure (engine4-ichimoku)")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        LOG.exception("Unhandled failure (engine4-ichimoku)")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/engine4-ichimoku/status")
def engine4_ichimoku_status(
    request: Request,
    refresh: bool = Query(False, description="Refresh signal statuses against current prices"),
    date: Optional[str] = Query(None, description="As-of date for refresh (YYYY-MM-DD)"),
):
    """
    Engine 4: Signal Status Tracker

    Returns current status of all tracked Ichimoku signals.

    If refresh=True, updates signal statuses based on current price action:
    - Checks if entry triggers have been hit
    - Checks if stops have been hit
    - Marks invalidated signals
    """
    flags = get_flags()
    if not flags.ENABLE_ENGINE4_ICHIMOKU:
        raise HTTPException(
            status_code=503,
            detail="Engine 4 (Ichimoku Continuation) is disabled.",
        )

    try:
        if refresh:
            client = get_client_optional()
            if client is None:
                raise HTTPException(status_code=503, detail="ORATS unavailable for refresh.")

            refresh_result = refresh_engine4_statuses(client, as_of_date=date)
            return {
                "refreshed": True,
                **refresh_result,
                "signals": get_engine4_signals(),
            }

        return {
            "refreshed": False,
            "signals": get_engine4_signals(),
        }

    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Unhandled failure (engine4-ichimoku/status)")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/engine4-ichimoku/{ticker}")
def engine4_ichimoku_ticker(
    request: Request,
    ticker: str,
    date: Optional[str] = Query(None, description="Scan date (YYYY-MM-DD), defaults to today"),
):
    """
    Engine 4: Single ticker Ichimoku analysis

    Analyzes a specific ticker for Ichimoku continuation setup with full details:
    - Complete Ichimoku state (Tenkan, Kijun, cloud, Chikou)
    - Trend regime qualification
    - Pullback state machine
    - Entry trigger detection
    - A+ scoring breakdown
    - Dealer gamma context
    """
    flags = get_flags()
    if not flags.ENABLE_ENGINE4_ICHIMOKU:
        raise HTTPException(
            status_code=503,
            detail="Engine 4 (Ichimoku Continuation) is disabled.",
        )

    try:
        client = get_client_optional()
        if client is None:
            raise HTTPException(status_code=503, detail="ORATS unavailable (missing ORATS_TOKEN).")

        t = str(ticker or "").strip().upper()
        if not t:
            raise HTTPException(status_code=400, detail="Missing ticker.")

        benzinga_client = get_benzinga_client_optional()

        result = compute_engine4_single_ticker(
            client,
            ticker=t,
            as_of_date=date,
            benzinga_client=benzinga_client,
        )

        return result

    except HTTPException:
        raise
    except OratsError as e:
        LOG.exception(f"ORATS failure (engine4-ichimoku/{ticker})")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        LOG.exception(f"Unhandled failure (engine4-ichimoku/{ticker})")
        raise HTTPException(status_code=500, detail="Internal error") from e
