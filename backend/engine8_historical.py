"""Engine 8 – Historical Pattern Layer.

Backtests historical earnings for the same ticker under matching
displacement conditions and computes forward-return probabilities at
1-, 2-, 3-, and 5-day horizons.

Critical constraints
--------------------
* Similarity matching uses ONLY deterministic fields: magnitude bucket,
  structure label, and direction.  Sentiment is excluded.
* If ``sample_size < ENGINE8_MIN_HISTORICAL_SAMPLE`` (default 15), the
  layer sets ``force_pass = True`` and no probabilities are computed.
  The decision module respects this unconditionally.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.config import FeatureFlags, get_flags
from backend.engine8_classifier import classify_displacement
from backend.engine8_snapshot import _to_float, _compute_atr, _classify_gap_structure

LOG = logging.getLogger(__name__)

_HORIZONS = (1, 2, 3, 5)


# ---------------------------------------------------------------------------
# Data class
# ---------------------------------------------------------------------------

@dataclass
class HistoricalPatternResult:
    force_pass: bool = False
    sample_size: int = 0
    confidence_band: str = "LOW"         # HIGH (>=20) | MEDIUM (>=15) | LOW (<15)

    continuation_prob_1d: Optional[float] = None
    continuation_prob_2d: Optional[float] = None
    continuation_prob_3d: Optional[float] = None
    continuation_prob_5d: Optional[float] = None

    reversion_prob_1d: Optional[float] = None
    reversion_prob_2d: Optional[float] = None
    reversion_prob_3d: Optional[float] = None
    reversion_prob_5d: Optional[float] = None

    avg_continuation_magnitude: Optional[float] = None
    avg_reversion_magnitude: Optional[float] = None

    matched_events: List[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def _confidence_band(n: int) -> str:
    if n >= 20:
        return "HIGH"
    if n >= 15:
        return "MEDIUM"
    return "LOW"


def _magnitude_bucket(move_vs_em: Optional[float], flags: FeatureFlags) -> str:
    if move_vs_em is None:
        return "unknown"
    if move_vs_em >= flags.ENGINE8_EM_RATIO_EXTREME:
        return "extreme"
    if move_vs_em >= flags.ENGINE8_EM_RATIO_OVER:
        return "over"
    if move_vs_em >= 0.8:
        return "at"
    return "under"


_GAP_MAP = {"HOLD": "GAP_AND_HOLD", "FADE": "GAP_AND_FADE", "STALL": "GAP_AND_CONSOLIDATION"}


def _structure_bucket(gap_label: Optional[str]) -> str:
    if gap_label is None:
        return "unknown"
    return _GAP_MAP.get(gap_label.upper(), "unknown")


# ---------------------------------------------------------------------------
# Build historical event rows
# ---------------------------------------------------------------------------

def _build_event_row(
    *,
    earnings_date: dt.date,
    pre_close: float,
    post_bar: dict,
    expected_move_pct: Optional[float],
    bars_for_atr: List[dict],
    forward_bars: List[dict],
    flags: FeatureFlags,
) -> Optional[dict]:
    """Compute snapshot-equivalent fields for one historical event."""
    post_close = _to_float(post_bar.get("close") or post_bar.get("adjusted_close"))
    post_open = _to_float(post_bar.get("open"))
    post_high = _to_float(post_bar.get("high"))
    post_low = _to_float(post_bar.get("low"))

    if post_close is None or pre_close <= 0:
        return None

    actual_move_pct = ((post_close - pre_close) / pre_close) * 100.0
    direction = "UP" if actual_move_pct > 0 else "DOWN"

    move_vs_em: Optional[float] = None
    if expected_move_pct and expected_move_pct > 0:
        move_vs_em = abs(actual_move_pct) / expected_move_pct

    atr = _compute_atr(bars_for_atr, period=14)
    atr_multiple: Optional[float] = None
    if atr and atr > 0:
        atr_multiple = abs(post_close - pre_close) / atr

    gap_structure: Optional[str] = None
    if post_open is not None and post_high is not None and post_low is not None:
        gap_structure = _classify_gap_structure(post_open, post_high, post_low, post_close, pre_close, atr)

    mag_bucket = _magnitude_bucket(move_vs_em, flags)
    struct_bucket = _structure_bucket(gap_structure)

    # Relative volume: event-day volume / 20-day average volume
    rel_volume: Optional[float] = None
    post_vol = _to_float(post_bar.get("volume"))
    if post_vol is not None and post_vol > 0 and bars_for_atr:
        avg_vols = [_to_float(b.get("volume")) for b in bars_for_atr[-20:]]
        avg_vols = [v for v in avg_vols if v is not None and v > 0]
        if avg_vols:
            avg_vol = sum(avg_vols) / len(avg_vols)
            if avg_vol > 0:
                rel_volume = post_vol / avg_vol

    # Forward returns
    forward_returns: Dict[int, Optional[float]] = {}
    sorted_fwd = sorted(forward_bars, key=lambda b: str(b.get("date", "")))
    for h in _HORIZONS:
        if h - 1 < len(sorted_fwd):
            fc = _to_float(sorted_fwd[h - 1].get("close") or sorted_fwd[h - 1].get("adjusted_close"))
            if fc is not None and post_close > 0:
                forward_returns[h] = ((fc - post_close) / post_close) * 100.0

    return {
        "earnings_date": _fmt_date(earnings_date),
        "actual_move_pct": round(actual_move_pct, 4),
        "move_vs_em": round(move_vs_em, 4) if move_vs_em is not None else None,
        "atr_multiple": round(atr_multiple, 4) if atr_multiple is not None else None,
        "direction": direction,
        "gap_structure": gap_structure,
        "magnitude_bucket": mag_bucket,
        "structure_bucket": struct_bucket,
        "rel_volume": round(rel_volume, 2) if rel_volume is not None else None,
        "forward_returns": {str(k): round(v, 4) for k, v in forward_returns.items() if v is not None},
    }


# ---------------------------------------------------------------------------
# Matching
# ---------------------------------------------------------------------------

def _matches(event: dict, current_mag: str, current_struct: str, current_dir: str) -> bool:
    """Strict similarity matching on all 3 deterministic fields."""
    if event.get("magnitude_bucket") != current_mag:
        return False
    if event.get("structure_bucket") != current_struct:
        return False
    if event.get("direction") != current_dir:
        return False
    return True


def _matches_relaxed(event: dict, current_mag: str, current_dir: str) -> bool:
    """Relaxed matching — magnitude + direction only (structure dropped)."""
    if event.get("magnitude_bucket") != current_mag:
        return False
    if event.get("direction") != current_dir:
        return False
    return True


# ---------------------------------------------------------------------------
# Probability computation
# ---------------------------------------------------------------------------

def _compute_probs(
    events: List[dict],
    direction: str,
) -> dict:
    """Compute continuation / reversion probabilities and magnitudes."""
    result: Dict[str, Optional[float]] = {}
    cont_mags: list[float] = []
    rev_mags: list[float] = []

    is_up = direction == "UP"

    for h in _HORIZONS:
        cont_count = 0
        rev_count = 0
        total = 0
        for ev in events:
            fwd = ev.get("forward_returns", {}).get(str(h))
            if fwd is None:
                continue
            total += 1
            if (is_up and fwd > 0) or (not is_up and fwd < 0):
                cont_count += 1
                cont_mags.append(abs(fwd))
            else:
                rev_count += 1
                rev_mags.append(abs(fwd))

        if total > 0:
            result[f"continuation_prob_{h}d"] = round(cont_count / total, 4)
            result[f"reversion_prob_{h}d"] = round(rev_count / total, 4)
        else:
            result[f"continuation_prob_{h}d"] = None
            result[f"reversion_prob_{h}d"] = None

    result["avg_continuation_magnitude"] = round(sum(cont_mags) / len(cont_mags), 4) if cont_mags else None
    result["avg_reversion_magnitude"] = round(sum(rev_mags) / len(rev_mags), 4) if rev_mags else None

    return result


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def compute_historical_patterns(
    *,
    ticker: str,
    current_magnitude_bucket: str,
    current_structure_bucket: str,
    current_direction: str,
    all_event_rows: List[dict],
    flags: Optional[FeatureFlags] = None,
) -> HistoricalPatternResult:
    """Run the historical pattern layer.

    ``all_event_rows`` should be pre-built rows from
    ``build_historical_events()`` (see engine8_pipeline).

    Matching strategy:
      1. Try strict match (magnitude + structure + direction).
      2. If strict match yields < ENGINE8_MIN_HISTORICAL_SAMPLE events,
         fall back to relaxed match (magnitude + direction only) with
         confidence_band capped at "LOW".
      3. If relaxed match also < ENGINE8_MIN_HISTORICAL_SAMPLE, force_pass.
    """
    if flags is None:
        flags = get_flags()

    matched = [
        ev for ev in all_event_rows
        if _matches(ev, current_magnitude_bucket, current_structure_bucket, current_direction)
    ]
    n = len(matched)
    relaxed = False

    if n < flags.ENGINE8_MIN_HISTORICAL_SAMPLE:
        matched_relaxed = [
            ev for ev in all_event_rows
            if _matches_relaxed(ev, current_magnitude_bucket, current_direction)
        ]
        if len(matched_relaxed) >= flags.ENGINE8_MIN_HISTORICAL_SAMPLE:
            matched = matched_relaxed
            n = len(matched)
            relaxed = True
            LOG.info(
                "Engine 8 historical: strict match yielded %d events, relaxed to %d for %s",
                len([ev for ev in all_event_rows if _matches(ev, current_magnitude_bucket, current_structure_bucket, current_direction)]),
                n, ticker,
            )

    band = "LOW" if relaxed else _confidence_band(n)

    if n < flags.ENGINE8_MIN_HISTORICAL_SAMPLE:
        return HistoricalPatternResult(
            force_pass=True,
            sample_size=n,
            confidence_band=band,
            matched_events=matched,
        )

    probs = _compute_probs(matched, current_direction)

    return HistoricalPatternResult(
        force_pass=False,
        sample_size=n,
        confidence_band=band,
        continuation_prob_1d=probs.get("continuation_prob_1d"),
        continuation_prob_2d=probs.get("continuation_prob_2d"),
        continuation_prob_3d=probs.get("continuation_prob_3d"),
        continuation_prob_5d=probs.get("continuation_prob_5d"),
        reversion_prob_1d=probs.get("reversion_prob_1d"),
        reversion_prob_2d=probs.get("reversion_prob_2d"),
        reversion_prob_3d=probs.get("reversion_prob_3d"),
        reversion_prob_5d=probs.get("reversion_prob_5d"),
        avg_continuation_magnitude=probs.get("avg_continuation_magnitude"),
        avg_reversion_magnitude=probs.get("avg_reversion_magnitude"),
        matched_events=matched,
    )
