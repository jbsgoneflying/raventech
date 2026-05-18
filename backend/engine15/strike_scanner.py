"""Engine 15 — Strike Scanner.

Given a baseline E15 scenario, sweep ~120 nearby IC variants (strikes +
structure), re-price each at the entry chain, score against the baseline,
and emit one of four verdicts:

  - ``dominating``         — strictly better on credit AND breach risk
  - ``safer_alternative``  — within 90% of baseline credit, materially lower breach
  - ``richer_alternative`` — within 10% of baseline breach, materially higher credit
  - ``optimal``            — no candidate Pareto-dominates baseline

This module is pure-Python and I/O-free. The router supplies the entry
chain + baseline matchedEvents; this module returns a structured verdict.
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.engine14.chain_replay import ChainRow, FillModel, reprice_ic

LOG = logging.getLogger("engine15.strike_scanner")


# ---------------------------------------------------------------------------
# Strike step inference
# ---------------------------------------------------------------------------

# Common option-chain strike grids. We infer the per-ticker step empirically
# from the spread between adjacent strikes in the user's baseline, then
# round to the nearest plausible value.
_PLAUSIBLE_STEPS: Tuple[float, ...] = (0.5, 1.0, 2.5, 5.0, 10.0, 25.0)


def infer_strike_step(strikes: Sequence[float]) -> float:
    """Infer the typical strike step for this underlying from the user's
    baseline four strikes. Falls back to 1.0 if we can't tell.

    Logic: take the gaps between sorted strikes, pick the GCD-ish minimum
    nonzero gap, and snap to the nearest plausible step value.
    """
    sorted_k = sorted(set(float(s) for s in strikes if s is not None))
    if len(sorted_k) < 2:
        return 1.0
    gaps = [sorted_k[i + 1] - sorted_k[i] for i in range(len(sorted_k) - 1)]
    gaps = [g for g in gaps if g > 0.0]
    if not gaps:
        return 1.0
    min_gap = min(gaps)
    # Snap min_gap to the nearest plausible step (prefer smaller).
    best = _PLAUSIBLE_STEPS[0]
    best_dist = abs(min_gap - best)
    for step in _PLAUSIBLE_STEPS:
        if step > min_gap + 1e-6:
            continue
        d = abs(min_gap - step)
        if d < best_dist - 1e-9 or (abs(d - best_dist) < 1e-9 and step > best):
            best = step
            best_dist = d
    return float(best)


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

# Structure tags. Drives the candidate generator's families and the UI labels.
STRUCTURE_IC = "iron_condor"
STRUCTURE_FLY = "iron_fly"
STRUCTURE_ASYM = "asymmetric_ic"
STRUCTURE_PUT_VERT = "put_vertical"
STRUCTURE_CALL_VERT = "call_vertical"


@dataclass(frozen=True)
class CandidateStrikes:
    """A four-leg IC (or one-sided vertical) candidate.

    For verticals, the non-existent side has both strikes set to ``None``.
    The structure tag drives downstream pricing + UI rendering.
    """

    short_put: Optional[float]
    long_put: Optional[float]
    short_call: Optional[float]
    long_call: Optional[float]
    structure: str

    @property
    def is_two_sided(self) -> bool:
        return self.structure in (STRUCTURE_IC, STRUCTURE_FLY, STRUCTURE_ASYM)

    @property
    def put_wing_width(self) -> float:
        if self.short_put is None or self.long_put is None:
            return 0.0
        return abs(float(self.short_put) - float(self.long_put))

    @property
    def call_wing_width(self) -> float:
        if self.short_call is None or self.long_call is None:
            return 0.0
        return abs(float(self.long_call) - float(self.short_call))

    @property
    def max_wing_width(self) -> float:
        return max(self.put_wing_width, self.call_wing_width)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "shortPut":  self.short_put,
            "longPut":   self.long_put,
            "shortCall": self.short_call,
            "longCall":  self.long_call,
            "structure": self.structure,
        }

    def short_strikes_summary(self) -> str:
        """Compact display string for verdict headlines."""
        parts: List[str] = []
        if self.short_put is not None:
            parts.append(f"SP {self.short_put:g}")
        if self.long_put is not None and self.long_put != self.short_put:
            parts.append(f"LP {self.long_put:g}")
        if self.short_call is not None:
            parts.append(f"SC {self.short_call:g}")
        if self.long_call is not None and self.long_call != self.short_call:
            parts.append(f"LC {self.long_call:g}")
        return " / ".join(parts)


@dataclass
class ScoredCandidate:
    """A candidate after re-pricing + breach-rate estimation."""

    strikes: CandidateStrikes
    credit: float            # per-contract premium (points)
    max_loss: float          # per-contract max loss (points)
    p_breach: float          # estimated breach probability (0..1)
    p_breach_interval: Tuple[float, float]
    ev: float                # naive EV: (1 - p_breach) * credit - p_breach * max_loss
    delta_breach_pct: float  # signed pct change vs baseline (negative = safer)
    delta_credit_pct: float
    delta_max_loss_pct: float
    delta_ev_pct: float
    is_baseline: bool = False
    rationale: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["strikes"] = self.strikes.to_dict()
        d["p_breach_interval"] = list(self.p_breach_interval)
        return d


# ---------------------------------------------------------------------------
# Candidate generation
# ---------------------------------------------------------------------------

_STRIKE_SHIFTS: Tuple[int, ...] = (-3, -2, -1, 0, 1, 2, 3)
_WING_WIDTH_STEPS: Tuple[int, ...] = (1, 2, 3, 4, 5)


def generate_candidates(
    *,
    baseline_strikes: CandidateStrikes,
    strike_step: float,
) -> List[CandidateStrikes]:
    """Build a deduplicated grid of strike/structure variants around the
    baseline. Excludes the baseline tuple itself.
    """
    if baseline_strikes.structure != STRUCTURE_IC or not baseline_strikes.is_two_sided:
        # We only generate variants for full ICs; a vertical baseline gets
        # a minimal sweep of strike shifts on its own legs.
        return _generate_vertical_variants(baseline_strikes, strike_step)

    sp0 = float(baseline_strikes.short_put)
    lp0 = float(baseline_strikes.long_put)
    sc0 = float(baseline_strikes.short_call)
    lc0 = float(baseline_strikes.long_call)
    put_wing = sp0 - lp0
    call_wing = lc0 - sc0
    seen: set = set()
    out: List[CandidateStrikes] = []

    def _push(sp: float, lp: float, sc: float, lc: float, structure: str) -> None:
        if not (lp < sp < sc < lc) and structure in (STRUCTURE_IC, STRUCTURE_ASYM):
            return
        if structure == STRUCTURE_FLY and not (lp < sp <= sc < lc):
            return
        # Keep widths sensible — at least one strike step on each side.
        if structure in (STRUCTURE_IC, STRUCTURE_ASYM) and (sp - lp) < strike_step - 1e-9:
            return
        if structure in (STRUCTURE_IC, STRUCTURE_ASYM) and (lc - sc) < strike_step - 1e-9:
            return
        key = (round(sp, 4), round(lp, 4), round(sc, 4), round(lc, 4), structure)
        if key in seen:
            return
        seen.add(key)
        out.append(CandidateStrikes(
            short_put=sp, long_put=lp, short_call=sc, long_call=lc, structure=structure,
        ))

    # Family 1: strikes sweep — shift each short leg, keep wing widths.
    for ds_put in _STRIKE_SHIFTS:
        for ds_call in _STRIKE_SHIFTS:
            if ds_put == 0 and ds_call == 0:
                continue
            sp = sp0 + ds_put * strike_step
            sc = sc0 + ds_call * strike_step
            lp = sp - put_wing
            lc = sc + call_wing
            structure = STRUCTURE_ASYM if (ds_put != ds_call) else STRUCTURE_IC
            _push(sp, lp, sc, lc, structure)

    # Family 2: wing-width variants — keep shorts, vary wing widths.
    for put_w_steps in _WING_WIDTH_STEPS:
        for call_w_steps in _WING_WIDTH_STEPS:
            if (
                abs(put_wing - put_w_steps * strike_step) < 1e-6
                and abs(call_wing - call_w_steps * strike_step) < 1e-6
            ):
                continue
            sp = sp0
            sc = sc0
            lp = sp0 - put_w_steps * strike_step
            lc = sc0 + call_w_steps * strike_step
            _push(sp, lp, sc, lc, STRUCTURE_IC)

    # Family 3: iron-fly variants — touching shorts (SP == SC == midpoint).
    midpoint = (sp0 + sc0) / 2.0
    # Snap midpoint to the strike grid.
    snapped_mid = round(midpoint / strike_step) * strike_step
    for wing_steps in _WING_WIDTH_STEPS:
        wing_w = wing_steps * strike_step
        _push(
            sp=snapped_mid, lp=snapped_mid - wing_w,
            sc=snapped_mid, lc=snapped_mid + wing_w,
            structure=STRUCTURE_FLY,
        )

    # Family 4: one-sided verticals.
    out.append(CandidateStrikes(
        short_put=sp0, long_put=lp0, short_call=None, long_call=None,
        structure=STRUCTURE_PUT_VERT,
    ))
    out.append(CandidateStrikes(
        short_put=None, long_put=None, short_call=sc0, long_call=lc0,
        structure=STRUCTURE_CALL_VERT,
    ))

    return out


def _generate_vertical_variants(
    baseline: CandidateStrikes, strike_step: float,
) -> List[CandidateStrikes]:
    """Minimal sweep when the baseline is already a vertical."""
    out: List[CandidateStrikes] = []
    if baseline.structure == STRUCTURE_PUT_VERT and baseline.short_put is not None:
        sp0 = float(baseline.short_put)
        lp0 = float(baseline.long_put) if baseline.long_put is not None else sp0 - strike_step
        width = sp0 - lp0
        for ds in _STRIKE_SHIFTS:
            if ds == 0:
                continue
            sp = sp0 + ds * strike_step
            lp = sp - width
            if lp >= sp:
                continue
            out.append(CandidateStrikes(
                short_put=sp, long_put=lp, short_call=None, long_call=None,
                structure=STRUCTURE_PUT_VERT,
            ))
    elif baseline.structure == STRUCTURE_CALL_VERT and baseline.short_call is not None:
        sc0 = float(baseline.short_call)
        lc0 = float(baseline.long_call) if baseline.long_call is not None else sc0 + strike_step
        width = lc0 - sc0
        for ds in _STRIKE_SHIFTS:
            if ds == 0:
                continue
            sc = sc0 + ds * strike_step
            lc = sc + width
            if sc >= lc:
                continue
            out.append(CandidateStrikes(
                short_put=None, long_put=None, short_call=sc, long_call=lc,
                structure=STRUCTURE_CALL_VERT,
            ))
    return out


# ---------------------------------------------------------------------------
# Scoring (tier 1 — fast)
# ---------------------------------------------------------------------------


def _credit_at_entry(
    *,
    chain: List[ChainRow],
    candidate: CandidateStrikes,
    snap_max_pts: float,
    fill_model: Optional[FillModel] = None,
) -> Optional[float]:
    """Return the per-contract net credit that *opening* this IC at the
    entry date would have collected, using mids from ``chain``.

    For a short IC: credit = (short_put_mid + short_call_mid) - (long_put_mid + long_call_mid)
    For a put vertical: credit = short_put_mid - long_put_mid
    For a call vertical: credit = short_call_mid - long_call_mid

    Returns None when any required leg can't be found within the snap
    tolerance.
    """
    if not chain:
        return None
    fm = fill_model or FillModel(mode="mid")

    def _find(strike: float, want: str) -> Optional[float]:
        """Find the row nearest ``strike`` within ``snap_max_pts`` and
        return its put/call mid. ``want`` in {"put", "call"}."""
        best_row: Optional[ChainRow] = None
        best_dist = math.inf
        for r in chain:
            d = abs(float(r.strike) - float(strike))
            if d < best_dist:
                best_dist = d
                best_row = r
        if best_row is None or best_dist > snap_max_pts + 1e-9:
            return None
        if want == "put":
            return best_row.put_mid_px()
        return best_row.call_mid_px()

    short_put_mid = (
        _find(candidate.short_put, "put") if candidate.short_put is not None else 0.0
    )
    long_put_mid = (
        _find(candidate.long_put, "put") if candidate.long_put is not None else 0.0
    )
    short_call_mid = (
        _find(candidate.short_call, "call") if candidate.short_call is not None else 0.0
    )
    long_call_mid = (
        _find(candidate.long_call, "call") if candidate.long_call is not None else 0.0
    )

    if any(v is None for v in (short_put_mid, long_put_mid, short_call_mid, long_call_mid)):
        return None

    credit = (
        float(short_put_mid) + float(short_call_mid)
        - float(long_put_mid) - float(long_call_mid)
    )
    # Reject pathological pricing (negative or absurdly large credits).
    if credit <= 0.0 or not math.isfinite(credit):
        return None
    # Apply the fill penalty if not pure-mid. We approximate the round-trip
    # half-spread cost the same way reprice_ic does for closes.
    if fm.mode == "nbbo":
        credit *= 1.0 - 0.5 * fm.penalty_pct / 100.0
    return credit


def _max_loss(candidate: CandidateStrikes, credit: float) -> float:
    """Per-contract max loss = max wing width - credit (both in points)."""
    return max(0.0, float(candidate.max_wing_width) - float(credit))


def _breach_distance_pct(strike: Optional[float], spot: float, side: str) -> Optional[float]:
    """Distance from spot to short strike as a fraction of spot, signed
    so that breaches happen when realized move EXCEEDS the magnitude.

    Returns absolute distance: short put is breached when realized return
    < -distance, short call when realized return > +distance.
    """
    if strike is None or spot <= 0:
        return None
    if side == "put":
        return max(0.0, (float(spot) - float(strike)) / float(spot))
    if side == "call":
        return max(0.0, (float(strike) - float(spot)) / float(spot))
    return None


def _estimate_breach_rate(
    *,
    candidate: CandidateStrikes,
    matched_events: Sequence[Dict[str, Any]],
    user_spot: float,
) -> Tuple[float, Tuple[float, float], int]:
    """Tier-1 estimator: for each matched event, replay the realized
    earnings move against the candidate's short strikes (converted to
    pct-of-spot at the current spot). A breach occurs whenever the
    candidate's short put / short call would be touched by the event's
    *realized move*.

    Returns (p_breach, (ci_lo, ci_hi), n_events).

    The CI is Wilson-style on a binomial proportion at 95%.
    """
    if not matched_events or user_spot <= 0:
        return (0.0, (0.0, 0.0), 0)

    put_dist_pct = _breach_distance_pct(candidate.short_put, user_spot, "put")
    call_dist_pct = _breach_distance_pct(candidate.short_call, user_spot, "call")

    breach_count = 0
    n = 0
    for ev in matched_events:
        realized = ev.get("realizedMovePct")
        if realized is None:
            continue
        try:
            r_pct = float(realized) / 100.0  # field is percentage points
        except (TypeError, ValueError):
            continue
        if not math.isfinite(r_pct):
            continue
        n += 1
        breached = False
        if put_dist_pct is not None and r_pct < -put_dist_pct + 1e-9:
            breached = True
        if not breached and call_dist_pct is not None and r_pct > call_dist_pct - 1e-9:
            breached = True
        if breached:
            breach_count += 1

    if n == 0:
        return (0.0, (0.0, 0.0), 0)
    p = breach_count / n
    ci = _wilson_ci(breach_count, n, z=1.96)
    return (p, ci, n)


def _wilson_ci(successes: int, n: int, *, z: float = 1.96) -> Tuple[float, float]:
    """Wilson 95% CI for a binomial proportion."""
    if n <= 0:
        return (0.0, 0.0)
    phat = successes / n
    denom = 1.0 + z * z / n
    centre = (phat + z * z / (2 * n)) / denom
    half = (z * math.sqrt((phat * (1 - phat) + z * z / (4 * n)) / n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def score_candidate(
    candidate: CandidateStrikes,
    *,
    entry_chain: List[ChainRow],
    matched_events: Sequence[Dict[str, Any]],
    user_spot: float,
    snap_max_pts: float,
    fill_model: Optional[FillModel] = None,
    baseline: Optional["ScoredCandidate"] = None,
) -> Optional[ScoredCandidate]:
    """Score a single candidate. Returns None when the chain can't price it.

    ``baseline`` is optional; when supplied the delta_* fields are
    populated. The scanner uses it for non-baseline candidates and leaves
    it None when scoring the baseline itself.
    """
    credit = _credit_at_entry(
        chain=entry_chain,
        candidate=candidate,
        snap_max_pts=snap_max_pts,
        fill_model=fill_model,
    )
    if credit is None:
        return None
    max_loss = _max_loss(candidate, credit)
    p_breach, ci, _n = _estimate_breach_rate(
        candidate=candidate, matched_events=matched_events, user_spot=user_spot,
    )
    # Naive EV: average outcome weighted by breach proxy. Real outcome
    # distribution adds nuance (early-target collects partial credit) — we
    # approximate "non-breach => keep credit, breach => lose max_loss".
    ev = (1.0 - p_breach) * credit - p_breach * max_loss

    if baseline is None:
        delta_breach_pct = delta_credit_pct = delta_max_loss_pct = delta_ev_pct = 0.0
    else:
        delta_breach_pct = _pct_delta(p_breach, baseline.p_breach)
        delta_credit_pct = _pct_delta(credit, baseline.credit)
        delta_max_loss_pct = _pct_delta(max_loss, baseline.max_loss)
        delta_ev_pct = _pct_delta(ev, baseline.ev)

    return ScoredCandidate(
        strikes=candidate,
        credit=round(credit, 4),
        max_loss=round(max_loss, 4),
        p_breach=round(p_breach, 4),
        p_breach_interval=(round(ci[0], 4), round(ci[1], 4)),
        ev=round(ev, 4),
        delta_breach_pct=round(delta_breach_pct, 2),
        delta_credit_pct=round(delta_credit_pct, 2),
        delta_max_loss_pct=round(delta_max_loss_pct, 2),
        delta_ev_pct=round(delta_ev_pct, 2),
    )


def _pct_delta(new: float, base: float) -> float:
    if base is None or abs(base) < 1e-9:
        return 0.0
    return (new - base) / abs(base) * 100.0


# ---------------------------------------------------------------------------
# Verdict ranking
# ---------------------------------------------------------------------------

# Thresholds used by ``rank_and_verdict``. Public so callers can override.
DOMINATING_MIN_EV_DELTA_PCT = 5.0           # require >=5% better EV
DOMINATING_REQUIRE_CREDIT_NON_NEGATIVE = -2.0  # allow tiny credit dip
SAFER_MIN_BREACH_REDUCTION_PCT = 20.0       # >=20% relative breach reduction
SAFER_MIN_CREDIT_RETENTION_PCT = 90.0       # keep at least 90% of credit
RICHER_MIN_CREDIT_GAIN_PCT = 15.0           # >=15% relative credit gain
RICHER_MAX_BREACH_INCREASE_PCT = 10.0       # at most +10% relative breach


def rank_and_verdict(
    *,
    baseline: ScoredCandidate,
    scored: Sequence[ScoredCandidate],
) -> Dict[str, Any]:
    """Pick the best alternative under each rubric and emit a verdict.

    Returns a dict with ``verdict``, ``headline``, ``top_alternatives``
    (up to 3 distinct picks), and ``all_candidates`` (sorted by EV desc).
    """
    non_baseline = [c for c in scored if not _strikes_equal(c.strikes, baseline.strikes)]

    # Bucket 1 — dominating: strictly better EV AND credit not materially worse.
    dominating = [
        c for c in non_baseline
        if c.delta_ev_pct >= DOMINATING_MIN_EV_DELTA_PCT
        and c.delta_credit_pct >= DOMINATING_REQUIRE_CREDIT_NON_NEGATIVE
        and c.delta_breach_pct <= 0.0
    ]
    dominating.sort(key=lambda c: c.delta_ev_pct, reverse=True)

    # Bucket 2 — safer at near-equal credit.
    safer = [
        c for c in non_baseline
        if c.delta_breach_pct <= -SAFER_MIN_BREACH_REDUCTION_PCT
        and c.delta_credit_pct >= -(100.0 - SAFER_MIN_CREDIT_RETENTION_PCT)
    ]
    safer.sort(key=lambda c: c.delta_breach_pct)

    # Bucket 3 — richer at near-equal breach.
    richer = [
        c for c in non_baseline
        if c.delta_credit_pct >= RICHER_MIN_CREDIT_GAIN_PCT
        and c.delta_breach_pct <= RICHER_MAX_BREACH_INCREASE_PCT
    ]
    richer.sort(key=lambda c: c.delta_credit_pct, reverse=True)

    top: List[ScoredCandidate] = []
    rationales: List[str] = []

    if dominating:
        c = dominating[0]
        c.rationale = _rationale_dominating(c)
        top.append(c)
        rationales.append("dominating")
    if safer:
        c = safer[0]
        if not any(_strikes_equal(c.strikes, t.strikes) for t in top):
            c.rationale = _rationale_safer(c)
            top.append(c)
            rationales.append("safer")
    if richer:
        c = richer[0]
        if not any(_strikes_equal(c.strikes, t.strikes) for t in top):
            c.rationale = _rationale_richer(c)
            top.append(c)
            rationales.append("richer")

    # Decide the overall verdict in the priority order.
    if "dominating" in rationales:
        verdict = "dominating"
    elif "safer" in rationales:
        verdict = "safer_alternative"
    elif "richer" in rationales:
        verdict = "richer_alternative"
    else:
        verdict = "optimal"

    headline = _headline(verdict, top, baseline, scanned_n=len(scored))

    # Sort everything by EV desc for the disclosure table.
    all_sorted = sorted(non_baseline, key=lambda c: c.ev, reverse=True)

    return {
        "verdict":          verdict,
        "headline":         headline,
        "scanned_n":        len(scored),
        "baseline":         baseline.to_dict(),
        "top_alternatives": [c.to_dict() for c in top[:3]],
        "all_candidates":   [c.to_dict() for c in all_sorted],
    }


def _strikes_equal(a: CandidateStrikes, b: CandidateStrikes) -> bool:
    return (
        a.short_put == b.short_put
        and a.long_put == b.long_put
        and a.short_call == b.short_call
        and a.long_call == b.long_call
        and a.structure == b.structure
    )


# ---------------------------------------------------------------------------
# Rationales + headlines
# ---------------------------------------------------------------------------


def _rationale_dominating(c: ScoredCandidate) -> str:
    return (
        f"{c.strikes.short_strikes_summary()}: "
        f"{c.delta_credit_pct:+.1f}% credit, {c.delta_breach_pct:+.1f}% breach risk, "
        f"{c.delta_ev_pct:+.1f}% expected value vs baseline."
    )


def _rationale_safer(c: ScoredCandidate) -> str:
    credit_retention = 100.0 + c.delta_credit_pct
    return (
        f"{c.strikes.short_strikes_summary()}: keeps "
        f"{credit_retention:.0f}% of baseline credit while cutting breach risk by "
        f"{abs(c.delta_breach_pct):.1f}%."
    )


def _rationale_richer(c: ScoredCandidate) -> str:
    return (
        f"{c.strikes.short_strikes_summary()}: collects "
        f"{c.delta_credit_pct:+.1f}% more credit at "
        f"{c.delta_breach_pct:+.1f}% breach risk vs baseline."
    )


def _headline(
    verdict: str,
    top: Sequence[ScoredCandidate],
    baseline: ScoredCandidate,
    scanned_n: int,
) -> str:
    if verdict == "dominating" and top:
        c = top[0]
        return (
            f"Better setup: {c.strikes.short_strikes_summary()} for "
            f"{c.delta_credit_pct:+.1f}% credit and "
            f"{c.delta_breach_pct:+.1f}% breach risk. "
            f"{_structure_label(c.strikes.structure)}."
        )
    if verdict == "safer_alternative" and top:
        c = top[0]
        credit_retention = 100.0 + c.delta_credit_pct
        return (
            f"Equivalent-but-safer: {c.strikes.short_strikes_summary()} captures "
            f"{credit_retention:.0f}% of current credit with "
            f"{abs(c.delta_breach_pct):.0f}% less breach risk."
        )
    if verdict == "richer_alternative" and top:
        c = top[0]
        return (
            f"Richer at near-equal risk: {c.strikes.short_strikes_summary()} collects "
            f"{c.delta_credit_pct:+.0f}% more credit; breach within "
            f"{c.delta_breach_pct:+.0f}% of baseline."
        )
    return (
        f"This is as good as it gets. Scanned {scanned_n} alternative configurations "
        "across strikes + structure — none Pareto-dominates the current setup."
    )


def _structure_label(structure: str) -> str:
    return {
        STRUCTURE_IC:        "Same structure (IC)",
        STRUCTURE_FLY:       "Iron-fly variant",
        STRUCTURE_ASYM:      "Asymmetric IC",
        STRUCTURE_PUT_VERT:  "Put vertical",
        STRUCTURE_CALL_VERT: "Call vertical",
    }.get(structure, "Alternative structure")


# ---------------------------------------------------------------------------
# Top-level entrypoint
# ---------------------------------------------------------------------------


def run_strike_scan(
    *,
    baseline_strikes: CandidateStrikes,
    baseline_credit: float,
    entry_chain: List[ChainRow],
    matched_events: Sequence[Dict[str, Any]],
    user_spot: float,
    snap_max_pts: float = 5.0,
    fill_model: Optional[FillModel] = None,
) -> Dict[str, Any]:
    """End-to-end tier-1 scan.

    Returns the verdict dict shaped for the router response. ``baseline_credit``
    is the desk's actual collected credit — we use it for the baseline scored
    record so the deltas read against what the desk actually paid, not the
    chain-mid the scanner would re-derive.
    """
    step = infer_strike_step([
        s for s in (
            baseline_strikes.short_put, baseline_strikes.long_put,
            baseline_strikes.short_call, baseline_strikes.long_call,
        ) if s is not None
    ])

    # Score baseline at the desk's actual credit (not re-derived).
    baseline_max_loss = _max_loss(baseline_strikes, baseline_credit)
    p_breach_base, ci_base, _n_base = _estimate_breach_rate(
        candidate=baseline_strikes, matched_events=matched_events, user_spot=user_spot,
    )
    ev_base = (1.0 - p_breach_base) * baseline_credit - p_breach_base * baseline_max_loss
    baseline_scored = ScoredCandidate(
        strikes=baseline_strikes,
        credit=round(baseline_credit, 4),
        max_loss=round(baseline_max_loss, 4),
        p_breach=round(p_breach_base, 4),
        p_breach_interval=(round(ci_base[0], 4), round(ci_base[1], 4)),
        ev=round(ev_base, 4),
        delta_breach_pct=0.0, delta_credit_pct=0.0,
        delta_max_loss_pct=0.0, delta_ev_pct=0.0,
        is_baseline=True,
    )

    # Generate + score candidates.
    candidates = generate_candidates(
        baseline_strikes=baseline_strikes, strike_step=step,
    )
    scored: List[ScoredCandidate] = []
    n_priced = 0
    n_unpriced = 0
    for c in candidates:
        s = score_candidate(
            c,
            entry_chain=entry_chain,
            matched_events=matched_events,
            user_spot=user_spot,
            snap_max_pts=snap_max_pts,
            fill_model=fill_model,
            baseline=baseline_scored,
        )
        if s is None:
            n_unpriced += 1
            continue
        scored.append(s)
        n_priced += 1

    out = rank_and_verdict(baseline=baseline_scored, scored=scored)
    out["strike_step"] = step
    out["scan_meta"] = {
        "n_generated":   len(candidates),
        "n_priced":      n_priced,
        "n_unpriced":    n_unpriced,
        "user_spot":     round(float(user_spot), 4),
        "snap_max_pts":  float(snap_max_pts),
    }
    return out
