"""Theta-capture estimator for Engine 1 Wing Console.

For an earnings iron condor, the primary P&L driver is the collapse of
pre-event implied vol (the "IV crush") PROVIDED the underlying stays
inside the short strikes. This module estimates, at each candidate
(EM-multiple, wing-width) placement, the expected fraction of entry
credit the desk should capture if it holds through the event and exits
post-earn.

Math (deterministic, no LLM involvement):

1. **Survival rate** at placement ``(em_multiple)``:
      survival = (# events with |signed_move_pct| <= em_multiple * EM) / N

2. **Decay richness** — how over-priced was the pre-event premium
   historically?

      richness = 1.0 - mean(|signed_move_pct| / EM)

   Clamped to ``[0.10, 0.95]``. A richness of 0.70 means "on average the
   market priced ~70% more move than actually occurred — the remainder
   was the IC seller's edge".

3. **Expected credit-kept fraction** at a placement:

      kept = survival * (0.3 + 0.65 * richness)

   The ``0.3`` baseline accounts for pre-earnings theta that decays
   regardless of the event; the ``0.65 * richness`` premium is the
   IV-crush kicker.

4. **Expected decay capture** is expressed as **% of entry credit**
   that a patient desk would keep, assuming standard same-day-after
   close-out.

Outputs feed the wing-console composite score and the LLM advisor
narrative.
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional

_BASELINE_THETA = 0.30        # credit decayed even on a "bad" event
_RICHNESS_KICKER = 0.65       # extra capture driven by IV crush


@dataclass
class ThetaCaptureReading:
    """Aggregate theta / IV-crush readings from the event pool."""

    n_events:         int = 0
    mean_move_ratio:  float = 0.0       # mean(|signed_move| / EM)
    median_move_ratio: float = 0.0
    decay_richness:   float = 0.0       # clamp(1 - mean_ratio, 0.10, 0.95)
    baseline_theta:   float = _BASELINE_THETA
    richness_kicker:  float = _RICHNESS_KICKER
    notes:            List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    def survival_rate(self, em_multiple: float) -> float:
        """Placeholder — real survival is computed in :func:`expected_decay_capture`
        against the event pool. Exposed here so callers can also query."""
        return 0.0  # defined at placement-time


# ---------------------------------------------------------------------------
# Event-pool ingestion
# ---------------------------------------------------------------------------


def _normalized_move_ratio(event: Dict[str, Any]) -> Optional[float]:
    """Pull ``|signed_move| / EM`` from an already-computed event row."""
    try:
        signed = event.get("signedMovePct")
        em = event.get("impliedMovePct") or event.get("impliedMoveImpPct")
        if signed is None or em is None:
            # `ratio` (realized / implied) is also stored pre-computed sometimes:
            r = event.get("ratioRealizedImplied") or event.get("ratio")
            if r is None:
                return None
            return abs(float(r))
        em_f = float(em)
        if em_f <= 0:
            return None
        return abs(float(signed)) / em_f
    except (TypeError, ValueError):
        return None


def estimate_theta_capture(events: Iterable[Dict[str, Any]]) -> ThetaCaptureReading:
    """Build a :class:`ThetaCaptureReading` from the breach-stats per-event list.

    ``events`` is expected to be the list stored at ``payload["events"]`` —
    each row carries ``signedMovePct`` + ``impliedMovePct``.
    """
    ratios: List[float] = []
    notes: List[str] = []
    for ev in events or []:
        r = _normalized_move_ratio(ev)
        if r is not None and math.isfinite(r):
            ratios.append(r)

    n = len(ratios)
    if n == 0:
        return ThetaCaptureReading(
            n_events=0,
            notes=["theta_capture: no event ratios available"],
        )

    mean_r = sum(ratios) / n
    sorted_r = sorted(ratios)
    median_r = (
        sorted_r[n // 2] if n % 2 == 1
        else 0.5 * (sorted_r[n // 2 - 1] + sorted_r[n // 2])
    )

    # Richness = "% of premium that was over-priced on average".
    # mean_r > 1 means realized > implied → market under-priced → low richness.
    richness = max(0.10, min(0.95, 1.0 - mean_r))

    if mean_r >= 1.1:
        notes.append(
            f"mean_realized/implied={mean_r:.2f} — market historically "
            "under-priced: expect thinner theta."
        )
    elif mean_r <= 0.6:
        notes.append(
            f"mean_realized/implied={mean_r:.2f} — market historically "
            "over-priced: rich theta-capture regime."
        )

    return ThetaCaptureReading(
        n_events=n,
        mean_move_ratio=round(mean_r, 4),
        median_move_ratio=round(median_r, 4),
        decay_richness=round(richness, 4),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# Placement-time pricing
# ---------------------------------------------------------------------------


def expected_decay_capture(
    *,
    reading:   ThetaCaptureReading,
    events:    Iterable[Dict[str, Any]],
    em_multiple: float,
) -> Dict[str, float]:
    """Compute the expected decay capture (% of credit) at ``em_multiple``.

    Returns a dict with ``survival_rate``, ``richness``, ``capture_pct``.
    """
    survival = _survival_rate(events, em_multiple=em_multiple)

    capture = survival * (_BASELINE_THETA + _RICHNESS_KICKER * reading.decay_richness)
    # Clamp to [0, 1] for safety (rounding edge cases).
    capture = max(0.0, min(1.0, capture))

    return {
        "survival_rate": round(survival, 4),
        "richness":      reading.decay_richness,
        "capture_pct":   round(capture * 100.0, 2),
    }


def _survival_rate(events: Iterable[Dict[str, Any]], *, em_multiple: float) -> float:
    """Fraction of events where ``|signed_move_pct| <= em_multiple * EM``."""
    if em_multiple <= 0:
        return 0.0
    n = 0
    survivors = 0
    for ev in events or []:
        r = _normalized_move_ratio(ev)
        if r is None or not math.isfinite(r):
            continue
        n += 1
        if r <= em_multiple:
            survivors += 1
    if n == 0:
        return 0.0
    return survivors / n
