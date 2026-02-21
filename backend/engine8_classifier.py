"""Engine 8 – Displacement Classification.

Categorises the post-event move along three deterministic axes:

  1. Magnitude  – move relative to EM and ATR
  2. Structure  – gap-and-hold / gap-and-fade / gap-and-consolidation
  3. Context    – alignment with prior trend and broad market

All inputs are deterministic (ORATS + EODHD).  No LLM or Benzinga data
is used for classification.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any, Optional

from backend.config import FeatureFlags, get_flags


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class DisplacementProfile:
    # Magnitude
    move_vs_em: Optional[float] = None
    magnitude_em_label: str = "unknown"       # under | at | over | extreme
    atr_multiple: Optional[float] = None
    magnitude_atr_label: str = "unknown"      # normal | elevated | extreme

    # Structure
    gap_structure: Optional[str] = None       # HOLD | FADE | STALL (from snapshot)
    structure_label: str = "unknown"          # GAP_AND_HOLD | GAP_AND_FADE | GAP_AND_CONSOLIDATION

    # Context
    direction: Optional[str] = None           # UP | DOWN
    trend_5d_aligned: Optional[bool] = None
    trend_20d_aligned: Optional[bool] = None
    market_aligned: Optional[bool] = None
    context_label: str = "unknown"            # ALIGNED | NEUTRAL | OPPOSED

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Magnitude classification
# ---------------------------------------------------------------------------

def _classify_magnitude_em(
    move_vs_em: Optional[float],
    flags: FeatureFlags,
) -> str:
    if move_vs_em is None:
        return "unknown"
    if move_vs_em >= flags.ENGINE8_EM_RATIO_EXTREME:
        return "extreme"
    if move_vs_em >= flags.ENGINE8_EM_RATIO_OVER:
        return "over"
    if move_vs_em >= 0.8:
        return "at"
    return "under"


def _classify_magnitude_atr(
    atr_multiple: Optional[float],
    flags: FeatureFlags,
) -> str:
    if atr_multiple is None:
        return "unknown"
    if atr_multiple >= flags.ENGINE8_ATR_EXTREME:
        return "extreme"
    if atr_multiple >= flags.ENGINE8_ATR_ELEVATED:
        return "elevated"
    return "normal"


# ---------------------------------------------------------------------------
# Structure classification
# ---------------------------------------------------------------------------

_STRUCTURE_MAP = {
    "HOLD": "GAP_AND_HOLD",
    "FADE": "GAP_AND_FADE",
    "STALL": "GAP_AND_CONSOLIDATION",
}


def _classify_structure(gap_structure: Optional[str]) -> str:
    if gap_structure is None:
        return "unknown"
    return _STRUCTURE_MAP.get(gap_structure.upper(), "unknown")


# ---------------------------------------------------------------------------
# Context classification
# ---------------------------------------------------------------------------

def _sign(v: Optional[float]) -> Optional[int]:
    if v is None:
        return None
    if v > 0:
        return 1
    if v < 0:
        return -1
    return 0


def _classify_context(
    direction: Optional[str],
    trend_5d_return: Optional[float],
    trend_20d_return: Optional[float],
    spy_5d_return: Optional[float],
) -> tuple[str, Optional[bool], Optional[bool], Optional[bool]]:
    """Return (context_label, trend_5d_aligned, trend_20d_aligned, market_aligned)."""
    if direction is None:
        return "unknown", None, None, None

    gap_sign = 1 if direction == "UP" else -1

    t5_aligned = _sign(trend_5d_return) == gap_sign if trend_5d_return is not None else None
    t20_aligned = _sign(trend_20d_return) == gap_sign if trend_20d_return is not None else None
    mkt_aligned = _sign(spy_5d_return) == gap_sign if spy_5d_return is not None else None

    votes = [v for v in (t5_aligned, t20_aligned, mkt_aligned) if v is not None]
    if not votes:
        return "unknown", t5_aligned, t20_aligned, mkt_aligned

    aligned_count = sum(1 for v in votes if v)
    if aligned_count >= 2:
        label = "ALIGNED"
    elif aligned_count == 0:
        label = "OPPOSED"
    else:
        label = "NEUTRAL"

    return label, t5_aligned, t20_aligned, mkt_aligned


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def classify_displacement(
    *,
    move_vs_em: Optional[float] = None,
    atr_multiple: Optional[float] = None,
    gap_structure: Optional[str] = None,
    direction: Optional[str] = None,
    trend_5d_return: Optional[float] = None,
    trend_20d_return: Optional[float] = None,
    spy_5d_return: Optional[float] = None,
    flags: Optional[FeatureFlags] = None,
) -> DisplacementProfile:
    """Classify the post-event displacement into magnitude, structure, context."""
    if flags is None:
        flags = get_flags()

    mag_em = _classify_magnitude_em(move_vs_em, flags)
    mag_atr = _classify_magnitude_atr(atr_multiple, flags)
    struct = _classify_structure(gap_structure)
    ctx_label, t5, t20, mkt = _classify_context(direction, trend_5d_return, trend_20d_return, spy_5d_return)

    return DisplacementProfile(
        move_vs_em=move_vs_em,
        magnitude_em_label=mag_em,
        atr_multiple=atr_multiple,
        magnitude_atr_label=mag_atr,
        gap_structure=gap_structure,
        structure_label=struct,
        direction=direction,
        trend_5d_aligned=t5,
        trend_20d_aligned=t20,
        market_aligned=mkt,
        context_label=ctx_label,
    )
