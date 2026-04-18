"""Engine 14 — IC Scenario Simulator routes."""

from __future__ import annotations

import datetime as dt
import logging
import os
import threading
from typing import Any, Dict, Optional

from cachetools import TTLCache
from fastapi import APIRouter, Body, Header, HTTPException, Query

from backend.config import get_flags
from backend.deps import get_benzinga_client_optional, get_client
from backend.engine14 import chain_cache, regime_features
from backend.engine14.conditioning import load_modifier_coefficients
from backend.engine14.simulator import IcScenarioRequest, run_scenario
from backend.engine2_trades import get_trade, log_trade
from backend.redis_store import get_store_optional

LOG = logging.getLogger("engine14.router")

router = APIRouter()

# Small request-level cache so repeated identical submissions (same request body)
# return in milliseconds rather than re-doing the replay loop.
_scenario_cache_lock = threading.Lock()
_scenario_cache: TTLCache = TTLCache(maxsize=512, ttl=10 * 60)


def _ensure_enabled() -> None:
    f = get_flags()
    if not getattr(f, "ENABLE_ENGINE14_IC_SCENARIO", False):
        raise HTTPException(status_code=404, detail="Engine 14 disabled (ENABLE_ENGINE14_IC_SCENARIO=0).")


def _parse_request(body: Dict[str, Any]) -> IcScenarioRequest:
    def _req_float(k: str) -> float:
        if k not in body or body[k] is None:
            raise HTTPException(status_code=400, detail=f"Missing required field: {k}")
        try:
            return float(body[k])
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Field {k} must be numeric.")

    def _req_str(k: str) -> str:
        if k not in body or not body[k]:
            raise HTTPException(status_code=400, detail=f"Missing required field: {k}")
        return str(body[k]).strip()

    underlying = str(body.get("underlying") or "SPX").upper()
    if underlying != "SPX":
        raise HTTPException(status_code=400, detail="Engine 14 supports SPX only in Phase 1.")

    f = get_flags()
    try:
        req = IcScenarioRequest(
            underlying=underlying,
            entry_date=_req_str("entryDate"),
            expiry=_req_str("expiry"),
            short_put=_req_float("shortPut"),
            long_put=_req_float("longPut"),
            short_call=_req_float("shortCall"),
            long_call=_req_float("longCall"),
            credit_received=_req_float("creditReceived"),
            profit_target_pct=float(body.get("profitTargetPct", f.ENGINE14_DEFAULT_PROFIT_TARGET_PCT)),
            stop_loss_pct=float(body.get("stopLossPct", f.ENGINE14_DEFAULT_STOP_LOSS_PCT)),
            season_mode=str(body.get("seasonMode") or "none"),
            season_value=(str(body.get("seasonValue")).strip() if body.get("seasonValue") else None),
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid request: {type(e).__name__}: {e}")

    # Sanity checks.
    if not (req.long_put < req.short_put < req.short_call < req.long_call):
        raise HTTPException(
            status_code=400,
            detail="Strikes must satisfy: longPut < shortPut < shortCall < longCall.",
        )
    if req.credit_received <= 0:
        raise HTTPException(status_code=400, detail="creditReceived must be positive.")
    if req.entry_date >= req.expiry:
        raise HTTPException(status_code=400, detail="expiry must be after entryDate.")
    return req


def _cache_key(req: IcScenarioRequest) -> tuple:
    f = get_flags()
    return (
        req.underlying, req.entry_date, req.expiry,
        req.short_put, req.long_put, req.short_call, req.long_call,
        round(float(req.credit_received), 4),
        float(req.profit_target_pct), float(req.stop_loss_pct),
        req.season_mode, req.season_value,
        f.cache_key_engine14(),
    )


@router.post("/api/ic-scenario")
def ic_scenario(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    _ensure_enabled()
    req = _parse_request(body)
    key = _cache_key(req)

    with _scenario_cache_lock:
        cached = _scenario_cache.get(key)
    if cached is not None:
        return cached

    try:
        client = get_client()
    except Exception as e:
        LOG.exception("engine14: ORATS client init failed")
        raise HTTPException(status_code=503, detail=f"ORATS client unavailable: {e}")

    try:
        bz = get_benzinga_client_optional()
    except Exception:
        bz = None
    try:
        store = get_store_optional()
    except Exception:
        store = None

    try:
        result = run_scenario(req, client=client, benzinga_client=bz, store=store)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        LOG.exception("engine14: run_scenario failed")
        raise HTTPException(status_code=500, detail=f"Scenario replay failed: {type(e).__name__}: {e}")

    with _scenario_cache_lock:
        _scenario_cache[key] = result
    return result


@router.get("/api/ic-scenario/health")
def ic_scenario_health() -> Dict[str, Any]:
    """Cache coverage + enablement probe, used by the UI before enabling the Run button."""
    f = get_flags()
    enabled = bool(getattr(f, "ENABLE_ENGINE14_IC_SCENARIO", False))
    try:
        cov = chain_cache.cache_coverage(ticker="SPX")
    except Exception as e:
        cov = {"ticker": "SPX", "daysCovered": 0, "error": f"{type(e).__name__}: {e}"}
    return {
        "enabled": enabled,
        "chainCache": cov,
        "minAnalogues": int(f.ENGINE14_MIN_ANALOGUES),
        "lookbackYears": int(f.ENGINE14_LOOKBACK_YEARS),
    }


@router.get("/api/ic-scenario/coverage")
def ic_scenario_coverage() -> Dict[str, Any]:
    _ensure_enabled()
    return {"SPX": chain_cache.cache_coverage(ticker="SPX")}


# ---------------------------------------------------------------------------
# Phase 3: trade-journal hand-off
# ---------------------------------------------------------------------------

@router.post("/api/ic-scenario/journal")
def ic_scenario_journal(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Persist a simulated IC to the Engine 2 trade journal.

    Expected body:
      { "scenario": <full payload from /api/ic-scenario>,
        "request":  <original form submission>,
        "note":     "optional free-text" }
    """
    _ensure_enabled()
    scenario = body.get("scenario") or {}
    form = body.get("request") or scenario.get("request") or {}
    if not form:
        raise HTTPException(status_code=400, detail="request payload missing.")

    # Normalize into the Engine 2 trade-log schema.
    strikes = {
        "shortPut": form.get("short_put") or form.get("shortPut"),
        "longPut": form.get("long_put") or form.get("longPut"),
        "shortCall": form.get("short_call") or form.get("shortCall"),
        "longCall": form.get("long_call") or form.get("longCall"),
    }
    trade_data = {
        "source": "engine14",
        "entry": {
            "underlying": str(form.get("underlying") or "SPX").upper(),
            "entryDate": form.get("entry_date") or form.get("entryDate"),
            "expiry": form.get("expiry"),
            "strikes": strikes,
            "creditReceived": form.get("credit_received") or form.get("creditReceived"),
            "profitTargetPct": form.get("profit_target_pct") or form.get("profitTargetPct"),
            "stopLossPct": form.get("stop_loss_pct") or form.get("stopLossPct"),
        },
        "entryContext": {
            "engine14Scenario": scenario,
            "note": str(body.get("note") or "").strip() or None,
        },
        "advisorVerdict": {
            "engine": 14,
            "expectedValue": scenario.get("expectedValue"),
            "outcomeDistribution": scenario.get("outcomeDistribution"),
            "adjustedOutcomeDistribution": scenario.get("adjustedOutcomeDistribution"),
            "exitRules": scenario.get("exitRulesOptimization"),
        },
    }

    trade_id = log_trade(trade_data)
    if trade_id is None:
        raise HTTPException(
            status_code=503,
            detail="Trade journal unavailable (Redis not configured).",
        )
    return {"tradeId": trade_id, "viewUrl": f"/spx?tradeId={trade_id}"}


@router.get("/api/ic-scenario/review")
def ic_scenario_review(trade_id: str = Query(..., alias="tradeId")) -> Dict[str, Any]:
    """Post-trade review: compare the stored simulation to the closed outcome."""
    _ensure_enabled()
    trade = get_trade(trade_id)
    if trade is None:
        raise HTTPException(status_code=404, detail=f"Trade {trade_id} not found.")

    scenario = ((trade.get("entryContext") or {}).get("engine14Scenario")) or {}
    if not scenario:
        raise HTTPException(
            status_code=400,
            detail="This trade has no Engine 14 scenario attached — nothing to review.",
        )

    base = scenario.get("outcomeDistribution") or {}
    adjusted = scenario.get("adjustedOutcomeDistribution") or {}
    predicted = {
        "meanPnlPct": (scenario.get("expectedValue") or {}).get("meanPnlPct"),
        "medianPnlPct": (scenario.get("expectedValue") or {}).get("medianPnlPct"),
        "fullCollectPct": (base.get("fullCollect") or {}).get("pct"),
        "earlyTargetPct": (base.get("earlyTarget") or {}).get("pct"),
        "breachPct": (base.get("breach") or {}).get("pct"),
        "stopOutPct": (base.get("stopOut") or {}).get("pct"),
    }
    predicted_adj = {
        "fullCollectPct": (adjusted.get("fullCollect") or {}).get("pct"),
        "earlyTargetPct": (adjusted.get("earlyTarget") or {}).get("pct"),
        "breachPct": (adjusted.get("breach") or {}).get("pct"),
        "stopOutPct": (adjusted.get("stopOut") or {}).get("pct"),
    } if adjusted else None

    status = str(trade.get("status") or "active")
    outcome = trade.get("outcome") or {}
    close_reason = trade.get("closeReason")
    actual: Dict[str, Any] = {
        "status": status,
        "closedAt": trade.get("closedAt"),
        "closeReason": close_reason,
    }
    if outcome:
        actual["pnlPct"] = outcome.get("pnlPct")
        actual["pnlDollars"] = outcome.get("pnlDollars")
        actual["daysHeld"] = outcome.get("daysHeld")

    # Verdict: was the sim roughly right?
    verdict: Optional[str] = None
    if status in ("closed",) and actual.get("pnlPct") is not None:
        actual_pnl = float(actual["pnlPct"])
        pred_mean = predicted.get("meanPnlPct")
        if pred_mean is not None:
            diff = actual_pnl - float(pred_mean)
            if abs(diff) <= 15.0:
                verdict = f"Sim within ±15pp of actual (Δ={diff:+.1f}pp)."
            elif diff > 0:
                verdict = f"Actual beat sim by {diff:.1f}pp — tailwinds stronger than modeled."
            else:
                verdict = f"Actual underperformed sim by {-diff:.1f}pp — headwinds stronger than modeled."

    return {
        "tradeId": trade_id,
        "predicted": predicted,
        "predictedAdjusted": predicted_adj,
        "actual": actual,
        "verdict": verdict,
        "scenarioVersion": scenario.get("version"),
        "analoguesUsed": scenario.get("analoguesUsed"),
    }


# ---------------------------------------------------------------------------
# Admin: backfill endpoint
# ---------------------------------------------------------------------------

_backfill_state: Dict[str, Any] = {"running": False, "started_at": None, "progress": None, "error": None}
_backfill_lock = threading.Lock()


def _check_admin_token(x_admin_token: Optional[str]) -> None:
    f = get_flags()
    expected = str(getattr(f, "ENGINE14_ADMIN_TOKEN", "") or os.getenv("ENGINE14_ADMIN_TOKEN", "")).strip()
    if not expected:
        raise HTTPException(
            status_code=503,
            detail="ENGINE14_ADMIN_TOKEN not configured on server.",
        )
    if not x_admin_token or str(x_admin_token).strip() != expected:
        raise HTTPException(status_code=401, detail="Invalid or missing X-Admin-Token.")


def _run_backfill_bg(*, years: float, max_dte: int, resume: bool, delay_ms: int) -> None:
    """Background worker: mirrors scripts/engine14_backfill_chains.py."""
    global _backfill_state
    import time
    from backend.orats_client import OratsClient, OratsError
    from backend.spx_ic.ohlc import fetch_dailies_ohlc_range
    try:
        today = dt.date.today()
        start = today - dt.timedelta(days=int(float(years) * 370))
        client = OratsClient.from_env()
        bars = fetch_dailies_ohlc_range(client, ticker="SPX", start=start, end=today)
        dates = [b.trade_date for b in bars if b.close is not None]
        if resume:
            cached = set(chain_cache.fetch_cached_trade_dates(ticker="SPX"))
            dates = [d for d in dates if d not in cached]
        total = len(dates)
        _backfill_state["progress"] = {"total": total, "completed": 0, "failed": 0}
        delay = max(0.0, float(delay_ms) / 1000.0)
        for i, td in enumerate(dates, start=1):
            try:
                chain_cache.fetch_and_cache_day(
                    client, ticker="SPX", trade_date=td, max_dte=int(max_dte),
                )
                _backfill_state["progress"]["completed"] = i
            except OratsError as e:
                LOG.warning("backfill: ORATS error at %s: %s", td, e)
                _backfill_state["progress"]["failed"] = (_backfill_state["progress"]["failed"] or 0) + 1
            except Exception as e:
                LOG.exception("backfill: unexpected error at %s: %s", td, e)
                _backfill_state["progress"]["failed"] = (_backfill_state["progress"]["failed"] or 0) + 1
            if delay:
                time.sleep(delay)
    except Exception as e:
        LOG.exception("backfill: fatal error")
        _backfill_state["error"] = f"{type(e).__name__}: {e}"
    finally:
        _backfill_state["running"] = False
        _backfill_state["finished_at"] = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z"


@router.post("/api/ic-scenario/backfill")
def ic_scenario_backfill(
    body: Dict[str, Any] = Body(default_factory=dict),
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
) -> Dict[str, Any]:
    """Kick off a background chain-cache backfill. Token-gated.

    Body: {"years": 2.0, "maxDte": 45, "resume": true, "delayMs": 250}
    Poll `/api/ic-scenario/backfill/status` for progress.
    """
    _ensure_enabled()
    _check_admin_token(x_admin_token)

    f = get_flags()
    years = float(body.get("years") or f.ENGINE14_LOOKBACK_YEARS)
    years = max(0.1, min(float(f.ENGINE14_BACKFILL_MAX_YEARS), years))
    max_dte = int(body.get("maxDte") or 45)
    resume = bool(body.get("resume", True))
    delay_ms = int(body.get("delayMs") or 250)

    with _backfill_lock:
        if _backfill_state.get("running"):
            raise HTTPException(status_code=409, detail="Backfill already in progress.")
        _backfill_state.update({
            "running": True,
            "started_at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
            "finished_at": None,
            "progress": None,
            "error": None,
            "params": {"years": years, "maxDte": max_dte, "resume": resume, "delayMs": delay_ms},
        })
        t = threading.Thread(
            target=_run_backfill_bg,
            kwargs={"years": years, "max_dte": max_dte, "resume": resume, "delay_ms": delay_ms},
            daemon=True,
            name="engine14-backfill",
        )
        t.start()
    return {"started": True, "params": _backfill_state["params"]}


@router.get("/api/ic-scenario/backfill/status")
def ic_scenario_backfill_status() -> Dict[str, Any]:
    """Open status endpoint (no token) — progress only, no destructive ops."""
    _ensure_enabled()
    cov = chain_cache.cache_coverage(ticker="SPX")
    return {
        "running": bool(_backfill_state.get("running")),
        "startedAt": _backfill_state.get("started_at"),
        "finishedAt": _backfill_state.get("finished_at"),
        "progress": _backfill_state.get("progress"),
        "error": _backfill_state.get("error"),
        "params": _backfill_state.get("params"),
        "coverage": cov,
    }


# ---------------------------------------------------------------------------
# Phase B: modifier-coefficients inspection
# ---------------------------------------------------------------------------

def _summarize_coeff_sources(coeffs: Dict[str, Any]) -> Dict[str, Dict[str, int]]:
    """Count hand_coded vs empirical buckets per modifier section."""
    out: Dict[str, Dict[str, int]] = {}
    cal_kws = ((coeffs.get("calendar") or {}).get("keywords")) or []
    out["calendar"] = {
        "empirical": sum(1 for r in cal_kws if r.get("source") == "empirical"),
        "handCoded": sum(1 for r in cal_kws if r.get("source") != "empirical"),
    }
    for section in ("dealerGamma", "creditStress", "gapRegime"):
        rows = (coeffs.get(section) or {})
        emp = sum(1 for v in rows.values() if isinstance(v, dict) and v.get("source") == "empirical")
        hc = sum(1 for v in rows.values() if isinstance(v, dict) and v.get("source") != "empirical")
        out[section] = {"empirical": int(emp), "handCoded": int(hc)}
    return out


@router.get("/api/ic-scenario/regime-features/coverage")
def ic_scenario_regime_features_coverage() -> Dict[str, Any]:
    """Open coverage probe for the Phase C1 multi-factor regime features store."""
    _ensure_enabled()
    try:
        cov = regime_features.coverage()
    except Exception as e:
        LOG.exception("regime features coverage failed")
        cov = {"error": f"{type(e).__name__}: {e}"}
    return {"store": cov}


@router.get("/api/ic-scenario/modifier-coefficients")
def ic_scenario_modifier_coefficients(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    reload: bool = Query(default=False, description="Force re-read from disk."),
) -> Dict[str, Any]:
    """Inspect the currently-loaded Phase B modifier coefficients.

    Token-gated because the learned values are considered tuning data.
    The returned payload includes the raw coefficients, the resolved
    source path, and a per-section hand-coded vs empirical tally.
    """
    _ensure_enabled()
    _check_admin_token(x_admin_token)
    f = get_flags()
    path = str(getattr(f, "ENGINE14_MODIFIER_COEFFICIENTS_PATH", "") or "")
    coeffs = load_modifier_coefficients(force_reload=bool(reload))
    return {
        "path": path,
        "exists": bool(path and os.path.exists(path)),
        "coefficients": coeffs,
        "sources": _summarize_coeff_sources(coeffs),
    }
