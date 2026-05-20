"""Engine 2 phase-based live review with Engine 14 replay context."""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, Optional

from backend.config import FeatureFlags, get_flags
from backend.deps import get_benzinga_client_optional, get_client
from backend.engine14.simulator import IcScenarioRequest, run_scenario
from backend.engine2_advisor import compute_trade_tracking, generate_checkin_analysis


def _to_float(v: Any, default: float = 0.0) -> float:
    try:
        return float(v)
    except Exception:
        return float(default)


def _phase_for_trade(trade: Dict[str, Any], requested: Optional[str] = None) -> str:
    if requested in ("pre_event", "pre_open", "post_open"):
        return str(requested)
    expiry = ((trade.get("entry") or {}).get("expiryDate") or "")[:10]
    if not expiry:
        return "pre_event"
    try:
        dte = (dt.date.fromisoformat(expiry) - dt.date.today()).days
    except Exception:
        return "pre_event"
    if dte <= 0:
        return "post_open"
    if dte == 1:
        return "pre_open"
    return "pre_event"


def _summarize_replay(scenario: Dict[str, Any]) -> Dict[str, Any]:
    tl = scenario.get("mtmTimeline") if isinstance(scenario.get("mtmTimeline"), list) else []
    end = tl[-1] if tl else {}
    dist = scenario.get("outcomeDistribution") if isinstance(scenario.get("outcomeDistribution"), dict) else {}
    early = (dist.get("earlyTarget") or {}).get("pct")
    full_collect = (dist.get("fullCollect") or {}).get("pct")
    white_knuckle = (dist.get("whiteKnuckle") or {}).get("pct")
    stop_out = (dist.get("stopOut") or {}).get("pct")
    breach = (dist.get("breach") or {}).get("pct")
    mtm_curve = []
    for row in tl:
        if not isinstance(row, dict):
            continue
        mtm_curve.append({
            "dte": row.get("dte"),
            "p10": row.get("p10"),
            "p50": row.get("p50"),
            "p90": row.get("p90"),
            "pBreach": row.get("pBreach"),
            "pStopHit": row.get("pStopHit"),
        })
    exit_opt = scenario.get("exitRulesOptimization") if isinstance(scenario.get("exitRulesOptimization"), dict) else {}
    early_avg_days = (dist.get("earlyTarget") or {}).get("avgDays")
    mae_p50 = (dist.get("whiteKnuckle") or {}).get("maxAdverseExcursionPct")
    return {
        "analoguesUsed": int(scenario.get("analoguesUsed") or 0),
        "p10": end.get("p10"),
        "p50": end.get("p50"),
        "p90": end.get("p90"),
        "pBreach": None if end.get("pBreach") is None else round(_to_float(end.get("pBreach")) * 100.0, 1),
        "earlyExitRate": early,
        "fullCollectRate": full_collect,
        "whiteKnuckleRate": white_knuckle,
        "stopOutRate": stop_out,
        "breachRate": breach,
        "mtmCurve": mtm_curve,
        "exitRulesRec": {
            "profitTarget": exit_opt.get("recommendedProfitTarget"),
            "stopLoss": exit_opt.get("recommendedStopLoss"),
            "timeStopDays": exit_opt.get("recommendedTimeStopDays"),
        },
        "daysToEarlyExit": early_avg_days,
        "medianMaePct": mae_p50,
    }


def _ladder_from_tracking(tracking: Dict[str, Any], replay_summary: Dict[str, Any]) -> Dict[str, Any]:
    status = str(tracking.get("deterministicStatus") or "on_track")
    p_breach = _to_float(replay_summary.get("pBreach"), 0.0)
    gate = "HOLD"
    conf = 68
    if status in ("exit",):
        gate = "EXIT"
        conf = 84
    elif status in ("adjust",):
        gate = "ADJUST"
        conf = 76
    elif p_breach >= 30:
        gate = "CAUTION"
        conf = 70
    rows = [
        {"action": "HOLD", "probability": max(5, 100 - int(p_breach * 1.3)), "note": "Stay in plan if distance remains healthy."},
        {"action": "ADJUST", "probability": min(90, int(p_breach + 15)), "note": "Consider narrowing risk or taking partial profits."},
        {"action": "EXIT", "probability": min(95, int(p_breach + 5 if status in ('adjust', 'exit') else p_breach / 2)), "note": "Exit if regime/price drift accelerates into shorts."},
    ]
    return {"preVerdict": gate, "confidence": conf, "rows": rows}


def run_e2_live_review(
    *,
    trade: Dict[str, Any],
    current_spot: float,
    current_regime: Optional[Dict[str, Any]],
    current_vol: Optional[str],
    phase: Optional[str] = None,
    flags: Optional[FeatureFlags] = None,
    store: Any = None,
) -> Dict[str, Any]:
    f = flags or get_flags()
    entry = trade.get("entry") or {}
    resolved_phase = _phase_for_trade(trade, phase)
    tracking = compute_trade_tracking(
        trade=trade,
        current_spot=float(current_spot),
        current_regime=current_regime,
        current_vol_pressure=current_vol,
    )
    llm = generate_checkin_analysis(trade=trade, tracking=tracking, flags=f)

    replay_summary: Dict[str, Any] = {}
    try:
        entry_date = str(entry.get("entryDate") or (trade.get("loggedAt") or "")[:10])[:10]
        expiry = str(entry.get("expiryDate") or "")[:10]
        sp = _to_float(entry.get("shortPutStrike"), 0.0)
        lp = _to_float(entry.get("longPutStrike"), 0.0)
        sc = _to_float(entry.get("shortCallStrike"), 0.0)
        lc = _to_float(entry.get("longCallStrike"), 0.0)
        credit = _to_float(entry.get("entryCredit"), 0.0)
        if entry_date and expiry and sp and lp and sc and lc and credit > 0:
            scenario = run_scenario(
                IcScenarioRequest(
                    underlying=str(entry.get("underlying") or "SPX"),
                    entry_date=entry_date,
                    expiry=expiry,
                    short_put=sp,
                    long_put=lp,
                    short_call=sc,
                    long_call=lc,
                    credit_received=credit,
                ),
                client=get_client(),
                flags=f,
                benzinga_client=get_benzinga_client_optional(),
                store=store,
            )
            replay_summary = _summarize_replay(scenario)
    except Exception:
        replay_summary = {}

    ladder = _ladder_from_tracking(tracking, replay_summary)
    history_breaker = (trade.get("entryContext") or {}).get("historyBreakerRisk")

    return {
        "phase": resolved_phase,
        "mode": trade.get("mode") or "live",
        "tracking": tracking,
        "actionLadder": ladder,
        "projection": replay_summary,
        "historyBreaker": history_breaker,
        "llm": {
            "status": llm.get("status"),
            "headline": llm.get("headline"),
            "spotAnalysis": llm.get("spotAnalysis"),
            "regimeDrift": llm.get("regimeDrift"),
            "recommendation": llm.get("recommendation"),
            "adjustmentIfNeeded": llm.get("adjustmentIfNeeded"),
            "riskUpdate": llm.get("riskUpdate"),
            "deskNote": llm.get("deskNote"),
            "source": llm.get("_source"),
        },
    }

