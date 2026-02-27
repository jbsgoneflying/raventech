"""RTv2.0 — Engine Integration Wiring.

Transforms raw engine outputs into standardised RTv2.0 signals, scores
them via UPS, and feeds them into the trade lifecycle pipeline.

This module is the bridge between existing engines (Layer 0) and the
RTv2.0 orchestration layers (Layers 1-3).
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional

from backend.rtv2_capital_allocator import (
    bucket_for_engine,
    derive_ru,
    ENGINE_RU_HARD_CAP,
    PORTFOLIO_RU_HARD_CAP,
)
from backend.rtv2_cross_engine_scorer import (
    compute_ups,
    rank_signals,
    record_engine_score,
    UPSResult,
)
from backend.rtv2_trade_lifecycle import (
    TradeRecord,
    create_trade,
    persist_trade,
    load_active_trades,
)
from backend.rtv2_risk_manager import check_trade_risk
from backend.rtv2_performance_feedback import (
    engine_hit_rate_bonus,
    load_all_outcomes,
)

LOG = logging.getLogger(__name__)

PORTFOLIO_CAPITAL_DEFAULT = 500_000


# ---------------------------------------------------------------------------
# Engine-specific signal extractors
# ---------------------------------------------------------------------------

def _extract_e1_signals(e1_output: List[dict]) -> List[dict]:
    """Extract signals from Engine 1 (Earnings Breach Ranker)."""
    signals = []
    for item in (e1_output or []):
        ticker = item.get("ticker", "")
        score = float(item.get("compositeScore", 0))
        tier = item.get("tier", "")
        if not ticker or tier in ("avoid", "caution"):
            continue
        signals.append({
            "engine_id": "E1",
            "ticker": ticker,
            "raw_score": score,
            "direction": "neutral",
            "trade_type": "premium_decay",
            "sector": "",
            "max_loss_per_unit": None,
            "units": 1,
            "notes": f"Tier: {tier}",
        })
    return signals


def _extract_e2_signals(e2_output: dict) -> List[dict]:
    """Extract signals from Engine 2 (SPX IC)."""
    if not e2_output:
        return []
    current = e2_output.get("current", {})
    regime = current.get("regime", {})
    score = float(regime.get("score100", 0))
    symbol = e2_output.get("underlying", {}).get("symbol", "SPX")

    if score < 20:
        return []

    signals = [{
        "engine_id": "E2",
        "ticker": symbol,
        "raw_score": score,
        "direction": "neutral",
        "trade_type": "premium_decay",
        "sector": "Index",
        "max_loss_per_unit": None,
        "units": 1,
        "notes": f"Regime score: {score:.0f}",
    }]
    return signals


def _extract_e3_signals(e3_output: List[dict]) -> List[dict]:
    """Extract signals from Engine 3 (Red Dog Reversal)."""
    signals = []
    for item in (e3_output or []):
        ticker = item.get("ticker", "")
        quality = item.get("quality", {})
        score = float(quality.get("score", 0))
        grade = quality.get("grade", "")
        if not ticker or grade in ("C", ""):
            continue
        direction = item.get("direction", "bullish")
        levels = item.get("levels", {})
        entry = float(levels.get("entryTrigger", 0))
        stop = float(levels.get("stopLoss", 0))
        max_loss = abs(entry - stop) if entry and stop else None
        signals.append({
            "engine_id": "E3",
            "ticker": ticker,
            "raw_score": score,
            "direction": direction,
            "trade_type": "mean_reversion",
            "sector": item.get("sector", ""),
            "max_loss_per_unit": max_loss,
            "units": 100,
            "entry_trigger": entry,
            "stop_loss": stop,
            "notes": f"Grade: {grade}",
        })
    return signals


def _extract_e4_signals(e4_output: List[dict]) -> List[dict]:
    """Extract signals from Engine 4 (Ichimoku)."""
    signals = []
    for item in (e4_output or []):
        ticker = item.get("ticker", "")
        score = float(item.get("score", 0))
        grade = item.get("grade", "")
        bucket = item.get("freshnessBucket", "")
        if not ticker or bucket == "rejected":
            continue
        direction = item.get("direction", "bullish")
        levels = item.get("levels", {})
        entry = float(levels.get("entryTrigger", 0))
        stop = float(levels.get("stopLoss", 0))
        max_loss = abs(entry - stop) if entry and stop else None
        signals.append({
            "engine_id": "E4",
            "ticker": ticker,
            "raw_score": score,
            "direction": direction,
            "trade_type": "trend_continuation",
            "sector": item.get("sector", ""),
            "max_loss_per_unit": max_loss,
            "units": 100,
            "entry_trigger": entry,
            "stop_loss": stop,
            "notes": f"Grade: {grade}, Bucket: {bucket}",
        })
    return signals


def _extract_e5_signals(e5_output: dict) -> List[dict]:
    """Extract signals from Engine 5 (Lead-Lag + Regime ideas)."""
    signals = []
    ideas = e5_output.get("weekly_ideas", []) if isinstance(e5_output, dict) else []
    for item in ideas:
        symbol = item.get("symbol", "")
        confidence = float(item.get("confidence", 0))
        if not symbol or item.get("suppressed"):
            continue
        direction = item.get("directionalLean", "neutral")
        signals.append({
            "engine_id": "E5",
            "ticker": symbol,
            "raw_score": confidence,
            "direction": direction,
            "trade_type": "varies",
            "sector": "",
            "max_loss_per_unit": None,
            "units": 1,
            "notes": f"Structure: {item.get('structure', '')}",
        })
    return signals


def _extract_e7_signals(e7_output: List[dict]) -> List[dict]:
    """Extract signals from Engine 7 (Pairs)."""
    signals = []
    for item in (e7_output or []):
        pair_id = item.get("pair_id", "")
        score = float(item.get("confidence_score", 0))
        grade = item.get("grade", "")
        if not pair_id or not item.get("tradable"):
            continue
        signals.append({
            "engine_id": "E7",
            "ticker": pair_id,
            "raw_score": score,
            "direction": item.get("mode", "mean_reversion"),
            "trade_type": "relative_value",
            "sector": "",
            "max_loss_per_unit": None,
            "units": 1,
            "notes": f"Grade: {grade}, Long: {item.get('long_asset','')}, Short: {item.get('short_asset','')}",
        })
    return signals


def _extract_e8_signals(e8_output: List[dict]) -> List[dict]:
    """Extract signals from Engine 8 (Post-Event Extension)."""
    signals = []
    for item in (e8_output or []):
        ticker = item.get("ticker", "")
        decision = item.get("decision", {})
        verdict = decision.get("decision", "PASS") if isinstance(decision, dict) else str(decision)
        if verdict == "PASS":
            continue
        snapshot = item.get("snapshot", {})
        direction = str(snapshot.get("direction", "UP")).lower()
        if direction == "up":
            direction = "long"
        elif direction == "down":
            direction = "short"

        trade_type = "event_continuation" if verdict == "CONTINUE" else "event_fade"
        signals.append({
            "engine_id": "E8",
            "ticker": ticker,
            "raw_score": 70.0,
            "direction": direction,
            "trade_type": trade_type,
            "sector": "",
            "max_loss_per_unit": None,
            "units": 100,
            "notes": f"Decision: {verdict}",
        })
    return signals


EXTRACTORS = {
    "E1": _extract_e1_signals,
    "E2": _extract_e2_signals,
    "E3": _extract_e3_signals,
    "E4": _extract_e4_signals,
    "E5": _extract_e5_signals,
    "E7": _extract_e7_signals,
    "E8": _extract_e8_signals,
}


# ---------------------------------------------------------------------------
# Pipeline: extract → score → create trades
# ---------------------------------------------------------------------------

def ingest_engine_signals(
    engine_outputs: Dict[str, Any],
    *,
    dms: Optional[dict] = None,
    gate_results: Optional[Dict[str, dict]] = None,
    store: Any = None,
    portfolio_capital: float = PORTFOLIO_CAPITAL_DEFAULT,
) -> Dict[str, Any]:
    """Run the full integration pipeline.

    engine_outputs: {"E1": [...], "E3": [...], ...}
    gate_results: {"TICKER": {"status": "TRADABLE", ...}, ...}
    Returns summary of new trades created + scored queue.
    """
    gates = gate_results or {}
    all_signals: List[dict] = []

    for engine_id, extractor in EXTRACTORS.items():
        raw = engine_outputs.get(engine_id)
        if raw is None:
            continue
        try:
            extracted = extractor(raw)
            all_signals.extend(extracted)
        except Exception as exc:
            LOG.warning("Failed to extract %s signals: %s", engine_id, exc)

    if not all_signals:
        return {"signals_extracted": 0, "trades_created": 0, "queue": []}

    regime = "Transitional"
    vol_state = "normal"
    flow_label = ""
    engine_gate_map: Dict[str, str] = {}

    if dms:
        regime_obj = dms.get("regime")
        if isinstance(regime_obj, dict):
            regime = str(regime_obj.get("state", "Transitional"))
        elif isinstance(regime_obj, str):
            regime = regime_obj
        vol_state = str(dms.get("vol_state", dms.get("vol_direction", "normal")))
        fp = dms.get("flow_pressure")
        if isinstance(fp, dict):
            flow_label = str(fp.get("label", ""))
        eg = dms.get("engine_gates", {})
        if isinstance(eg, dict):
            engine_gate_map = {k: str(v) for k, v in eg.items()}

    active_trades = load_active_trades(store) if store else []
    active_tickers = {t.ticker for t in active_trades if t.lifecycle_state in ("ACTIVE", "EXTENDING", "CLOSING")}

    outcomes = load_all_outcomes(store) if store else []
    hit_rates: Dict[str, float] = {}
    for eid in EXTRACTORS.keys():
        hit_rates[eid] = engine_hit_rate_bonus(eid, outcomes)

    ups_results: List[UPSResult] = []
    signal_map: Dict[str, dict] = {}

    for sig in all_signals:
        engine_id = sig["engine_id"]
        ticker = sig["ticker"]
        raw_score = sig["raw_score"]

        if store:
            record_engine_score(engine_id, raw_score, store)

        gate_info = gates.get(ticker, {})
        gate_decision = str(gate_info.get("status", "TRADABLE"))
        engine_gate = engine_gate_map.get(engine_id, "allowed")

        penalties = []
        if gate_decision == "SUPPRESS":
            penalties.append("engine_gate_suppress")
        if ticker in active_tickers:
            penalties.append("same_underlying_overlap")

        signal_id = f"{engine_id}:{ticker}:{dt.date.today().isoformat()}"

        ru_info = derive_ru(
            max_loss_per_unit=sig.get("max_loss_per_unit"),
            units=sig.get("units", 1),
            portfolio_capital=portfolio_capital,
            engine_id=engine_id,
        )

        hard_block_check = {}
        if sig.get("max_loss_per_unit") is None and engine_id not in ("E5",):
            pass
        if not sig.get("ticker"):
            hard_block_check["data_integrity_failure"] = True

        ups = compute_ups(
            signal_id=signal_id,
            engine_id=engine_id,
            ticker=ticker,
            raw_score=raw_score,
            engine_gate=engine_gate,
            gate_decision=gate_decision,
            vol_state=vol_state,
            flow_label=flow_label,
            regime=regime,
            trade_type=sig.get("trade_type", ""),
            days_since_signal=0.0,
            event_proximity_days=5.0 if engine_id in ("E1", "E8") else None,
            sequencer_favored=False,
            store=store,
            penalty_conditions=penalties,
            hard_block_check=hard_block_check if hard_block_check else None,
        )

        ups_results.append(ups)
        signal_map[signal_id] = {**sig, "ru_info": ru_info}

    ranked = rank_signals(ups_results, hit_rates)

    trades_created = []
    for ups_r in ranked:
        if ups_r.hard_blocked:
            continue

        sig = signal_map.get(ups_r.signal_id, {})
        engine_id = ups_r.engine_id
        bucket = bucket_for_engine(engine_id)
        ru_info = sig.get("ru_info", {})

        trade = create_trade(
            ticker=ups_r.ticker,
            engine_source=engine_id,
            bucket=bucket,
            raw_engine_score=ups_r.raw_score,
            ups_score=ups_r.final_ups,
            direction=sig.get("direction", ""),
            sector=sig.get("sector", ""),
            max_loss_per_unit=sig.get("max_loss_per_unit") or 0,
            units=sig.get("units", 0),
            derived_ru=ru_info.get("capped_ru", ru_info.get("derived_ru", 0)),
            notes=sig.get("notes", ""),
            initial_state="QUEUED",
        )

        if store:
            persist_trade(trade, store)
        trades_created.append(trade.to_dict())

    return {
        "signals_extracted": len(all_signals),
        "trades_created": len(trades_created),
        "trades": trades_created,
        "ups_scores": [r.to_dict() for r in ranked],
    }
