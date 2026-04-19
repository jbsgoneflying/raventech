"""Engine 14 — IC Scenario Simulator routes."""

from __future__ import annotations

import concurrent.futures
import datetime as dt
import logging
import os
import threading
from typing import Any, Dict, Optional

from cachetools import TTLCache
from fastapi import APIRouter, Body, Header, HTTPException, Query

from backend.config import get_flags
from backend.deps import get_benzinga_client_optional, get_client
from backend.engine14 import chain_cache, reconciliation, regime_features
from backend.engine14.card_explain import (
    CARD_CATALOG,
    generate_card_explanation,
    supported_card_types,
)
from backend.engine14.conditioning import load_modifier_coefficients
from backend.engine14.live_chain import fetch_live_chain_nbbo, validate_strikes_exist
from backend.engine14.simulator import IcScenarioRequest, run_scenario
from backend.engine2_trades import get_trade, log_trade
from backend.redis_store import get_store_optional
from backend.spx_ic import compute_engine2_spx_ic

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


# ---------------------------------------------------------------------------
# Engine-14 ↔ Engine-2 reconciliation endpoint (Stage 1 + 1.5)
# ---------------------------------------------------------------------------

_reconcile_cache_lock = threading.Lock()
_reconcile_cache: TTLCache = TTLCache(maxsize=256, ttl=5 * 60)
_reconcile_executor = concurrent.futures.ThreadPoolExecutor(
    max_workers=4, thread_name_prefix="engine14-reconcile",
)


def _compute_engine2_payload(under: str) -> Dict[str, Any]:
    """Run a vanilla Engine-2 scan with default params for reconciliation."""
    try:
        return compute_engine2_spx_ic(
            client=get_client(),
            benzinga_client=get_benzinga_client_optional(),
            flags=get_flags(),
            underlying_preference=under,
            entry_day="mon",
            years=3,
            widths=[0.8, 1.0, 1.2, 1.5, 2.0],
            risk_target_breach_pct=25.0,
            seasonality_mode="none",
        )
    except Exception as e:
        LOG.warning("reconcile: Engine-2 scan failed: %s", e)
        return {}


def _compute_advisor_with_timeout(
    engine2_payload: Dict[str, Any],
    timeout_s: float,
) -> Optional[Dict[str, Any]]:
    """Run the LLM advisor off-thread with a hard wall-clock timeout."""
    f = get_flags()
    if not getattr(f, "ENGINE2_ADVISOR_ENABLED", False):
        return None
    if not engine2_payload or not engine2_payload.get("current"):
        return None

    # Local import keeps the router import-light when advisor is disabled.
    from backend.engine2_advisor import generate_trade_analysis

    def _run() -> Dict[str, Any]:
        return generate_trade_analysis(
            engine2_payload=engine2_payload,
            width_analysis=engine2_payload.get("widthComparison"),
            flags=f,
        )

    fut = _reconcile_executor.submit(_run)
    try:
        return fut.result(timeout=float(timeout_s))
    except concurrent.futures.TimeoutError:
        LOG.info("reconcile: advisor timed out after %.1fs", timeout_s)
        fut.cancel()
        return None
    except Exception as e:
        LOG.warning("reconcile: advisor failed: %s", e)
        return None


@router.post("/api/ic-scenario/reconcile")
def ic_scenario_reconcile(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Cross-check a simulated scenario against Engine 2 + LLM advisor + live chain.

    Body can be either:

      * Previously-run output of ``/api/ic-scenario`` — pass under key
        ``scenario``. This avoids re-running the sim.
      * A raw scenario request — we run the simulation ourselves.

    Options:

      * ``runAdvisor`` (default True): kick off the LLM advisor (async,
        12s timeout). Set False for a fast deterministic-only reconcile.
      * ``checkLiveChain`` (default True): pull live NBBO for the four
        legs and include it as a credit anchor.
      * ``engine2`` (optional): pre-computed E2 payload. Saves ~2s.
    """
    _ensure_enabled()

    scenario = body.get("scenario")
    if not isinstance(scenario, dict) or not scenario.get("entryState"):
        # Treat the body itself as a scenario request.
        req = _parse_request(body.get("request") or body)
        try:
            client = get_client()
        except Exception as e:
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
            scenario = run_scenario(req, client=client, benzinga_client=bz, store=store)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            LOG.exception("reconcile: run_scenario failed")
            raise HTTPException(status_code=500, detail=f"Scenario replay failed: {type(e).__name__}: {e}")

    # Engine 2 scan — prefer caller-supplied payload; otherwise compute fresh.
    e2_payload = body.get("engine2")
    if not isinstance(e2_payload, dict) or not e2_payload:
        under = str((scenario.get("request") or {}).get("underlying") or "SPX").upper()
        e2_payload = _compute_engine2_payload(under)

    run_advisor = bool(body.get("runAdvisor", True))
    check_chain = bool(body.get("checkLiveChain", True))
    advisor_timeout_s = float(body.get("advisorTimeoutSeconds") or 12.0)

    advisor: Optional[Dict[str, Any]] = None
    live_chain: Optional[Dict[str, Any]] = None
    errors: Dict[str, str] = {}

    # Kick the advisor off FIRST so it runs concurrently with the live-chain fetch.
    advisor_fut = None
    if run_advisor and e2_payload:
        f = get_flags()
        if getattr(f, "ENGINE2_ADVISOR_ENABLED", False):
            advisor_fut = _reconcile_executor.submit(
                _compute_advisor_with_timeout, e2_payload, advisor_timeout_s,
            )

    if check_chain:
        try:
            req_fields = scenario.get("request") or {}
            client = get_client()
            live_chain = fetch_live_chain_nbbo(
                client,
                ticker=str(req_fields.get("underlying") or "SPX").upper(),
                expiry=str(req_fields.get("expiry") or ""),
                short_put=float(req_fields.get("short_put")),
                long_put=float(req_fields.get("long_put")),
                short_call=float(req_fields.get("short_call")),
                long_call=float(req_fields.get("long_call")),
            )
        except Exception as e:
            LOG.warning("reconcile: live chain fetch failed: %s", e)
            errors["liveChain"] = f"{type(e).__name__}: {e}"

    if advisor_fut is not None:
        try:
            advisor = advisor_fut.result(timeout=float(advisor_timeout_s) + 2.0)
        except concurrent.futures.TimeoutError:
            errors["advisor"] = f"timeout after {advisor_timeout_s}s"
        except Exception as e:
            errors["advisor"] = f"{type(e).__name__}: {e}"

    reconcile_payload = reconciliation.reconcile_full(
        scenario_result=scenario,
        engine2_payload=e2_payload or {},
        engine2_advisor=advisor,
        live_chain=live_chain,
    )

    return {
        "reconcile": reconcile_payload,
        "scenario": scenario,
        "engine2": {
            "asOfDate": (e2_payload or {}).get("asOfDate"),
            "current": (e2_payload or {}).get("current"),
            "expectedMove": (e2_payload or {}).get("expectedMove"),
            "strikeTargets": (e2_payload or {}).get("strikeTargets"),
            "deskConsensus": (e2_payload or {}).get("deskConsensus"),
            "recommendation": (e2_payload or {}).get("recommendation"),
            "widthComparison": (e2_payload or {}).get("widthComparison"),
            "oddsLikeNow": (e2_payload or {}).get("oddsLikeNow"),
        },
        "advisor": advisor,
        "liveChain": live_chain,
        "errors": errors or None,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
    }


# ---------------------------------------------------------------------------
# Pre-submit guardrails (Stage 3)
# ---------------------------------------------------------------------------

def _pre_check_block(kind: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"severity": "block", "kind": kind, "message": message, **extra}


def _pre_check_warn(kind: str, message: str, **extra: Any) -> Dict[str, Any]:
    return {"severity": "warn", "kind": kind, "message": message, **extra}


@router.post("/api/ic-scenario/pre-check")
def ic_scenario_pre_check(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Fast pre-submit guardrails for the scenario form.

    Responsibilities:

      * Hard-block when any of the four strikes does not exist on the
        live option chain for the requested expiry. The response includes
        a ``suggestion`` with nearest-available strikes so the UI can
        offer a one-click fix.
      * Warn when the user-typed credit is outside the live NBBO or far
        off the width-comparison proxy / advisor estimate.
      * Warn when the user's EM multiple is below Engine 2's
        ``deskConsensus.suggestedEmFloor``.
      * Warn when the chosen cell violates the Engine 2 policy
        thresholds (breach / outside / MAE).

    Intentionally avoids the LLM advisor (sync path, sub-second).
    """
    _ensure_enabled()

    def _req_float(k: str) -> float:
        v = body.get(k)
        if v is None or v == "":
            raise HTTPException(status_code=400, detail=f"Missing required field: {k}")
        try:
            return float(v)
        except (TypeError, ValueError):
            raise HTTPException(status_code=400, detail=f"Field {k} must be numeric.")

    underlying = str(body.get("underlying") or "SPX").upper()
    if underlying != "SPX":
        raise HTTPException(status_code=400, detail="Engine 14 supports SPX only.")
    expiry = str(body.get("expiry") or "").strip()
    if not expiry:
        raise HTTPException(status_code=400, detail="expiry is required.")

    short_put = _req_float("shortPut")
    long_put = _req_float("longPut")
    short_call = _req_float("shortCall")
    long_call = _req_float("longCall")
    credit = _req_float("creditReceived")

    if not (long_put < short_put < short_call < long_call):
        raise HTTPException(
            status_code=400,
            detail="Strikes must satisfy: longPut < shortPut < shortCall < longCall.",
        )

    blocks: list[Dict[str, Any]] = []
    warnings: list[Dict[str, Any]] = []

    # --- Strike existence on the live chain -------------------------------
    try:
        client = get_client()
    except Exception as e:
        return {
            "ok": True,
            "blocks": [],
            "warnings": [_pre_check_warn(
                "liveChainUnavailable",
                f"Live chain unavailable ({type(e).__name__}); proceeding without strike verification.",
            )],
            "liveChain": None,
            "suggestion": None,
        }

    strike_check = validate_strikes_exist(
        client, ticker=underlying, expiry=expiry,
        short_put=short_put, long_put=long_put,
        short_call=short_call, long_call=long_call,
    )

    suggestion: Optional[Dict[str, Any]] = None
    if strike_check.get("expiryFound") and not strike_check.get("ok"):
        missing = strike_check.get("missing") or []
        blocks.append(_pre_check_block(
            "missingStrike",
            f"{len(missing)} leg(s) do not exist for {underlying} {expiry}.",
            missing=missing,
        ))
        # Build a full suggestion struct with nearest-strike replacements.
        fix = {
            "shortPut": short_put, "longPut": long_put,
            "shortCall": short_call, "longCall": long_call,
        }
        for m in missing:
            fix[m["leg"]] = m["nearest"]
        suggestion = {"strikes": fix}

    if not strike_check.get("expiryFound"):
        warnings.append(_pre_check_warn(
            "liveChainUnavailable",
            f"No live chain data for {underlying} {expiry}. Strike existence not verified.",
        ))

    # --- Live NBBO credit anchor ------------------------------------------
    live_chain: Optional[Dict[str, Any]] = None
    if strike_check.get("ok"):
        try:
            live_chain = fetch_live_chain_nbbo(
                client, ticker=underlying, expiry=expiry,
                short_put=short_put, long_put=long_put,
                short_call=short_call, long_call=long_call,
            )
        except Exception as e:
            LOG.warning("pre-check: live NBBO fetch failed: %s", e)

    if live_chain:
        mid = float(live_chain.get("mid") or 0.0)
        net_bid = live_chain.get("netBid")
        net_ask = live_chain.get("netAsk")
        inside = True
        if net_bid is not None and credit < float(net_bid) - 1e-6:
            inside = False
        if net_ask is not None and credit > float(net_ask) + 1e-6:
            inside = False
        if not inside:
            warnings.append(_pre_check_warn(
                "creditOutsideNBBO",
                f"User credit ${credit:.2f} is outside live NBBO "
                f"[${net_bid:.2f}, ${net_ask:.2f}] for mid ${mid:.2f}.",
                userCredit=credit, nbbo={"bid": net_bid, "ask": net_ask, "mid": mid},
            ))
        elif mid > 0 and abs(credit - mid) / mid > 0.25:
            warnings.append(_pre_check_warn(
                "creditFarFromMid",
                f"User credit ${credit:.2f} is >25% off live mid ${mid:.2f}.",
                userCredit=credit, mid=mid,
            ))

    # --- Engine 2 policy / floor / box ------------------------------------
    try:
        e2 = _compute_engine2_payload(underlying)
    except Exception:
        e2 = {}

    if e2:
        # Synthesize a minimal "scenario-like" dict so we can reuse the
        # single-check helpers from reconciliation.
        em = e2.get("expectedMove") or {}
        spot = float(em.get("smartSpotPrice") or em.get("spotPrice") or 0.0) or None
        put_dist = abs(spot - short_put) if spot else 0.0
        call_dist = abs(short_call - spot) if spot else 0.0
        em_pct = float(em.get("oratsExpectedMovePct") or em.get("delayedImpliedMovePct") or 0.0) or None
        em_dollars = (em_pct / 100.0) * spot if (em_pct and spot) else None
        user_em_mult = None
        if em_dollars and em_dollars > 0:
            user_em_mult = round(((put_dist + call_dist) / 2.0) / em_dollars, 2)
        wing_width = float(min(short_put - long_put, long_call - short_call))

        synthetic_scenario = {
            "request": {
                "short_put": short_put, "long_put": long_put,
                "short_call": short_call, "long_call": long_call,
                "credit_received": credit, "underlying": underlying,
                "expiry": expiry,
            },
            "entryState": {
                "userSpot": spot,
                "userEmPct": em_pct,
                "userEmMultiple": user_em_mult,
                "wingWidth": wing_width,
                "regimeBucket": ((e2.get("current") or {}).get("regime") or {}).get("bucket"),
                "regimeSource": "em_proxy",
            },
        }

        policy_chip = reconciliation._check_policy(synthetic_scenario, e2)
        floor_chip = reconciliation._check_desk_floor(synthetic_scenario, e2)
        box_chip = reconciliation._check_em_multiple_label(synthetic_scenario, e2)

        if policy_chip["status"] == "mismatch":
            warnings.append(_pre_check_warn(
                "policyMultipleViolations",
                policy_chip["note"],
                chip=policy_chip,
            ))
        elif policy_chip["status"] == "drift":
            warnings.append(_pre_check_warn(
                "policyDrift",
                policy_chip["note"],
                chip=policy_chip,
            ))

        if floor_chip["status"] == "mismatch":
            warnings.append(_pre_check_warn(
                "belowDeskEmFloor",
                floor_chip["note"],
                chip=floor_chip,
            ))

        if box_chip["status"] in ("drift", "mismatch"):
            warnings.append(_pre_check_warn(
                "emMultipleMisaligned",
                box_chip["note"],
                chip=box_chip,
            ))

    return {
        "ok": len(blocks) == 0,
        "blocks": blocks,
        "warnings": warnings,
        "liveChain": live_chain,
        "availableStrikes": strike_check.get("availableStrikes") or [],
        "suggestion": suggestion,
    }


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
# Per-card LLM explainer — desk tooltips that describe what each results
# card is, how to read it, and how to use it on a live trade.
# ---------------------------------------------------------------------------

@router.get("/api/ic-scenario/explain-card/catalog")
def ic_scenario_explain_catalog() -> Dict[str, Any]:
    """List every card that supports the /explain-card endpoint.

    Handy for the frontend to validate its `data-explain` slugs against
    the backend catalog at boot.
    """
    _ensure_enabled()
    return {
        "cardTypes": supported_card_types(),
        "titles": {k: v.get("title", k) for k, v in CARD_CATALOG.items()},
    }


@router.post("/api/ic-scenario/explain-card")
def ic_scenario_explain_card(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Return an LLM-generated, desk-friendly explanation for one card.

    Expected body:
      {
        "cardType":        "entry_state" | "outcome_distribution" | ... ,
        "cardData":        <JSON-serializable card payload as displayed>,
        "scenarioContext": <optional: strikes/credit/expiry/analoguesUsed...>
      }
    """
    _ensure_enabled()

    card_type = str(body.get("cardType") or "").strip()
    if not card_type:
        raise HTTPException(status_code=400, detail="cardType is required.")
    if card_type not in CARD_CATALOG:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown cardType {card_type!r}. "
                f"Valid: {', '.join(supported_card_types())}"
            ),
        )

    card_data = body.get("cardData")
    if card_data is None:
        # Empty dict is fine — some cards (e.g. conditioning_notes) may be
        # empty at the time the user clicks. Don't 400 on that.
        card_data = {}

    scenario_context = body.get("scenarioContext") or {}

    return generate_card_explanation(
        card_type=card_type,
        card_data=card_data,
        scenario_context=scenario_context,
    )


# ---------------------------------------------------------------------------
# Phase 3: trade-journal hand-off
# ---------------------------------------------------------------------------

@router.post("/api/ic-scenario/journal")
def ic_scenario_journal(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Persist a simulated IC to the Engine 2 trade journal.

    Expected body:
      { "scenario":  <full payload from /api/ic-scenario>,
        "request":   <original form submission>,
        "reconcile": <optional: full /reconcile payload. If omitted, we
                     compute a fresh one here so the trade record always
                     captures what the desk saw at entry>,
        "engine2":   <optional: already-fetched Engine 2 scan to avoid
                     re-running it during snapshot capture>,
        "note":      "optional free-text" }
    """
    _ensure_enabled()
    scenario = body.get("scenario") or {}
    form = body.get("request") or scenario.get("request") or {}
    if not form:
        raise HTTPException(status_code=400, detail="request payload missing.")

    # --- Capture a reconcile snapshot at entry -------------------------------
    # Callers that just ran /reconcile can pass the payload through; otherwise
    # we synthesize a deterministic-only snapshot (cheap, no LLM / live NBBO)
    # so every logged trade carries a "what the desk knew at entry" chip.
    reconcile_full_payload: Optional[Dict[str, Any]] = None
    raw_reconcile = body.get("reconcile")
    if isinstance(raw_reconcile, dict) and raw_reconcile.get("overall"):
        reconcile_full_payload = raw_reconcile
    elif scenario:
        e2_payload = body.get("engine2")
        if not isinstance(e2_payload, dict) or not e2_payload:
            under = str(form.get("underlying") or "SPX").upper()
            e2_payload = _compute_engine2_payload(under) or {}
        try:
            reconcile_full_payload = reconciliation.reconcile_deterministic(
                scenario_result=scenario,
                engine2_payload=e2_payload,
            )
        except Exception:
            LOG.exception("journal: reconcile_deterministic failed; logging trade without snapshot")
            reconcile_full_payload = None

    reconcile_snapshot = reconciliation.summarize_for_journal(reconcile_full_payload)

    # Normalize into the Engine 2 trade-log schema.
    strikes = {
        "shortPut": form.get("short_put") or form.get("shortPut"),
        "longPut": form.get("long_put") or form.get("longPut"),
        "shortCall": form.get("short_call") or form.get("shortCall"),
        "longCall": form.get("long_call") or form.get("longCall"),
    }
    entry_context: Dict[str, Any] = {
        "engine14Scenario": scenario,
        "note": str(body.get("note") or "").strip() or None,
    }
    if reconcile_snapshot:
        entry_context["reconcile"] = reconcile_snapshot

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
        "entryContext": entry_context,
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
    return {
        "tradeId": trade_id,
        "viewUrl": f"/spx?tradeId={trade_id}",
        "reconcile": reconcile_snapshot,
    }


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

    entry_reconcile = (trade.get("entryContext") or {}).get("reconcile")

    return {
        "tradeId": trade_id,
        "predicted": predicted,
        "predictedAdjusted": predicted_adj,
        "actual": actual,
        "verdict": verdict,
        "scenarioVersion": scenario.get("version"),
        "analoguesUsed": scenario.get("analoguesUsed"),
        "entryReconcile": entry_reconcile,
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
