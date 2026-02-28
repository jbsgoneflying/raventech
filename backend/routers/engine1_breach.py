"""Engine 1 — Breach & Compare routes."""

from __future__ import annotations

import datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import replace
from typing import TYPE_CHECKING

from fastapi import APIRouter, HTTPException, Query

from backend.breach_ranker import rank_tickers, summarize_tiers
from backend.config import get_flags
from backend.deps import (
    LOG,
    breach_cache,
    breach_cache_key,
    breach_cache_lock,
    engine1_elig_cache,
    engine1_elig_cache_lock,
    get_benzinga_client_optional,
    get_client,
    get_client_optional,
)
from backend.earnings_gamma_context import compute_earnings_gamma_context
from backend.earnings_logic import BreachInputError, compute_breach_stats, compute_current_snapshot
from backend.go_no_go import compute_go_no_go
from backend.orats_client import OratsError

router = APIRouter()


@router.get("/api/breach")
def breach(
    ticker: str = Query(..., description="US equity ticker"),
    n: int = Query(20, ge=1, le=50),
    years: int = Query(5, ge=1, le=10),
    k: float = Query(1.0, gt=0.0),
    mode: str | None = Query(None, description="trade builder: auto|equal_delta|equal_premium"),
    symmetry: str | None = Query(None, description="trade builder: auto|symmetric|manual"),
    target_delta: float | None = Query(None, gt=0.0, lt=1.0),
    target_premium: float | None = Query(None, gt=0.0),
    wing_width: float | None = Query(None, gt=0.0),
    dte_target: int | None = Query(None, ge=1, le=60),
    exp: str | None = Query(None, description="trade builder expiration (YYYY-MM-DD)"),
    mc: bool | None = Query(None, description="enable Monte Carlo earnings gap risk outputs (additive)"),
    mc_opt: bool | None = Query(None, description="enable Monte Carlo wing optimization (risk-only)"),
    mc_stability: bool | None = Query(None, description="enable bootstrap stability + asymmetry caps (additive)"),
    mc_cond_quarter: bool | None = Query(None, description="MC conditioning: quarter"),
    mc_cond_regime: bool | None = Query(None, description="MC conditioning: regime"),
    mc_event_date: str | None = Query(None, description="manual next earnings date override (YYYY-MM-DD)"),
    mc_event_timing: str | None = Query(None, description="manual next earnings timing override (AMC|BMO)"),
):
    try:
        trade_builder_inputs = {
            "mode": mode,
            "symmetry": symmetry,
            "target_delta": target_delta,
            "target_premium": target_premium,
            "wing_width": wing_width,
            "dte_target": dte_target,
            "exp": exp,
        }
        has_trade_builder = any(v is not None for v in trade_builder_inputs.values())

        base_flags = get_flags()
        overrides = {}
        if mc is not None:
            overrides["ENABLE_MONTE_CARLO_EARNINGS"] = bool(mc)
        if mc_opt is not None:
            overrides["MC_ENABLE_WING_OPTIMIZATION"] = bool(mc_opt)
        if mc_stability is not None:
            overrides["MC_ENABLE_TAS_STABILITY"] = bool(mc_stability)
        if mc_cond_quarter is not None:
            overrides["MC_ENABLE_CONDITION_ON_QUARTER"] = bool(mc_cond_quarter)
        if mc_cond_regime is not None:
            overrides["MC_ENABLE_CONDITION_ON_REGIME"] = bool(mc_cond_regime)

        effective_flags = replace(base_flags, **overrides) if overrides else base_flags
        enable_mc = bool(effective_flags.ENABLE_MONTE_CARLO_EARNINGS)

        # MC depends on near-term anchoring (nextEvent/current snapshot); avoid mixing stale cached payloads.
        if enable_mc:
            has_trade_builder = True

        key = breach_cache_key(ticker, n, years, k, effective_flags.cache_fingerprint())
        if not has_trade_builder:
            with breach_cache_lock:
                cached = breach_cache.get(key)
            if cached is not None:
                # Refresh "current" snapshot even when the heavy payload is cached.
                # This prevents stale assumed-price/EM issues in the Trade Builder UI.
                try:
                    fresh = dict(cached)
                    client0 = get_client()
                    fresh["current"] = compute_current_snapshot(client=client0, ticker=ticker.strip().upper())
                    try:
                        bz_for_go = get_benzinga_client_optional() if bool(get_flags().ENABLE_BENZINGA) else None
                        fresh["goNoGo"] = compute_go_no_go(client0, ticker=ticker.strip().upper(), payload=fresh, benzinga_client=bz_for_go)
                    except Exception:
                        pass
                    return fresh
                except Exception:
                    return cached

        client = get_client()
        payload = compute_breach_stats(
            client=client,
            ticker=ticker,
            n=n,
            years=years,
            k=k,
            trade_builder_inputs=(trade_builder_inputs if has_trade_builder else None),
            flags_override=effective_flags,
            next_event_override={"date": mc_event_date, "timing": mc_event_timing},
            benzinga_client=get_benzinga_client_optional(),
        )

        # Inject Earnings Gamma Context (Raven-Tech 2.0)
        try:
            from backend.dealer_gamma_context import compute_dealer_gamma_context
            from backend.engine2_gamma_addons import compute_tail_ignition
            t_upper = ticker.strip().upper()
            rows = client.live_strikes(ticker=t_upper, fields="strike,gamma,callOpenInterest,putOpenInterest,spotPrice").rows or []
            if rows:
                dg = compute_dealer_gamma_context(rows)
                ti_data = compute_tail_ignition(client, t_upper)
                spot = None
                for r in rows:
                    if isinstance(r, dict) and r.get("spotPrice"):
                        spot = float(r["spotPrice"])
                        break
                current = payload.get("current") or {}
                im_pct = current.get("impliedMovePct")
                egc = compute_earnings_gamma_context(
                    ticker=t_upper,
                    as_of_date=dt.date.today().isoformat(),
                    dealer_gamma=dg,
                    tail_ignition=ti_data,
                    spot=spot,
                    implied_move_pct=im_pct,
                )
                payload["earningsGammaContext"] = egc.to_dict()
        except Exception as egc_err:
            LOG.debug(f"Earnings gamma context skipped for {ticker}: {egc_err}")

        if not has_trade_builder:
            with breach_cache_lock:
                breach_cache[key] = payload
        return payload
    except BreachInputError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except HTTPException:
        raise
    except OratsError as e:
        LOG.exception("ORATS failure")
        raise HTTPException(status_code=502, detail=str(e)) from e
    except Exception as e:
        LOG.exception("Unhandled failure")
        raise HTTPException(status_code=500, detail="Internal error") from e


@router.get("/api/breach-compare")
def breach_compare(
    tickers: str = Query(..., description="Comma-separated list of tickers (max 10)"),
    k: float = Query(1.0, gt=0.0, description="Breach multiple (1.0, 1.5, 2.0)"),
    n: int = Query(10, ge=1, le=50, description="Number of earnings events to analyze"),
    years: int = Query(3, ge=1, le=10, description="Lookback years"),
):
    """
    Compare and rank multiple tickers for earnings plays.

    Returns ranked list with composite scores based on:
    - Breach rate (25%)
    - IV elevation (20%)
    - EM richness (15%)
    - Liquidity (15%)
    - Tail coverage (10%)
    - Market regime (10%)
    - Event risk (5%)
    """
    try:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]
        ticker_list = list(dict.fromkeys(ticker_list))

        if not ticker_list:
            raise HTTPException(status_code=400, detail="No valid tickers provided")

        if len(ticker_list) > 10:
            raise HTTPException(status_code=400, detail="Maximum 10 tickers allowed")

        LOG.info(f"Breach compare: {len(ticker_list)} tickers at k={k}")

        client = get_client()
        benzinga_client = get_benzinga_client_optional()
        base_flags = get_flags()

        payloads: list[tuple[str, dict]] = []
        errors: list[dict] = []

        def fetch_single(ticker: str):
            """Fetch breach stats + goNoGo (for liquidity) for a single ticker."""
            payload = compute_breach_stats(
                client=client,
                ticker=ticker,
                n=n,
                years=years,
                k=k,
                trade_builder_inputs=None,
                flags_override=base_flags,
                benzinga_client=benzinga_client,
            )
            try:
                payload["goNoGo"] = compute_go_no_go(
                    client,
                    ticker=ticker,
                    payload=payload,
                    benzinga_client=benzinga_client,
                )
            except Exception as e:
                LOG.warning(f"goNoGo failed for {ticker}: {e}")
            return ticker, payload

        with ThreadPoolExecutor(max_workers=min(len(ticker_list), 5)) as executor:
            futures = {executor.submit(fetch_single, t): t for t in ticker_list}
            for future in as_completed(futures):
                ticker = futures[future]
                try:
                    _, payload = future.result(timeout=60)
                    payloads.append((ticker, payload))
                except Exception as e:
                    LOG.warning(f"Failed to fetch {ticker}: {e}")
                    errors.append({"ticker": ticker, "error": str(e)})

        rankings = rank_tickers(payloads)
        tier_summary = summarize_tiers(rankings)

        return {
            "asOfDate": dt.date.today().isoformat(),
            "k": k,
            "n": n,
            "years": years,
            "tickersRequested": len(ticker_list),
            "tickersAnalyzed": len(payloads),
            "summary": tier_summary,
            "rankings": rankings,
            "errors": errors if errors else None,
        }

    except HTTPException:
        raise
    except Exception as e:
        LOG.exception("Unhandled failure (breach-compare)")
        raise HTTPException(status_code=500, detail="Internal error") from e
