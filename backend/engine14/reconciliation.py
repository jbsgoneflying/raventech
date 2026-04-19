"""Engine 14 ↔ Engine 2 trade reconciliation.

Cross-check a user's iron-condor scenario against the Engine-2 scanner,
Engine-2 LLM advisor, and (optionally) a live ORATS NBBO snapshot. Emits
a compact set of ``checks`` plus an ``overall`` roll-up.

Each check has the shape::

    {
      "key":    "regimeBucket",
      "label":  "Regime bucket",
      "status": "agree" | "drift" | "mismatch" | "na",
      "e2":     <value>,
      "e14":    <value>,
      "rule":   "E2 vs E14 regime bucket must match when DMS is fresh.",
      "note":   "Human-readable context / top finding.",
    }

Checks never raise — a missing input collapses to ``status="na"`` with a
note so the caller can still render a row. This keeps the reconciliation
robust when (for example) Engine 2 is disabled or the advisor times out.

Design notes
------------
* ``reconcile_deterministic`` takes only the E14 scenario payload and the
  E2 scanner payload. It ships 8 chips that require no external I/O.
* ``reconcile_full`` extends the set with the LLM advisor and a live
  chain snapshot. Both are optional and short-circuit to "na" when
  unavailable. This is the flagship entrypoint used by the router.
* Thresholds are calibrated from the 4/20/2026 reference trade; see
  ``tests/test_engine14_reconciliation.py`` for the golden fixtures.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Tuple

Status = str   # "agree" | "drift" | "mismatch" | "na"
STATUS_ORDER = {"na": 0, "agree": 1, "drift": 2, "mismatch": 3}


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------

def _chip(
    key: str, label: str, status: Status, *,
    e2: Any = None, e14: Any = None,
    rule: str = "", note: str = "",
) -> Dict[str, Any]:
    return {
        "key": key, "label": label, "status": status,
        "e2": e2, "e14": e14,
        "rule": rule, "note": note,
    }


def _na(key: str, label: str, reason: str, *, rule: str = "") -> Dict[str, Any]:
    return _chip(key, label, "na", rule=rule, note=reason)


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _worst(statuses: List[Status]) -> Status:
    real = [s for s in statuses if s and s != "na"]
    if not real:
        return "na"
    return max(real, key=lambda s: STATUS_ORDER.get(s, 0))


def _rel_pct(a: Optional[float], b: Optional[float]) -> Optional[float]:
    """Relative difference from ``b`` as a positive percentage."""
    if a is None or b is None or b == 0:
        return None
    return abs(float(a) - float(b)) / abs(float(b)) * 100.0


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------

def _check_regime(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Dict[str, Any]:
    label = "Regime bucket"
    rule = "E14 regime (DMS-sourced) must match E2 current regime bucket."

    e14_bucket = ((scenario.get("entryState") or {}).get("regimeBucket") or "").upper() or None
    e14_source = (scenario.get("entryState") or {}).get("regimeSource")
    e2_bucket = (((e2.get("current") or {}).get("regime") or {}).get("bucket") or "").upper() or None
    e2_score = ((e2.get("current") or {}).get("regime") or {}).get("score100")

    if not e14_bucket or not e2_bucket:
        return _na("regimeBucket", label, "Regime data unavailable on one side.", rule=rule)

    if e14_bucket == e2_bucket:
        note = f"Both engines agree: {e14_bucket}"
        if e14_source == "em_proxy":
            note += " (E14 fallback via EM-proxy — DMS not read)."
        return _chip(
            "regimeBucket", label, "agree",
            e2={"bucket": e2_bucket, "score100": e2_score},
            e14={"bucket": e14_bucket, "source": e14_source},
            rule=rule, note=note,
        )

    return _chip(
        "regimeBucket", label, "mismatch",
        e2={"bucket": e2_bucket, "score100": e2_score},
        e14={"bucket": e14_bucket, "source": e14_source},
        rule=rule,
        note=(
            f"E2={e2_bucket} vs E14={e14_bucket}. "
            "Analogue cohort will systematically miss today's regime."
        ),
    )


def _check_spot(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Dict[str, Any]:
    label = "Spot price"
    rule = "E2 smartSpot vs E14 userSpot must agree within 1%."

    e14_spot = _to_float((scenario.get("entryState") or {}).get("userSpot"))
    em = e2.get("expectedMove") or {}
    e2_spot = _to_float(em.get("smartSpotPrice") or em.get("spotPrice"))

    if e14_spot is None or e2_spot is None:
        return _na("spotPrice", label, "Spot missing on one side.", rule=rule)

    diff_pct = _rel_pct(e14_spot, e2_spot) or 0.0
    if diff_pct <= 0.1:
        status = "agree"
    elif diff_pct <= 1.0:
        status = "drift"
    else:
        status = "mismatch"

    return _chip(
        "spotPrice", label, status,
        e2=e2_spot, e14=e14_spot,
        rule=rule,
        note=f"Δ = {diff_pct:.2f}%",
    )


def _check_expected_move(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Dict[str, Any]:
    label = "Expected move %"
    rule = "E14 userEmPct should track E2 ORATS EM within ±5% (≤15% drift)."

    e14_em = _to_float((scenario.get("entryState") or {}).get("userEmPct"))
    em = e2.get("expectedMove") or {}
    # ORATS delayed is the authoritative anchor (used by strikeTargets); fall back to live straddle.
    e2_em = (
        _to_float(em.get("oratsExpectedMovePct"))
        or _to_float(em.get("delayedImpliedMovePct"))
        or _to_float(em.get("expectedMovePct"))
    )

    if e14_em is None or e2_em is None:
        return _na("expectedMovePct", label, "Expected-move data missing.", rule=rule)

    diff_pct = _rel_pct(e14_em, e2_em) or 0.0
    if diff_pct <= 5.0:
        status = "agree"
    elif diff_pct <= 15.0:
        status = "drift"
    else:
        status = "mismatch"

    return _chip(
        "expectedMovePct", label, status,
        e2={"pct": e2_em, "source": em.get("oratsExpectedMoveSource") or "orats"},
        e14={"pct": e14_em},
        rule=rule,
        note=f"Δ = {diff_pct:.1f}% (abs)",
    )


def _user_half_width_pct(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Optional[float]:
    """Average of (spot-shortPut) and (shortCall-spot) as % of spot."""
    req = scenario.get("request") or {}
    e14_spot = _to_float((scenario.get("entryState") or {}).get("userSpot"))
    em = e2.get("expectedMove") or {}
    spot = e14_spot or _to_float(em.get("smartSpotPrice") or em.get("spotPrice"))
    sp = _to_float(req.get("short_put"))
    sc = _to_float(req.get("short_call"))
    if not (spot and spot > 0 and sp and sc):
        return None
    put_dist = abs(spot - sp)
    call_dist = abs(sc - spot)
    return ((put_dist + call_dist) / 2.0) / spot * 100.0


def _check_em_multiple_label(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Dict[str, Any]:
    label = "EM multiple / box"
    rule = "User half-width should map to a named E2 strikeTarget box."

    st = e2.get("strikeTargets") or {}
    half = _user_half_width_pct(scenario, e2)
    if not st or half is None:
        return _na("emMultipleLabel", label, "Strike targets or spot missing.", rule=rule)

    boxes = [
        ("White", 1.0, _to_float(st.get("whitePct"))),
        ("Blue",  1.5, _to_float(st.get("bluePct"))),
        ("Red",   2.0, _to_float(st.get("redPct"))),
    ]
    usable = [(n, m, p) for (n, m, p) in boxes if p]
    if not usable:
        return _na("emMultipleLabel", label, "strikeTargets percentages unavailable.", rule=rule)

    name, mult, nearest_pct = min(usable, key=lambda x: abs(half - x[2]))
    delta_mult = (half / float(nearest_pct)) * float(mult) - float(mult)

    if abs(delta_mult) <= 0.15:
        status = "agree"
    elif abs(delta_mult) <= 0.30:
        status = "drift"
    else:
        status = "mismatch"

    e14_em_mult = (scenario.get("entryState") or {}).get("userEmMultiple")
    return _chip(
        "emMultipleLabel", label, status,
        e2={"box": name, "multiple": mult, "targetPct": nearest_pct},
        e14={"halfWidthPct": round(half, 3), "userEmMultiple": e14_em_mult},
        rule=rule,
        note=f"Half-width {half:.2f}% sits {'at' if status=='agree' else 'near' if status=='drift' else 'off'} "
             f"the {name} box (expected {nearest_pct:.2f}%).",
    )


def _check_desk_floor(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Dict[str, Any]:
    label = "Desk EM floor"
    rule = "User EM multiple must clear deskConsensus.suggestedEmFloor."

    dc = e2.get("deskConsensus") or {}
    floor = _to_float(dc.get("suggestedEmFloor"))
    user_em_mult = _to_float((scenario.get("entryState") or {}).get("userEmMultiple"))

    if floor is None or user_em_mult is None:
        return _na("deskEmFloor", label, "Floor or user EM multiple missing.", rule=rule)

    if user_em_mult >= floor + 0.05:
        status = "agree"
    elif user_em_mult >= floor - 0.05:
        status = "drift"
    else:
        status = "mismatch"

    return _chip(
        "deskEmFloor", label, status,
        e2={"suggestedEmFloor": floor, "riskLevel": dc.get("riskLevel")},
        e14={"userEmMultiple": user_em_mult},
        rule=rule,
        note=f"User {user_em_mult:.2f}× vs floor {floor:.2f}×.",
    )


def _policy_cell_for_user(
    e2: Dict[str, Any],
    user_em_mult: Optional[float],
    user_wing_width: Optional[float],
) -> Optional[Dict[str, Any]]:
    if user_em_mult is None or user_wing_width is None:
        return None
    rows = e2.get("widthComparison") or []
    if not rows:
        return None

    def _score(row: Dict[str, Any]) -> Tuple[float, float]:
        em = _to_float(row.get("emMult")) or 0.0
        wp = _to_float(row.get("wingWidthPts")) or 0.0
        return (abs(em - user_em_mult), abs(wp - user_wing_width))

    return min(rows, key=_score)


def _check_policy(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Dict[str, Any]:
    label = "E2 policy"
    rule = "widthComparison cell must satisfy recommendation.policy thresholds."

    rec = e2.get("recommendation") or {}
    policy = rec.get("policy") or {}
    user_em_mult = _to_float((scenario.get("entryState") or {}).get("userEmMultiple"))
    user_wing = _to_float((scenario.get("entryState") or {}).get("wingWidth"))

    cell = _policy_cell_for_user(e2, user_em_mult, user_wing)
    if cell is None or not policy:
        return _na("policyConstraints", label, "No widthComparison cell or policy.", rule=rule)

    violations = []
    max_breach = _to_float(policy.get("maxBreachPct"))
    max_outside = _to_float(policy.get("maxOutsideWingsPct"))
    max_mae = _to_float(policy.get("maxMae95xWing"))

    if max_breach is not None and (_to_float(cell.get("breachPct")) or 0) > max_breach:
        violations.append(f"breachPct {cell.get('breachPct')} > {max_breach}")
    if max_outside is not None and (_to_float(cell.get("outsidePct")) or 0) > max_outside:
        violations.append(f"outsidePct {cell.get('outsidePct')} > {max_outside}")
    if max_mae is not None and (_to_float(cell.get("avgMae95xWing")) or 0) > max_mae:
        violations.append(f"avgMae95xWing {cell.get('avgMae95xWing')} > {max_mae}")

    if not violations:
        status = "agree"
    elif len(violations) == 1:
        status = "drift"
    else:
        status = "mismatch"

    return _chip(
        "policyConstraints", label, status,
        e2={"policy": policy, "cell": {
            "emMult": cell.get("emMult"), "wingWidthPts": cell.get("wingWidthPts"),
            "breachPct": cell.get("breachPct"), "outsidePct": cell.get("outsidePct"),
            "avgMae95xWing": cell.get("avgMae95xWing"),
        }},
        e14={"userEmMultiple": user_em_mult, "wingWidth": user_wing},
        rule=rule,
        note=("Meets all policy thresholds."
              if not violations else f"{len(violations)} violation(s): " + "; ".join(violations)),
    )


def _check_breach_rate(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
) -> Dict[str, Any]:
    label = "Breach rate"
    rule = "E2 pBreach (oddsLikeNow) vs E14 (breach + stopOut) within ±5pp."

    user_em_mult = _to_float((scenario.get("entryState") or {}).get("userEmMultiple"))
    odds = ((e2.get("oddsLikeNow") or {}).get("byWidth") or [])
    if user_em_mult is None or not odds:
        return _na("breachRate", label, "Odds or EM multiple missing.", rule=rule)

    row = min(odds, key=lambda r: abs((_to_float(r.get("w")) or 0.0) - user_em_mult))
    e2_breach = _to_float(row.get("breachEitherPct"))

    dist = scenario.get("outcomeDistribution") or {}
    e14_breach_pct = (_to_float((dist.get("breach") or {}).get("pct")) or 0.0) + \
                     (_to_float((dist.get("stopOut") or {}).get("pct")) or 0.0)

    if e2_breach is None:
        return _na("breachRate", label, "E2 breach % unavailable at user EM multiple.", rule=rule)

    diff = abs(e14_breach_pct - e2_breach)
    if diff <= 5.0:
        status = "agree"
    elif diff <= 15.0:
        status = "drift"
    else:
        status = "mismatch"

    return _chip(
        "breachRate", label, status,
        e2={"pBreachEitherPct": e2_breach, "atEmMult": row.get("w"), "n": row.get("n")},
        e14={"breachPlusStopOutPct": round(e14_breach_pct, 2)},
        rule=rule,
        note=f"Δ = {diff:.1f}pp",
    )


def _check_conditioning(scenario: Dict[str, Any]) -> Dict[str, Any]:
    label = "Conditioning modifiers"
    rule = "Show whether forward-looking modifiers materially move the base distribution."

    mods = scenario.get("conditioningModifiers") or {}
    net_tail = _to_float(mods.get("netTailMultiplier"))
    net_wr = _to_float(mods.get("netWinRateShiftPct"))
    if net_tail is None and net_wr is None:
        return _na("conditioningNetEffect", label, "No conditioning modifiers emitted.", rule=rule)

    tail_d = 0.0 if net_tail is None else abs(net_tail - 1.0)
    wr_d = 0.0 if net_wr is None else abs(net_wr)
    material = (tail_d >= 0.05) or (wr_d >= 1.0)
    status = "drift" if material else "agree"

    return _chip(
        "conditioningNetEffect", label, status,
        e2=None,
        e14={"netTailMultiplier": net_tail, "netWinRateShiftPct": net_wr, "material": material},
        rule=rule,
        note=(
            f"Material shift: tail ×{net_tail:.2f}, WR {net_wr:+.1f}pp."
            if material else
            f"Near-zero net effect (tail ×{net_tail or 1.0:.2f}, WR {net_wr or 0.0:+.1f}pp) — "
            "adjustedOutcomeDistribution ≈ base."
        ),
    )


# ---------------------------------------------------------------------------
# LLM + live-chain checks (Stage 1.5)
# ---------------------------------------------------------------------------

_CREDIT_NUM_RE = re.compile(r"[-+]?\d*\.?\d+")


def _parse_credit_estimate(raw: Any) -> Optional[float]:
    """Best-effort numeric parser for strings like '~$0.20' or '$0.60'."""
    if raw is None:
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    m = _CREDIT_NUM_RE.search(str(raw))
    if not m:
        return None
    try:
        return float(m.group(0))
    except ValueError:
        return None


def _check_llm_verdict(advisor: Dict[str, Any]) -> Dict[str, Any]:
    label = "Advisor verdict"
    rule = "LLM PASS agree / LEAN_PASS drift / LEAN_FAIL|FAIL mismatch."
    adv = advisor or {}
    verdict = str(adv.get("verdict") or "").upper()
    conf = _to_float(adv.get("confidence"))
    if not verdict:
        return _na("llmVerdict", label, "Advisor not run or returned no verdict.", rule=rule)
    # If the advisor short-circuited to its static fallback (OpenAI unreachable,
    # rate-limited, etc.), the PASS/confidence fields are stubs and must not
    # be trusted — render as na with the real reason surfaced.
    if str(adv.get("_source") or "").lower() == "fallback":
        return _na(
            "llmVerdict", label,
            f"Advisor unavailable: {adv.get('_fallback_reason') or 'fallback path'}.",
            rule=rule,
        )

    if verdict == "PASS":
        status = "agree"
    elif verdict == "LEAN_PASS":
        status = "drift"
    else:
        status = "mismatch"

    return _chip(
        "llmVerdict", label, status,
        e2={"verdict": verdict, "confidence": conf},
        e14=None,
        rule=rule,
        note=(advisor or {}).get("deskNote") or (advisor or {}).get("passReason") or "",
    )


def _check_llm_strikes(
    scenario: Dict[str, Any],
    advisor: Dict[str, Any],
) -> Dict[str, Any]:
    label = "Advisor strikes"
    rule = "All four advisor tradeTicket strikes should equal user's four strikes."
    ticket = (advisor or {}).get("tradeTicket") or {}
    if not ticket:
        return _na("llmStrikesMatchUser", label, "Advisor ticket unavailable.", rule=rule)

    req = scenario.get("request") or {}
    legs = [
        ("shortPut",  _to_float(ticket.get("shortPutStrike")),  _to_float(req.get("short_put"))),
        ("longPut",   _to_float(ticket.get("longPutStrike")),   _to_float(req.get("long_put"))),
        ("shortCall", _to_float(ticket.get("shortCallStrike")), _to_float(req.get("short_call"))),
        ("longCall",  _to_float(ticket.get("longCallStrike")),  _to_float(req.get("long_call"))),
    ]
    if any(a is None or b is None for _, a, b in legs):
        return _na("llmStrikesMatchUser", label, "Strike data incomplete.", rule=rule)

    exact = sum(1 for _, a, b in legs if abs(a - b) < 1e-6)
    near = sum(1 for _, a, b in legs if 1e-6 <= abs(a - b) <= 5.0)
    far = 4 - exact - near
    if exact == 4:
        status = "agree"
    elif far == 0 and near <= 2:
        status = "drift"
    else:
        status = "mismatch"

    return _chip(
        "llmStrikesMatchUser", label, status,
        e2={"ticket": ticket},
        e14={"request": {
            "short_put": req.get("short_put"), "long_put": req.get("long_put"),
            "short_call": req.get("short_call"), "long_call": req.get("long_call"),
        }},
        rule=rule,
        note=f"{exact}/4 exact, {near}/4 within 5pts, {far}/4 off",
    )


def _check_llm_wing(
    scenario: Dict[str, Any],
    advisor: Dict[str, Any],
) -> Dict[str, Any]:
    label = "Advisor wing width"
    rule = "Advisor wingWidth should match user's wing width."
    ticket = (advisor or {}).get("tradeTicket") or {}
    adv_wing = _to_float(ticket.get("wingWidth"))
    user_wing = _to_float((scenario.get("entryState") or {}).get("wingWidth"))
    if adv_wing is None or user_wing is None:
        return _na("llmWingMatchUser", label, "Wing width data missing.", rule=rule)

    diff = abs(adv_wing - user_wing)
    status = "agree" if diff < 1e-6 else ("drift" if diff <= 5.0 else "mismatch")
    return _chip(
        "llmWingMatchUser", label, status,
        e2={"wingWidth": adv_wing},
        e14={"wingWidth": user_wing},
        rule=rule,
        note=f"Δ = {diff:.1f} pts",
    )


def _check_credit_quad(
    scenario: Dict[str, Any],
    e2: Dict[str, Any],
    advisor: Optional[Dict[str, Any]],
    live_chain: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """Flagship check: user-typed credit vs live NBBO mid vs LLM estimate vs E2 proxy."""
    label = "Credit plausibility"
    rule = ("User credit should fit live NBBO [bid, ask] and be within ±15% of mid; "
            "secondary anchors are advisor.tradeTicket.estimatedCredit and "
            "widthComparison.creditProxy.")

    user = _to_float((scenario.get("request") or {}).get("credit_received"))
    if user is None:
        return _na("creditQuad", label, "User credit missing.", rule=rule)

    live_mid = None
    live_bid = None
    live_ask = None
    if live_chain:
        live_mid = _to_float(live_chain.get("mid"))
        live_bid = _to_float(live_chain.get("netBid"))
        live_ask = _to_float(live_chain.get("netAsk"))

    llm_est = None
    if advisor:
        llm_est = _parse_credit_estimate((advisor.get("tradeTicket") or {}).get("estimatedCredit"))

    user_em_mult = _to_float((scenario.get("entryState") or {}).get("userEmMultiple"))
    user_wing = _to_float((scenario.get("entryState") or {}).get("wingWidth"))
    cell = _policy_cell_for_user(e2, user_em_mult, user_wing)
    proxy = None
    if cell:
        # creditProxy is in cents-per-1lot dollars (e.g. 20 -> $0.20 per 1-lot).
        cp_raw = _to_float(cell.get("creditProxy"))
        if cp_raw is not None:
            proxy = cp_raw / 100.0

    anchors = {
        "user": user,
        "liveMid": live_mid, "liveBid": live_bid, "liveAsk": live_ask,
        "advisorEstimate": llm_est,
        "widthComparisonProxy": proxy,
    }

    if live_mid is not None:
        inside_nbbo = (live_bid is None or user >= live_bid - 1e-6) and \
                      (live_ask is None or user <= live_ask + 1e-6)
        mid_diff_pct = abs(user - live_mid) / max(1e-6, live_mid) * 100.0
        if inside_nbbo and mid_diff_pct <= 15.0:
            status = "agree"
            note = f"User ${user:.2f} inside NBBO and within {mid_diff_pct:.1f}% of ${live_mid:.2f} mid."
        elif mid_diff_pct <= 50.0:
            status = "drift"
            note = (f"User ${user:.2f} "
                    f"{'outside NBBO' if not inside_nbbo else 'off mid'} "
                    f"({mid_diff_pct:.0f}% vs ${live_mid:.2f} mid).")
        else:
            status = "mismatch"
            note = (f"User ${user:.2f} is {mid_diff_pct:.0f}% off live mid ${live_mid:.2f} "
                    "— likely stale quote or chain-pricing divergence.")
    elif llm_est is not None or proxy is not None:
        anchor = llm_est if llm_est is not None else proxy
        anchor_name = "advisor estimate" if llm_est is not None else "width-comparison proxy"
        diff_pct = abs(user - anchor) / max(1e-6, anchor) * 100.0
        if diff_pct <= 25.0:
            status = "agree"
        elif diff_pct <= 75.0:
            status = "drift"
        else:
            status = "mismatch"
        note = f"User ${user:.2f} vs {anchor_name} ${anchor:.2f} ({diff_pct:.0f}% off)."
    else:
        return _na("creditQuad", label, "No anchors available for credit check.", rule=rule)

    return _chip(
        "creditQuad", label, status,
        e2={"proxy": proxy, "advisorEstimate": llm_est,
            "liveMid": live_mid, "liveBid": live_bid, "liveAsk": live_ask},
        e14={"userCredit": user},
        rule=rule,
        note=note,
    )


# ---------------------------------------------------------------------------
# Top-level entrypoints
# ---------------------------------------------------------------------------

def reconcile_deterministic(
    *,
    scenario_result: Dict[str, Any],
    engine2_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """Stage 1 — 8 deterministic chips, no external I/O."""
    checks = [
        _check_regime(scenario_result, engine2_payload),
        _check_spot(scenario_result, engine2_payload),
        _check_expected_move(scenario_result, engine2_payload),
        _check_em_multiple_label(scenario_result, engine2_payload),
        _check_desk_floor(scenario_result, engine2_payload),
        _check_policy(scenario_result, engine2_payload),
        _check_breach_rate(scenario_result, engine2_payload),
        _check_conditioning(scenario_result),
    ]
    return _wrap(checks)


def reconcile_full(
    *,
    scenario_result: Dict[str, Any],
    engine2_payload: Dict[str, Any],
    engine2_advisor: Optional[Dict[str, Any]] = None,
    live_chain: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Stage 1.5 — adds llmVerdict / llmStrikes / llmWing / creditQuad when available."""
    checks = [
        _check_regime(scenario_result, engine2_payload),
        _check_spot(scenario_result, engine2_payload),
        _check_expected_move(scenario_result, engine2_payload),
        _check_em_multiple_label(scenario_result, engine2_payload),
        _check_desk_floor(scenario_result, engine2_payload),
        _check_policy(scenario_result, engine2_payload),
        _check_breach_rate(scenario_result, engine2_payload),
        _check_conditioning(scenario_result),
    ]

    # The credit quad is the flagship of the full reconcile; keep it even
    # when advisor / live_chain are missing (it'll collapse to na if both
    # anchors are absent).
    checks.append(_check_credit_quad(scenario_result, engine2_payload, engine2_advisor, live_chain))

    if engine2_advisor:
        checks.append(_check_llm_verdict(engine2_advisor))
        checks.append(_check_llm_strikes(scenario_result, engine2_advisor))
        checks.append(_check_llm_wing(scenario_result, engine2_advisor))

    return _wrap(checks)


def summarize_for_journal(
    reconcile_payload: Optional[Dict[str, Any]],
    *,
    max_findings: int = 5,
    note_char_cap: int = 280,
) -> Optional[Dict[str, Any]]:
    """Compress a full reconcile payload into a journal-safe snapshot.

    The trade-journal record keeps a permanent reference to "what the desk
    knew at entry" so post-trade reviews can reason about whether a losing
    trade had a detectable mismatch up-front. Storing the full
    ``reconcile_full`` payload is wasteful (it contains the whole Engine 2
    cell grid, verbose rules, etc.) so this helper produces a compact
    snapshot with just the pieces we render in the journal UI:

      * ``overall``  – status + counts + top findings (clipped to N)
      * ``checks``   – only ``key``, ``status``, ``label``, truncated ``note``
      * ``generatedAt`` – UTC timestamp of when the snapshot was captured.

    Accepts ``None`` / missing keys and returns ``None`` so callers can
    chain ``entryContext["reconcile"] = summarize_for_journal(payload)``
    without worrying about null-guarding.
    """
    if not reconcile_payload or not isinstance(reconcile_payload, dict):
        return None

    overall = reconcile_payload.get("overall") or {}
    raw_checks = reconcile_payload.get("checks") or []
    if not overall and not raw_checks:
        return None

    top_findings = list((overall.get("topFindings") or [])[: max(0, int(max_findings))])
    compact_checks: List[Dict[str, Any]] = []
    for c in raw_checks:
        if not isinstance(c, dict):
            continue
        note = c.get("note") or ""
        if isinstance(note, str) and len(note) > note_char_cap:
            note = note[: note_char_cap - 1].rstrip() + "\u2026"
        compact_checks.append({
            "key": c.get("key"),
            "label": c.get("label"),
            "status": c.get("status") or "na",
            "note": note or None,
        })

    return {
        "overall": {
            "status": overall.get("status") or "na",
            "counts": dict(overall.get("counts") or {}),
            "topFindings": top_findings,
        },
        "checks": compact_checks,
        "generatedAt": _utc_iso_now(),
    }


def _utc_iso_now() -> str:
    import datetime as _dt
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _wrap(checks: List[Dict[str, Any]]) -> Dict[str, Any]:
    overall_status = _worst([c.get("status", "na") for c in checks])
    findings = [
        f"{c['label']}: {c.get('note') or c.get('status')}"
        for c in checks
        if c.get("status") in ("drift", "mismatch")
    ][:3]

    return {
        "overall": {
            "status": overall_status,
            "counts": {
                "agree": sum(1 for c in checks if c.get("status") == "agree"),
                "drift": sum(1 for c in checks if c.get("status") == "drift"),
                "mismatch": sum(1 for c in checks if c.get("status") == "mismatch"),
                "na": sum(1 for c in checks if c.get("status") == "na"),
                "total": len(checks),
            },
            "topFindings": findings,
        },
        "checks": checks,
    }
