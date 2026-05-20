from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        return float(v)
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(v)
    except (TypeError, ValueError):
        return None


def _clamp(lo: float, hi: float, x: float) -> float:
    return max(lo, min(hi, x))


def _overall_breach_rate_pct(summary: Dict[str, Any], events: List[Dict[str, Any]]) -> Optional[float]:
    direct = _to_float(summary.get("breach_rate_pct"))
    if direct is not None:
        return direct
    breaches = summary.get("breaches")
    used = summary.get("events_used") or summary.get("eventsUsed")
    if breaches is not None and used:
        try:
            if float(used) > 0:
                return (float(breaches) / float(used)) * 100.0
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    usable = [e for e in events if isinstance(e.get("breach"), bool)]
    if not usable:
        return None
    b = sum(1 for e in usable if e.get("breach") is True)
    return (b / len(usable)) * 100.0


def _recent_events(events: List[Dict[str, Any]], n: int = 4) -> List[Dict[str, Any]]:
    usable = [e for e in events if isinstance(e.get("breach"), bool)]

    def _key(e: Dict[str, Any]) -> Tuple[int, str]:
        d = str(e.get("earnDate") or "")
        try:
            _ = dt.date.fromisoformat(d[:10])
            return (1, d[:10])
        except Exception:
            return (0, "")

    usable.sort(key=_key, reverse=True)
    return usable[: max(1, int(n))]


def compute_history_breaker_risk(
    *,
    summary: Dict[str, Any],
    events: List[Dict[str, Any]],
    regime: Dict[str, Any],
    regime_validation: Dict[str, Any],
    stability: Optional[Dict[str, Any]],
    gap_vs_ctc: Optional[Dict[str, Any]],
    event_risk: Optional[Dict[str, Any]],
    quarters: Dict[str, Any],
    current_quarter_key: Optional[str],
) -> Dict[str, Any]:
    score = 0.0
    drivers: List[str] = []
    signals: Dict[str, Any] = {}

    overall = _overall_breach_rate_pct(summary, events)
    recent = _recent_events(events, n=4)
    recent_rate = None
    if recent:
        recent_rate = (sum(1 for e in recent if e.get("breach") is True) / len(recent)) * 100.0
    signals["overallBreachRatePct"] = None if overall is None else round(overall, 2)
    signals["recentBreachRatePct"] = None if recent_rate is None else round(recent_rate, 2)
    signals["recentSample"] = len(recent)
    if overall is not None and recent_rate is not None:
        delta = recent_rate - overall
        signals["recentVsOverallDeltaPct"] = round(delta, 2)
        if delta >= 10.0:
            s = _clamp(0.0, 22.0, delta * 1.2)
            score += s
            drivers.append(f"Recent breach rate runs {delta:.1f}pp above full sample.")

    rv = regime_validation if isinstance(regime_validation, dict) else {}
    events_used = _to_int(rv.get("eventsUsed")) or 0
    breaches = _to_int(rv.get("breaches")) or 0
    missed = _to_int(rv.get("breachesMissed")) or 0
    by_gate = rv.get("breachRateByGatePct") if isinstance(rv.get("breachRateByGatePct"), dict) else {}
    ok_rate = _to_float(by_gate.get("OK"))
    no_trade_rate = _to_float(by_gate.get("NO_TRADE"))
    signals["regimeValidation"] = {
        "eventsUsed": events_used,
        "breaches": breaches,
        "breachesMissed": missed,
        "okGateBreachRatePct": ok_rate,
        "noTradeGateBreachRatePct": no_trade_rate,
    }
    if events_used >= 6 and breaches > 0:
        missed_ratio = missed / breaches
        signals["missedBreachRatio"] = round(missed_ratio, 3)
        if missed_ratio >= 0.35:
            s = _clamp(0.0, 22.0, missed_ratio * 30.0)
            score += s
            drivers.append("Historical breaches often occurred while prior regime gate was OK.")
    if ok_rate is not None and no_trade_rate is not None and ok_rate >= no_trade_rate + 8.0:
        score += 8.0
        drivers.append("OK-gate historical breach rate is materially elevated.")

    st = stability if isinstance(stability, dict) else {}
    sign_agree = _to_float(st.get("tasSignAgreementPct"))
    st_conf = str(st.get("confidenceDerived") or "").upper()
    signals["stability"] = {"tasSignAgreementPct": sign_agree, "confidence": st_conf or None}
    if sign_agree is not None:
        if sign_agree < 65.0:
            score += 16.0
            drivers.append("Directional tail signal is unstable across bootstrap samples.")
        elif sign_agree < 80.0:
            score += 7.0
            drivers.append("Tail asymmetry signal is only moderately stable.")
    elif st_conf == "LOW":
        score += 6.0

    if isinstance(event_risk, dict) and event_risk.get("enabled") is True:
        er_score = _to_float(event_risk.get("score01"))
        er_label = str(event_risk.get("label") or "")
        signals["eventRisk"] = {"score01": er_score, "label": er_label or None}
        if er_score is not None and er_score >= 0.66:
            score += 18.0
            drivers.append("Event-risk overlay is HIGH into earnings.")
        elif er_score is not None and er_score >= 0.50:
            score += 9.0
            drivers.append("Event-risk overlay is elevated into earnings.")

    qk = current_quarter_key
    qrec = None
    if qk and isinstance(quarters.get(qk), dict):
        qrec = str(quarters[qk].get("recommendation") or "")
    signals["quarterKey"] = qk
    signals["quarterRecommendation"] = qrec or None
    if qrec.lower().startswith("avoid"):
        score += 12.0
        drivers.append("Current quarter profile is marked Avoid.")

    gap = ((gap_vs_ctc or {}).get("gap") or {}) if isinstance(gap_vs_ctc, dict) else {}
    ctc = ((gap_vs_ctc or {}).get("ctc") or {}) if isinstance(gap_vs_ctc, dict) else {}
    gap1 = _to_float(gap.get("1.0"))
    ctc1 = _to_float(ctc.get("1.0"))
    signals["gapVsCtc1x"] = {"gapPct": gap1, "ctcPct": ctc1}
    if gap1 is not None and ctc1 is not None and ctc1 >= gap1 + 10.0:
        score += 8.0
        drivers.append("Close-to-close tail risk runs above gap-only history.")

    regime_gate = str(((regime.get("guidance") if isinstance(regime.get("guidance"), dict) else {}) or {}).get("tradeGate") or regime.get("tradeGate") or "OK").upper()
    signals["currentRegimeGate"] = regime_gate
    if regime_gate == "NO_TRADE":
        score += 15.0
        drivers.append("Current regime gate is NO_TRADE.")
    elif regime_gate == "CAUTION":
        score += 6.0

    score = round(_clamp(0.0, 100.0, score), 1)
    if score >= 70.0:
        level = "high"
        gate = "NO_TRADE"
        confidence = "high"
    elif score >= 45.0:
        level = "elevated"
        gate = "CAUTION"
        confidence = "med"
    else:
        level = "low"
        gate = "OK"
        confidence = "med" if score >= 25.0 else "low"

    override = level in ("elevated", "high")
    if not drivers:
        if level == "low":
            drivers = ["No strong history-breaker divergence signals detected."]
        else:
            drivers = ["Composite risk elevated from multiple moderate signals."]

    return {
        "score": score,
        "level": level,
        "gate": gate,
        "confidence": confidence,
        "overrideFavorableStats": override,
        "drivers": drivers[:3],
        "signals": signals,
    }

