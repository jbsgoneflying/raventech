"""RTv2.0 — API Endpoints.

All /api/rtv2/ endpoints for the unified trading desk system.
Mounted as a sub-router in app.py.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from backend.redis_store import get_store_optional
from backend.rtv2_capital_allocator import (
    build_allocation,
    compute_weekly_adjustments,
    derive_ru,
    load_allocation,
    persist_allocation,
    bucket_for_engine,
    ENGINE_BUCKET_MAP,
    ENGINE_RU_HARD_CAP,
    PORTFOLIO_RU_HARD_CAP,
)
from backend.rtv2_portfolio_state import (
    build_portfolio_snapshot,
    load_portfolio,
    persist_portfolio,
)
from backend.rtv2_cross_engine_scorer import (
    compute_ups,
    rank_signals,
    record_engine_score,
)
from backend.rtv2_trade_lifecycle import (
    TradeRecord,
    create_trade,
    create_manual_trade,
    transition,
    activate_trade,
    check_expirations,
    persist_trade,
    load_trade,
    load_active_trades,
)
from backend.rtv2_position_monitor import (
    evaluate_all_positions,
    positions_summary,
)
from backend.rtv2_performance_feedback import (
    TradeOutcome,
    create_outcome,
    persist_outcome,
    load_all_outcomes,
    refresh_all_metrics,
    load_engine_metrics,
    load_bucket_metrics,
    engine_hit_rate_bonus,
    compute_bucket_streaks,
)
from backend.rtv2_risk_manager import (
    check_trade_risk,
    build_risk_dashboard,
)
from backend.daily_market_state import load_dms as _load_dms_by_date, DailyMarketState
from backend.rtv2_integration import ingest_engine_signals

LOG = logging.getLogger(__name__)
router = APIRouter(prefix="/api/rtv2", tags=["rtv2"])

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
PORTFOLIO_CAPITAL = float(os.getenv("RTV2_PORTFOLIO_CAPITAL", "500000"))


def _store():
    return get_store_optional()


def _get_dms_dict() -> Optional[dict]:
    """Load today's DailyMarketState from Redis."""
    store = _store()
    if store is None:
        return None
    today = dt.date.today().isoformat()
    dms = _load_dms_by_date(today, store)
    if dms is None:
        return None
    return dms.to_dict()


def _get_regime(dms: Optional[dict] = None) -> str:
    if dms is None:
        dms = _get_dms_dict()
    if dms is None:
        return "Transitional"
    regime = dms.get("regime")
    if isinstance(regime, dict):
        return str(regime.get("state", "Transitional"))
    return str(regime or "Transitional")


# ---------------------------------------------------------------------------
# Pydantic models for request bodies
# ---------------------------------------------------------------------------

class ManualTradeRequest(BaseModel):
    ticker: str
    direction: str = "long"
    entry_price: float
    units: int
    trade_type: str
    thesis_stop: float
    thesis_target: float
    thesis_max_days: int = 10
    invalidation_conditions: List[str] = Field(default_factory=list)
    bucket: str = "directional"
    sector: str = ""
    notes: str = ""


class ThesisUpdateRequest(BaseModel):
    thesis_stop: Optional[float] = None
    thesis_target: Optional[float] = None
    thesis_max_days: Optional[int] = None
    invalidation_conditions: Optional[List[str]] = None


class StageRequest(BaseModel):
    pass


class ActivateRequest(BaseModel):
    entry_price: float
    units: int
    direction: str = "long"
    max_loss_per_unit: float = 0.0
    thesis_target: float = 0.0
    thesis_stop: float = 0.0
    thesis_max_days: int = 0
    invalidation_conditions: List[str] = Field(default_factory=list)
    trade_type: str = ""


class CloseRequest(BaseModel):
    pnl_dollars: float = 0.0
    exit_reason: str = "desk_discretion"


# ---------------------------------------------------------------------------
# Page route
# ---------------------------------------------------------------------------

@router.get("/page", include_in_schema=False)
def rtv2_page():
    """Serve the RTv2.0 Dashboard page."""
    page_path = STATIC_DIR / "rtv2.html"
    if not page_path.exists():
        raise HTTPException(status_code=500, detail="Missing static/rtv2.html")
    return FileResponse(str(page_path))


# ---------------------------------------------------------------------------
# Portfolio state
# ---------------------------------------------------------------------------

@router.get("/portfolio-state")
def api_portfolio_state():
    """Current portfolio snapshot with allocations, positions, health."""
    store = _store()
    dms = _get_dms_dict()
    regime = _get_regime(dms)

    active = load_active_trades(store) if store else []
    active_dicts = [t.to_dict() for t in active]

    prices: Dict[str, float] = {}
    for t in active_dicts:
        prices[t.get("ticker", "")] = t.get("entry_price", 0)

    if dms:
        evaluated = evaluate_all_positions(active_dicts, prices, dms)
    else:
        evaluated = active_dicts

    allocation = build_allocation(
        regime_label=regime,
        active_trades=evaluated,
        portfolio_capital=PORTFOLIO_CAPITAL,
    )
    if store:
        persist_allocation(allocation, store)

    snapshot = build_portfolio_snapshot(
        allocation=allocation.to_dict(),
        active_positions=evaluated,
    )
    if store:
        persist_portfolio(snapshot, store)

    return snapshot.to_dict()


# ---------------------------------------------------------------------------
# Allocation
# ---------------------------------------------------------------------------

@router.get("/allocation")
def api_allocation():
    """Current bucket allocations with regime context."""
    store = _store()
    dms = _get_dms_dict()
    regime = _get_regime(dms)

    active = load_active_trades(store) if store else []
    active_dicts = [t.to_dict() for t in active]

    outcomes = load_all_outcomes(store) if store else []
    streaks = compute_bucket_streaks(outcomes)
    adj = compute_weekly_adjustments(streaks)

    allocation = build_allocation(
        regime_label=regime,
        active_trades=active_dicts,
        weekly_adjustments=adj,
        portfolio_capital=PORTFOLIO_CAPITAL,
    )
    if store:
        persist_allocation(allocation, store)

    return {
        "allocation": allocation.to_dict(),
        "weekly_adjustments": adj,
        "bucket_streaks": streaks,
    }


# ---------------------------------------------------------------------------
# Unified idea queue
# ---------------------------------------------------------------------------

@router.get("/unified-queue")
def api_unified_queue():
    """Cross-engine ranked idea queue with UPS scores."""
    store = _store()
    dms = _get_dms_dict()
    regime = _get_regime(dms)

    active = load_active_trades(store) if store else []
    queued = [t for t in active if t.lifecycle_state in ("SOURCED", "QUEUED", "STAGED")]
    active_positions = [t for t in active if t.lifecycle_state in ("ACTIVE", "EXTENDING", "CLOSING")]

    queue_items = []
    for t in queued:
        queue_items.append({
            "trade_id": t.trade_id,
            "ticker": t.ticker,
            "engine_source": t.engine_source,
            "bucket": t.bucket,
            "lifecycle_state": t.lifecycle_state,
            "ups_score": t.ups_score,
            "raw_engine_score": t.raw_engine_score,
            "derived_ru": t.derived_ru,
            "direction": t.direction,
            "created_at": t.created_at,
            "notes": t.notes,
        })

    queue_items.sort(key=lambda x: -x.get("ups_score", 0))

    return {
        "queue": queue_items,
        "count": len(queue_items),
        "active_count": len(active_positions),
        "regime": regime,
    }


# ---------------------------------------------------------------------------
# Trade lifecycle CRUD
# ---------------------------------------------------------------------------

@router.post("/trades/{trade_id}/stage")
def api_stage_trade(trade_id: str):
    """Reserve capital for a trade (QUEUED → STAGED)."""
    store = _store()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    trade = load_trade(trade_id, store)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    try:
        transition(trade, "STAGED", reason="Desk accepted — capital reserved")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    persist_trade(trade, store)
    return trade.to_dict()


@router.post("/trades/{trade_id}/activate")
def api_activate_trade(trade_id: str, body: ActivateRequest):
    """Mark trade as entered — populates entry and thesis fields."""
    store = _store()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    trade = load_trade(trade_id, store)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    ru_info = derive_ru(
        max_loss_per_unit=body.max_loss_per_unit or abs(body.entry_price - body.thesis_stop) if body.thesis_stop else None,
        units=body.units,
        portfolio_capital=PORTFOLIO_CAPITAL,
        engine_id=trade.engine_source,
    )

    try:
        activate_trade(
            trade,
            entry_price=body.entry_price,
            units=body.units,
            direction=body.direction,
            max_loss_per_unit=body.max_loss_per_unit or abs(body.entry_price - body.thesis_stop),
            derived_ru=ru_info.get("capped_ru", ru_info.get("derived_ru", 0)),
            thesis_target=body.thesis_target,
            thesis_stop=body.thesis_stop,
            thesis_max_days=body.thesis_max_days,
            invalidation_conditions=body.invalidation_conditions,
            trade_type=body.trade_type,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    persist_trade(trade, store)
    return {**trade.to_dict(), "ru_info": ru_info}


@router.post("/trades/{trade_id}/close")
def api_close_trade(trade_id: str, body: CloseRequest):
    """Close a trade, record P&L, create TradeOutcome."""
    store = _store()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    trade = load_trade(trade_id, store)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    try:
        transition(trade, "CLOSED", reason=f"Closed: {body.exit_reason}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    persist_trade(trade, store)

    regime = _get_regime()
    outcome = create_outcome(
        trade.to_dict(),
        pnl_dollars=body.pnl_dollars,
        exit_reason=body.exit_reason,
        regime_at_exit=regime,
    )
    persist_outcome(outcome, store)

    return {
        "trade": trade.to_dict(),
        "outcome": outcome.to_dict(),
    }


@router.get("/trades/{trade_id}")
def api_get_trade(trade_id: str):
    """Get a single trade record."""
    store = _store()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    trade = load_trade(trade_id, store)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")
    return trade.to_dict()


@router.post("/trades/{trade_id}/transition")
def api_transition_trade(trade_id: str, to_state: str = Query(...), reason: str = Query("")):
    """Generic state transition for a trade."""
    store = _store()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    trade = load_trade(trade_id, store)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    try:
        transition(trade, to_state, reason=reason or f"Desk transition to {to_state}")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    persist_trade(trade, store)
    return trade.to_dict()


# ---------------------------------------------------------------------------
# Position monitoring
# ---------------------------------------------------------------------------

@router.get("/positions/active")
def api_active_positions():
    """All active positions with current PIL state and suggested action."""
    store = _store()
    dms = _get_dms_dict()

    active = load_active_trades(store) if store else []
    active_in_pil = [t for t in active if t.lifecycle_state in ("ACTIVE", "EXTENDING", "CLOSING")]
    active_dicts = [t.to_dict() for t in active_in_pil]

    prices: Dict[str, float] = {}
    for t in active_dicts:
        prices[t.get("ticker", "")] = t.get("entry_price", 0)

    evaluated = evaluate_all_positions(active_dicts, prices, dms)

    if store:
        for ev in evaluated:
            tid = ev.get("trade_id", "")
            if tid:
                trade = load_trade(tid, store)
                if trade:
                    trade.position_state = ev.get("position_state", "")
                    trade.suggested_action = ev.get("suggested_action", "")
                    trade.current_pnl_pct = ev.get("current_pnl_pct", 0)
                    trade.days_in_trade = ev.get("days_in_trade", 0)
                    trade.state_reason = ev.get("state_reason", "")
                    trade.last_evaluated = ev.get("last_evaluated", "")
                    persist_trade(trade, store)

    summary = positions_summary(evaluated)
    return {
        "positions": evaluated,
        "summary": summary,
        "count": len(evaluated),
    }


@router.post("/positions/manual")
def api_manual_entry(body: ManualTradeRequest):
    """Manual trade entry — creates ACTIVE lifecycle record with thesis."""
    store = _store()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    mlp = abs(body.entry_price - body.thesis_stop) if body.thesis_stop else 0
    ru_info = derive_ru(
        max_loss_per_unit=mlp,
        units=body.units,
        portfolio_capital=PORTFOLIO_CAPITAL,
        engine_id="manual",
    )

    trade = create_manual_trade(
        ticker=body.ticker,
        direction=body.direction,
        entry_price=body.entry_price,
        units=body.units,
        trade_type=body.trade_type,
        thesis_stop=body.thesis_stop,
        thesis_target=body.thesis_target,
        thesis_max_days=body.thesis_max_days,
        invalidation_conditions=body.invalidation_conditions,
        bucket=body.bucket,
        sector=body.sector,
        max_loss_per_unit=mlp,
        derived_ru=ru_info.get("capped_ru", ru_info.get("derived_ru", 0)),
        notes=body.notes,
    )

    persist_trade(trade, store)
    return {**trade.to_dict(), "ru_info": ru_info}


@router.put("/positions/{trade_id}/thesis")
def api_update_thesis(trade_id: str, body: ThesisUpdateRequest):
    """Update thesis fields on any active position."""
    store = _store()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    trade = load_trade(trade_id, store)
    if trade is None:
        raise HTTPException(status_code=404, detail="Trade not found")

    if body.thesis_stop is not None:
        trade.thesis_stop = body.thesis_stop
        trade.max_loss_per_unit = abs(trade.entry_price - body.thesis_stop)
        ru_info = derive_ru(
            max_loss_per_unit=trade.max_loss_per_unit,
            units=trade.units,
            portfolio_capital=PORTFOLIO_CAPITAL,
            engine_id=trade.engine_source,
        )
        trade.derived_ru = ru_info.get("capped_ru", ru_info.get("derived_ru", 0))

    if body.thesis_target is not None:
        trade.thesis_target = body.thesis_target
    if body.thesis_max_days is not None:
        trade.thesis_max_days = body.thesis_max_days
    if body.invalidation_conditions is not None:
        trade.invalidation_conditions = body.invalidation_conditions

    trade.updated_at = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
    persist_trade(trade, store)
    return trade.to_dict()


# ---------------------------------------------------------------------------
# Risk dashboard
# ---------------------------------------------------------------------------

@router.get("/risk-dashboard")
def api_risk_dashboard():
    """Risk metrics, correlation matrix, drawdown."""
    store = _store()
    dms = _get_dms_dict()
    regime = _get_regime(dms)

    active = load_active_trades(store) if store else []
    active_dicts = [t.to_dict() for t in active if t.lifecycle_state in ("ACTIVE", "EXTENDING", "CLOSING")]

    e9_level = 0.0
    if dms and isinstance(dms.get("credit_stress"), dict):
        e9_level = float(dms["credit_stress"].get("composite_score", 0))

    dashboard = build_risk_dashboard(
        active_positions=active_dicts,
        regime=regime,
        e9_level=e9_level,
        portfolio_capital=PORTFOLIO_CAPITAL,
    )
    return dashboard.to_dict()


# ---------------------------------------------------------------------------
# Performance scorecard
# ---------------------------------------------------------------------------

@router.get("/performance")
def api_performance():
    """Performance scorecard: per-engine and per-bucket rolling metrics."""
    store = _store()
    if store is None:
        return {"engines": {}, "buckets": {}}

    result = refresh_all_metrics(store)

    engines_list = ["E1", "E2", "E3", "E4", "E5", "E7", "E8", "manual"]
    engine_metrics = {}
    for eid in engines_list:
        m = load_engine_metrics(eid, store)
        if m:
            engine_metrics[eid] = m.to_dict()

    buckets_list = ["income_core", "directional", "opportunistic"]
    bucket_metrics = {}
    for bid in buckets_list:
        m = load_bucket_metrics(bid, store)
        if m:
            bucket_metrics[bid] = m.to_dict()

    return {
        "engines": engine_metrics,
        "buckets": bucket_metrics,
        "refreshed": True,
    }


# ---------------------------------------------------------------------------
# Risk check for a proposed trade
# ---------------------------------------------------------------------------

@router.post("/risk-check")
def api_risk_check(body: dict):
    """Run risk checks for a proposed new trade."""
    store = _store()
    dms = _get_dms_dict()
    regime = _get_regime(dms)

    active = load_active_trades(store) if store else []
    active_dicts = [t.to_dict() for t in active if t.lifecycle_state in ("ACTIVE", "EXTENDING", "CLOSING")]

    bucket = str(body.get("bucket", "directional"))
    alloc = build_allocation(regime_label=regime, active_trades=active_dicts, portfolio_capital=PORTFOLIO_CAPITAL)
    b_state = alloc.buckets.get(bucket, {})

    e9_level = 0.0
    vol_state = "normal"
    flow_label = ""
    if dms:
        if isinstance(dms.get("credit_stress"), dict):
            e9_level = float(dms["credit_stress"].get("composite_score", 0))
        vol_state = str(dms.get("vol_state", dms.get("vol_direction", "normal")))
        fp = dms.get("flow_pressure")
        if isinstance(fp, dict):
            flow_label = str(fp.get("label", ""))

    evaluated = evaluate_all_positions(active_dicts, {}, dms)
    p_states = positions_summary(evaluated)

    result = check_trade_risk(
        ticker=str(body.get("ticker", "")),
        bucket=bucket,
        derived_ru=float(body.get("derived_ru", 0)),
        direction=str(body.get("direction", "long")),
        sector=str(body.get("sector", "")),
        ups_score=float(body.get("ups_score", 0)),
        active_positions=active_dicts,
        regime=regime,
        vol_state=vol_state,
        flow_label=flow_label,
        e9_level=e9_level,
        bucket_used_ru=float(b_state.get("used_ru", 0)),
        bucket_max_ru=float(b_state.get("max_ru", 10)),
        bucket_active_count=int(b_state.get("active_count", 0)),
        bucket_max_concurrent=int(b_state.get("max_concurrent", 5)),
        position_states=p_states,
    )
    return result.to_dict()


# ---------------------------------------------------------------------------
# Expiration check
# ---------------------------------------------------------------------------

@router.post("/expire-stale")
def api_expire_stale():
    """Run auto-expiration on QUEUED and STAGED trades."""
    store = _store()
    if store is None:
        return {"expired": 0}

    trades = load_active_trades(store)
    expired = check_expirations(trades)
    for t in expired:
        persist_trade(t, store)

    return {
        "expired": len(expired),
        "details": [{"trade_id": t.trade_id, "state": t.lifecycle_state} for t in expired],
    }


# ---------------------------------------------------------------------------
# Init endpoint (loads all panels at once)
# ---------------------------------------------------------------------------

@router.get("/init")
def api_rtv2_init():
    """Full initialization payload for the RTv2.0 dashboard."""
    store = _store()
    dms = _get_dms_dict()
    regime = _get_regime(dms)

    active = load_active_trades(store) if store else []
    active_dicts = [t.to_dict() for t in active]

    active_positions = [t for t in active_dicts if t.get("lifecycle_state") in ("ACTIVE", "EXTENDING", "CLOSING")]
    queued = [t for t in active_dicts if t.get("lifecycle_state") in ("SOURCED", "QUEUED", "STAGED")]

    prices: Dict[str, float] = {}
    for t in active_positions:
        prices[t.get("ticker", "")] = t.get("entry_price", 0)

    evaluated = evaluate_all_positions(active_positions, prices, dms) if active_positions else []

    alloc = build_allocation(
        regime_label=regime,
        active_trades=evaluated,
        portfolio_capital=PORTFOLIO_CAPITAL,
    )

    e9_level = 0.0
    if dms and isinstance(dms.get("credit_stress"), dict):
        e9_level = float(dms["credit_stress"].get("composite_score", 0))

    risk = build_risk_dashboard(
        active_positions=evaluated,
        regime=regime,
        e9_level=e9_level,
        portfolio_capital=PORTFOLIO_CAPITAL,
    )

    perf_engines = {}
    perf_buckets = {}
    if store:
        for eid in ["E1", "E2", "E3", "E4", "E5", "E7", "E8", "manual"]:
            m = load_engine_metrics(eid, store)
            if m:
                perf_engines[eid] = m.to_dict()
        for bid in ["income_core", "directional", "opportunistic"]:
            m = load_bucket_metrics(bid, store)
            if m:
                perf_buckets[bid] = m.to_dict()

    # DMS cards
    regime_card = {}
    flow_card = {}
    vol_card = {}
    engine_gates = {}
    if dms:
        regime_card = dms.get("regime", {})
        flow_card = dms.get("flow_pressure", {})
        vs = dms.get("vol_state", {})
        if isinstance(vs, dict):
            vol_card = {
                "term_structure": vs.get("term_structure", ""),
                "level": vs.get("level", ""),
                "skew": vs.get("skew", ""),
            }
        else:
            vol_card = {"term_structure": str(vs), "level": "", "skew": ""}
        engine_gates = dms.get("engine_gates", {})

    return {
        "regime": regime,
        "regime_card": regime_card,
        "flow_card": flow_card,
        "vol_card": vol_card,
        "engine_gates": engine_gates,
        "allocation": alloc.to_dict(),
        "positions": evaluated,
        "positions_summary": positions_summary(evaluated),
        "queue": sorted(queued, key=lambda x: -float(x.get("ups_score", 0))),
        "risk": risk.to_dict(),
        "performance": {"engines": perf_engines, "buckets": perf_buckets},
        "portfolio_capital": PORTFOLIO_CAPITAL,
    }


# ---------------------------------------------------------------------------
# Engine ingestion pipeline
# ---------------------------------------------------------------------------

@router.post("/ingest")
def api_ingest(body: dict):
    """Run the full integration pipeline: extract engine signals → score → create trades.

    Body: {"E1": [...], "E3": [...], ...} — raw engine outputs.
    Alternatively, pass {"auto": true} to read latest engine outputs from
    the DMS and cached engine results.
    """
    store = _store()
    dms = _get_dms_dict()

    engine_outputs = {k: v for k, v in body.items() if k.startswith("E")}

    if body.get("auto") and store:
        if dms:
            e1_data = dms.get("earnings_candidates")
            if e1_data and isinstance(e1_data, list):
                engine_outputs.setdefault("E1", e1_data)

    gate_results = {}
    if dms and isinstance(dms.get("engine_gates"), dict):
        for k, v in dms["engine_gates"].items():
            gate_results[k] = {"status": v} if isinstance(v, str) else v

    result = ingest_engine_signals(
        engine_outputs,
        dms=dms,
        gate_results=gate_results,
        store=store,
        portfolio_capital=PORTFOLIO_CAPITAL,
    )
    return result


