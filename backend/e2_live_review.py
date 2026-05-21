"""Engine 2 phase-based live review with Engine 14 replay context.

Returns a richly nested ``evidence`` + ``recommendation`` payload that
mirrors the shape Engine 1's live review emits, so the SPX desk gets
the same decision-grade information density (verdict, narrative, key
points, risks, action ladder with probability + p10/p90 bands) plus
SPX-specific tiles (regime drift, vol shift, time decay, replay
projection, history-breaker).

The legacy ``tracking`` / ``projection`` / ``actionLadder`` /
``historyBreaker`` / ``llm`` top-level fields are preserved for any
older clients still mounted on the page.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from backend.config import FeatureFlags, get_flags
from backend.deps import get_benzinga_client_optional, get_client
from backend.engine14.simulator import IcScenarioRequest, run_scenario
from backend.engine2_advisor import compute_trade_tracking, generate_checkin_analysis


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _to_float(v: Any, default: Optional[float] = 0.0) -> Optional[float]:
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def _round(v: Any, digits: int = 2) -> Optional[float]:
    f = _to_float(v, default=None)
    if f is None:
        return None
    try:
        return round(f, digits)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Phase resolution
# ---------------------------------------------------------------------------

def _phase_for_trade(trade: Dict[str, Any], requested: Optional[str] = None) -> str:
    """Resolve phase, honoring an explicit user request when provided.

    The phase enum stays ``pre_event``/``pre_open``/``post_open`` for API
    back-compat. The frontend re-labels these as ``Position Check`` /
    ``Pre-Expiry`` / ``Expiry Day`` for the SPX context where there is
    no earnings catalyst.
    """
    if requested in ("pre_event", "pre_open", "post_open"):
        return str(requested)
    return _auto_phase(trade)


def _auto_phase(trade: Dict[str, Any]) -> str:
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


# ---------------------------------------------------------------------------
# Replay summary
# ---------------------------------------------------------------------------

def _summarize_replay(scenario: Dict[str, Any]) -> Dict[str, Any]:
    """Distill an Engine 14 scenario into the slice the live-review UI needs.

    Returns both legacy keys (p10/p50/p90/breachRate) and E1-shape keys
    (p10PnlPct/p50PnlPct/p90PnlPct/fullCollectRate as a decimal fraction
    /pathsCount/medianMaePct/daysToEarlyExit/exitRulesRec) so the new
    renderer can drop in.
    """
    tl = scenario.get("mtmTimeline") if isinstance(scenario.get("mtmTimeline"), list) else []
    end = tl[-1] if tl else {}
    dist = scenario.get("outcomeDistribution") if isinstance(scenario.get("outcomeDistribution"), dict) else {}
    early = (dist.get("earlyTarget") or {}).get("pct")
    full_collect = (dist.get("fullCollect") or {}).get("pct")
    white_knuckle = (dist.get("whiteKnuckle") or {}).get("pct")
    stop_out = (dist.get("stopOut") or {}).get("pct")
    breach = (dist.get("breach") or {}).get("pct")
    mtm_curve: List[Dict[str, Any]] = []
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
    expected = scenario.get("expectedValue") if isinstance(scenario.get("expectedValue"), dict) else {}
    paths_count = int(scenario.get("analoguesUsed") or 0)

    def _frac(pct: Any) -> Optional[float]:
        # outcomeDistribution exposes % values 0-100; the new UI expects
        # decimal fractions 0-1 (matching E1's "Full-collect rate"
        # tile). Pass-through Nones unchanged.
        f = _to_float(pct, default=None)
        return None if f is None else round(f / 100.0, 4)

    return {
        # back-compat (legacy renderers)
        "analoguesUsed": paths_count,
        "p10": end.get("p10"),
        "p50": end.get("p50"),
        "p90": end.get("p90"),
        "pBreach": None if end.get("pBreach") is None else round(_to_float(end.get("pBreach"), 0.0) * 100.0, 1),
        "earlyExitRate": early,
        "fullCollectRate": full_collect,
        "whiteKnuckleRate": white_knuckle,
        "stopOutRate": stop_out,
        "breachRate": breach,
        # E1-shape additions
        "available": paths_count > 0,
        "pathsCount": paths_count,
        "p10PnlPct": end.get("p10"),
        "p50PnlPct": end.get("p50"),
        "p90PnlPct": end.get("p90"),
        "fullCollectRateFrac": _frac(full_collect),
        "earlyExitRateFrac": _frac(early),
        "whiteKnuckleRateFrac": _frac(white_knuckle),
        "stopOutRateFrac": _frac(stop_out),
        "breachRateFrac": _frac(breach),
        "meanPnlPct": expected.get("meanPnlPct"),
        "medianPnlPct": expected.get("medianPnlPct"),
        "sharpeProxy": expected.get("sharpeProxy"),
        # Shared payloads
        "mtmCurve": mtm_curve,
        "exitRulesRec": {
            "profitTarget": exit_opt.get("recommendedProfitTarget"),
            "stopLoss": exit_opt.get("recommendedStopLoss"),
            "timeStopDays": exit_opt.get("recommendedTimeStopDays"),
        },
        "daysToEarlyExit": early_avg_days,
        "medianMaePct": mae_p50,
        "conditioningSummary": _extract_conditioning_summary(scenario.get("conditioningSummary")),
    }


def _extract_conditioning_summary(raw: Any) -> Optional[str]:
    """Engine 14 returns ``conditioningSummary`` as a structured dict; the
    desk-facing line lives in ``humanSummary``. Older snapshots and tests
    pass a plain string straight through. Return ``None`` when neither
    representation is usable so the renderer can hide the band cleanly."""
    if raw is None:
        return None
    if isinstance(raw, str):
        return raw.strip() or None
    if isinstance(raw, dict):
        for k in ("humanSummary", "summary", "narrative"):
            val = raw.get(k)
            if isinstance(val, str) and val.strip():
                return val.strip()
    return None


# ---------------------------------------------------------------------------
# Evidence assembly
# ---------------------------------------------------------------------------

def _build_spot_evidence(trade: Dict[str, Any], tracking: Dict[str, Any]) -> Dict[str, Any]:
    entry = trade.get("entry") or {}
    now = _to_float(tracking.get("currentSpot"), default=None)
    at_entry = _to_float(entry.get("spotAtEntry"), default=None)
    move = None
    if now is not None and at_entry not in (None, 0.0):
        move = round((now - at_entry) / at_entry * 100.0, 2)
    put_pct = _to_float(tracking.get("distPutPct"), default=None)
    call_pct = _to_float(tracking.get("distCallPct"), default=None)
    nearest = None
    if put_pct is not None and call_pct is not None:
        nearest = round(min(put_pct, call_pct), 2)
    elif put_pct is not None:
        nearest = put_pct
    elif call_pct is not None:
        nearest = call_pct
    return {
        "now": _round(now, 2),
        "atEntry": _round(at_entry, 2),
        "moveSinceEntryPct": move,
        "putDistPct": put_pct,
        "callDistPct": call_pct,
        "nearestShortPct": nearest,
        "distPutPts": tracking.get("distPutPts"),
        "distCallPts": tracking.get("distCallPts"),
        "breachProxPut": tracking.get("breachProxPut"),
        "breachProxCall": tracking.get("breachProxCall"),
    }


def _build_iv_evidence(trade: Dict[str, Any], current_vol: Any) -> Dict[str, Any]:
    """Render the vol/IV tile from whatever the router hands us.

    The MI v2 ``regime_snapshot`` exposes ``vol_state`` as a structured
    dict (``{level, term_structure, skew, source}``), while the legacy
    engine5 path returns a plain string like ``"neutral"``. We accept
    both shapes here so the frontend gets clean ``volPressureNow`` /
    ``volStructure`` / ``volSkew`` / ``ivLevelNow`` fields and never
    sees a ``[object Object]`` spill.
    """
    ctx = trade.get("entryContext") or {}
    entry_vol_raw = ctx.get("volPressureState")
    iv_at_entry = _to_float(ctx.get("ivAtEntry"), default=None)
    iv_now = _to_float(ctx.get("ivNow"), default=None)

    # Normalise both sides into a (label, level, structure, skew) tuple.
    def _norm(vol: Any) -> Dict[str, Any]:
        if vol is None:
            return {"label": None, "level": None, "structure": None, "skew": None}
        if isinstance(vol, dict):
            lvl = _to_float(vol.get("level"), default=None)
            structure = vol.get("term_structure") or vol.get("structure")
            skew = vol.get("skew")
            # Build a compact label: prefer existing canonical label
            # fields, otherwise synthesise one from structure.
            label = vol.get("label") or vol.get("state") or structure
            return {
                "label": str(label) if label is not None else None,
                "level": lvl,
                "structure": str(structure) if structure is not None else None,
                "skew": str(skew) if skew is not None else None,
            }
        return {"label": str(vol), "level": None, "structure": None, "skew": None}

    now_n = _norm(current_vol)
    entry_n = _norm(entry_vol_raw)

    # If the dict carries a level and we don't have an explicit IV, use
    # that as the "IV now" reading so the tile shows a number.
    if iv_now is None and now_n["level"] is not None:
        iv_now = now_n["level"]
    if iv_at_entry is None and entry_n["level"] is not None:
        iv_at_entry = entry_n["level"]

    crush = None
    if iv_at_entry not in (None, 0.0) and iv_now is not None:
        crush = round((iv_now - iv_at_entry) / iv_at_entry * 100.0, 1)

    shift = None
    if now_n["label"] and entry_n["label"] and now_n["label"] != entry_n["label"]:
        shift = f"{entry_n['label']} -> {now_n['label']}"

    has_any = any([
        now_n["label"], entry_n["label"], iv_now is not None, iv_at_entry is not None,
    ])
    return {
        "available": bool(has_any),
        "now": iv_now,
        "atEntry": iv_at_entry,
        "crushSoFarPct": crush,
        "volPressureNow": now_n["label"],
        "volPressureAtEntry": entry_n["label"],
        "volStructure": now_n["structure"],
        "volSkew": now_n["skew"],
        "shift": shift,
    }


def _build_regime_evidence(
    trade: Dict[str, Any],
    tracking: Dict[str, Any],
    current_regime: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    ctx = trade.get("entryContext") or {}
    cur = current_regime or {}
    now_bucket = cur.get("bucket") or cur.get("label")
    entry_bucket = ctx.get("regimeBucket")
    available = bool(now_bucket or entry_bucket or tracking.get("regimeDriftScore") is not None)
    return {
        "available": available,
        "now": now_bucket,
        "atEntry": entry_bucket,
        "drift": tracking.get("regimeDriftScore"),
        "bucketShift": tracking.get("regimeDriftBucket"),
        "scoreNow": _round(cur.get("score"), 1),
        "scoreAtEntry": _round(ctx.get("regimeScore"), 1),
    }


def _build_time_decay_evidence(tracking: Dict[str, Any]) -> Dict[str, Any]:
    progress = _to_float(tracking.get("timeDecayProgress"), default=None)
    pct = None if progress is None else round(progress * 100.0, 0)
    return {
        "dte": tracking.get("dte"),
        "progress": progress,
        "progressPct": pct,
    }


def _status_chip(tracking: Dict[str, Any]) -> str:
    """Map the deterministic status into a UI chip name."""
    status = str(tracking.get("deterministicStatus") or "").lower()
    if status == "exit":
        return "breached"
    if status == "adjust":
        return "short_strike_challenged"
    if status == "caution":
        return "caution"
    return "on_track"


# ---------------------------------------------------------------------------
# Recommendation + action ladder
# ---------------------------------------------------------------------------

_VERDICT_FROM_STATUS = {
    "on_track": "HOLD",
    "caution": "HOLD",
    "adjust": "ADJUST",
    "exit": "CUT",
}


def _score_action_ladder(
    *,
    tracking: Dict[str, Any],
    replay: Dict[str, Any],
    history_breaker: Optional[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], str, float]:
    """Build the desk's HOLD / ADJUST / CUT_NOW ladder with probability bands.

    Mirrors Engine 1's ladder: HOLD reports p10/p50/p90 from the replay
    and ``probWin = fullCollectRate``; ADJUST captures the defensive
    case; CUT_NOW surfaces "guaranteed" close-at-mid.
    """
    status = str(tracking.get("deterministicStatus") or "on_track").lower()
    full_collect_frac = replay.get("fullCollectRateFrac")
    breach_frac = replay.get("breachRateFrac") or 0.0
    stop_out_frac = replay.get("stopOutRateFrac") or 0.0
    p10 = replay.get("p10PnlPct")
    p50 = replay.get("p50PnlPct")
    p90 = replay.get("p90PnlPct")
    mean_pnl = replay.get("meanPnlPct")
    median_pnl = replay.get("medianPnlPct")

    # "probWin" on HOLD is the probability the held position avoids a
    # tail event by expiry (i.e. doesn't breach or get stopped out).
    # The simulator's strict "fullCollect" bucket only counts paths that
    # kept 100% of credit, which understates the desk's actual win odds
    # for a typical SPX 1-DTE IC — the more truthful metric is
    # 1 − breach − stopOut. We fall back to fullCollect when the
    # combined rates are missing.
    if breach_frac is not None or stop_out_frac is not None:
        hold_prob_win = max(0.0, min(1.0, 1.0 - (breach_frac or 0.0) - (stop_out_frac or 0.0)))
    elif full_collect_frac is not None:
        hold_prob_win = float(full_collect_frac)
    else:
        hold_prob_win = None

    hb_level = str((history_breaker or {}).get("level") or "low").lower()
    hb_score = _to_float((history_breaker or {}).get("score"), default=0.0) or 0.0

    pre = _VERDICT_FROM_STATUS.get(status, "HOLD")
    if hb_level == "high" and pre == "HOLD":
        pre = "ADJUST"
    conf = {
        "HOLD": 0.7 if status == "on_track" else 0.62,
        "ADJUST": 0.74,
        "CUT": 0.86,
    }.get(pre, 0.65)
    if hold_prob_win is not None and pre == "HOLD":
        # Lift HOLD confidence to the realised win probability so a
        # 95%+ win-odds setup doesn't display "conf 70%" next to it.
        conf = max(conf, float(hold_prob_win))
    conf = round(min(max(conf, 0.55), 0.95), 2)

    # HOLD action — surface the replay's central tendency.
    hold_label = "Stay in plan; ride credit to expiry."
    if status == "caution":
        hold_label = "Hold but watch the tested side; pull in stops if drift accelerates."
    elif status == "adjust":
        hold_label = "Defensive hold only if liquidity prevents a clean adjust."
    elif hb_level in ("elevated", "high"):
        hold_label = "Hold with tighter risk; history-breaker is warning."

    adjust_label = "Roll the tested side or take partials to neutralize delta."
    if status == "exit":
        adjust_label = "Adjust only if you cannot exit cleanly; legging out preferred."

    cut_label = "Close at mid to lock the remaining credit and remove gap risk."

    # Probability heuristics for the ADJUST/CUT rows. We don't simulate
    # the adjusted structure, so these are deliberately conservative.
    adjust_prob = None
    if breach_frac is not None:
        adjust_prob = round(max(0.0, min(1.0, 0.55 - breach_frac * 0.5 + hb_score / 400.0)), 4)

    ladder: List[Dict[str, Any]] = [
        {
            "action": "HOLD",
            "label": hold_label,
            "rationale": hold_label,
            "expectedPnlPct": _round(median_pnl if median_pnl is not None else p50, 1),
            "p10PnlPct": _round(p10, 1),
            "p90PnlPct": _round(p90, 1),
            "probWin": (round(float(hold_prob_win), 4) if hold_prob_win is not None else None),
        },
        {
            "action": "CUT_NOW",
            "label": cut_label,
            "rationale": cut_label,
            "expectedPnlPct": None,
            "p10PnlPct": None,
            "p90PnlPct": None,
            "probWin": 1.0,
        },
        {
            "action": "ADJUST",
            "label": adjust_label,
            "rationale": adjust_label,
            "expectedPnlPct": None,
            "p10PnlPct": None,
            "p90PnlPct": None,
            "probWin": adjust_prob,
        },
    ]
    # Track mean for diagnostic; not part of the ladder rows.
    if mean_pnl is not None:
        ladder[0]["meanPnlPct"] = _round(mean_pnl, 1)
    return ladder, pre, conf


def _split_sentences(text: Optional[str], limit: int = 2) -> List[str]:
    """Naive sentence splitter that tolerates decimal numbers.

    The original implementation broke a sentence like ``"Current spot is
    7432.97, between the 7250 short put..."`` into two bullets because
    the ``.`` inside ``7432.97`` looked like a sentence terminator. We
    now only split on a terminator when (a) the next character is
    whitespace or EOS, and (b) the previous character is *not* a digit.
    """
    if not text:
        return []
    s = str(text)
    parts: List[str] = []
    buf = ""
    for i, ch in enumerate(s):
        buf += ch
        if ch in ".!?" and len(buf.strip()) > 12:
            prev_ch = s[i - 1] if i > 0 else ""
            next_ch = s[i + 1] if i + 1 < len(s) else ""
            # Skip splits inside decimals (``7432.97``) or numeric
            # abbreviations (``1.5x EM``).
            if prev_ch.isdigit() and (next_ch.isdigit() or (next_ch and not next_ch.isspace())):
                continue
            # Require either whitespace or end-of-string after the dot
            # so we don't cut mid-token.
            if next_ch and not next_ch.isspace():
                continue
            parts.append(buf.strip())
            buf = ""
            if len(parts) >= limit:
                return parts
    if buf.strip():
        parts.append(buf.strip())
    return parts[:limit]


def _build_recommendation(
    *,
    tracking: Dict[str, Any],
    replay: Dict[str, Any],
    llm: Dict[str, Any],
    history_breaker: Optional[Dict[str, Any]],
    ladder_pre: str,
    ladder_conf: float,
    ladder: List[Dict[str, Any]],
) -> Dict[str, Any]:
    # Verdict — the rule-based pre-verdict is the *floor*; the LLM is
    # allowed to escalate (e.g. HOLD -> ADJUST) but never to de-escalate
    # below what the deterministic tracker flagged. This keeps the desk
    # safe from an over-confident model green-lighting a tested trade.
    _SEVERITY = {"HOLD": 0, "ADJUST": 1, "CUT": 2}
    llm_status = str(llm.get("status") or "").lower()
    if llm_status == "exit":
        llm_verdict = "CUT"
    elif llm_status == "adjust":
        llm_verdict = "ADJUST"
    elif llm_status in ("caution", "on_track"):
        llm_verdict = "HOLD"
    else:
        llm_verdict = ladder_pre
    verdict = ladder_pre if _SEVERITY.get(ladder_pre, 0) >= _SEVERITY.get(llm_verdict, 0) else llm_verdict

    narrative = llm.get("recommendation") or llm.get("headline") or ""
    headline = llm.get("headline") or ""

    # Key points: bullet-ify the most desk-relevant LLM strands.
    key_points: List[str] = []
    for s in _split_sentences(llm.get("spotAnalysis"), limit=2):
        key_points.append(s)
    for s in _split_sentences(llm.get("regimeDrift"), limit=1):
        key_points.append(s)
    if llm.get("recommendation") and llm.get("recommendation") != narrative:
        for s in _split_sentences(llm.get("recommendation"), limit=1):
            key_points.append(s)

    # Risks: combine LLM risk update + history-breaker drivers + status
    # warnings so the desk sees both narrative and quantitative tail.
    risks: List[str] = []
    for s in _split_sentences(llm.get("riskUpdate"), limit=2):
        risks.append(s)
    drivers = (history_breaker or {}).get("drivers") or []
    if isinstance(drivers, list):
        for d in drivers[:2]:
            if d and d not in risks:
                risks.append(str(d))
    breach_frac = replay.get("breachRateFrac")
    if breach_frac is not None and breach_frac >= 0.10:
        risks.append(f"Replay shows {round(breach_frac * 100, 1)}% breach probability in matched analogues.")
    elif breach_frac is not None and breach_frac >= 0.05:
        risks.append(f"Replay carries a {round(breach_frac * 100, 1)}% breach tail to monitor.")
    if tracking.get("breachProxPut") and tracking.get("breachProxPut") >= 70:
        risks.append("Put-side breach proximity is above 70%; downside is the tested wing.")
    if tracking.get("breachProxCall") and tracking.get("breachProxCall") >= 70:
        risks.append("Call-side breach proximity is above 70%; upside is the tested wing.")

    desk_note = llm.get("deskNote") or ""

    return {
        "verdict": str(verdict).upper(),
        "confidence": ladder_conf,
        "narrative": narrative or headline or None,
        "headline": headline or None,
        "keyPoints": [p for p in key_points if p][:5],
        "riskFactors": [r for r in risks if r][:5],
        "deskNote": desk_note or None,
        "adjustmentIfNeeded": llm.get("adjustmentIfNeeded") or None,
        "preVerdict": ladder_pre,
        "preConfidence": ladder_conf,
        "actionLadder": ladder,
    }


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

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
    auto_phase = _auto_phase(trade)
    resolved_phase = _phase_for_trade(trade, phase)

    tracking = compute_trade_tracking(
        trade=trade,
        current_spot=float(current_spot),
        current_regime=current_regime,
        current_vol_pressure=current_vol,
    )
    llm = generate_checkin_analysis(trade=trade, tracking=tracking, flags=f)

    replay_summary: Dict[str, Any] = {}
    replay_error: Optional[str] = None
    try:
        entry_date = str(entry.get("entryDate") or (trade.get("loggedAt") or "")[:10])[:10]
        expiry = str(entry.get("expiryDate") or "")[:10]
        sp = _to_float(entry.get("shortPutStrike"), 0.0)
        lp = _to_float(entry.get("longPutStrike"), 0.0)
        sc = _to_float(entry.get("shortCallStrike"), 0.0)
        lc = _to_float(entry.get("longCallStrike"), 0.0)
        credit = _to_float(entry.get("entryCredit"), 0.0)
        if entry_date and expiry and sp and lp and sc and lc and credit and credit > 0:
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
        else:
            replay_error = "Insufficient entry data to replay (missing strikes/dates/credit)."
    except Exception as e:
        replay_error = f"{type(e).__name__}: {e}"
        replay_summary = {}

    history_breaker = (trade.get("entryContext") or {}).get("historyBreakerRisk")

    # Evidence assembly
    spot_evidence = _build_spot_evidence(trade, tracking)
    iv_evidence = _build_iv_evidence(trade, current_vol)
    regime_evidence = _build_regime_evidence(trade, tracking, current_regime)
    time_decay = _build_time_decay_evidence(tracking)

    replay_for_evidence: Dict[str, Any] = dict(replay_summary) if replay_summary else {}
    if not replay_summary:
        replay_for_evidence = {"available": False}
    if replay_error:
        replay_for_evidence["error"] = replay_error

    evidence: Dict[str, Any] = {
        "spot": spot_evidence,
        "iv": iv_evidence,
        "regime": regime_evidence,
        "timeDecay": time_decay,
        "historyBreaker": history_breaker,
        "replay": replay_for_evidence,
    }

    # Recommendation + action ladder
    ladder, pre_verdict, pre_conf = _score_action_ladder(
        tracking=tracking,
        replay=replay_summary or {},
        history_breaker=history_breaker,
    )
    recommendation = _build_recommendation(
        tracking=tracking,
        replay=replay_summary or {},
        llm=llm,
        history_breaker=history_breaker,
        ladder_pre=pre_verdict,
        ladder_conf=pre_conf,
        ladder=ladder,
    )

    status_chip = _status_chip(tracking)

    return {
        # E1-shape (new)
        "phase": resolved_phase,
        "phaseAuto": auto_phase,
        "phaseMismatch": (resolved_phase != auto_phase),
        "mode": trade.get("mode") or "live",
        "statusChip": status_chip,
        "currentSpot": tracking.get("currentSpot"),
        "nearestShortPct": spot_evidence.get("nearestShortPct"),
        "dte": tracking.get("dte"),
        "evidence": evidence,
        "recommendation": recommendation,
        # legacy fields preserved for back-compat
        "tracking": tracking,
        "actionLadder": {
            "preVerdict": pre_verdict,
            "confidence": int(round(pre_conf * 100)),
            "rows": [
                {
                    "action": r.get("action"),
                    "probability": (int(round((r.get("probWin") or 0.0) * 100))
                                    if r.get("probWin") is not None else 0),
                    "note": r.get("label") or r.get("rationale") or "",
                }
                for r in ladder
            ],
        },
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
            "fallbackReason": llm.get("_fallback_reason"),
        },
    }
