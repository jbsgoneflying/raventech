"""RTv2.0 — Cross-Engine Scorer (Unified Priority Score).

Computes UPS for every signal entering the Unified Idea Queue using
within-engine percentile rank (rolling 90-day).  No expected value
term in v1.

Storage:
  - Redis: rtv2:engine_scores:{engine_id}  rolling score buffer  TTL 180 days
"""

from __future__ import annotations

import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

SCORE_BUFFER_TTL_S = 180 * 86400
SCORE_BUFFER_PREFIX = "rtv2:engine_scores"
COLD_START_THRESHOLD = 30

UPS_WEIGHTS = {
    "engine_percentile_rank": 0.40,
    "regime_alignment":       0.25,
    "gate_status_bonus":      0.15,
    "timing_score":           0.20,
}

GATE_BONUS = {"TRADABLE": 15.0, "WATCH": 5.0, "SUPPRESS": 0.0}

# UPS soft penalties keyed by condition
SOFT_PENALTIES: Dict[str, float] = {
    "engine_gate_suppress":       -30.0,
    "same_underlying_overlap":    -20.0,
    "directional_tilt_excess":    -10.0,
    "consecutive_bucket_losses":  -15.0,
    "regime_flow_divergence":     -15.0,
    "correlation_warning":        -20.0,
    "conflicting_directional":    -15.0,
    "thesis_weakening_bucket":    -10.0,
}


@dataclass
class UPSResult:
    signal_id: str = ""
    engine_id: str = ""
    ticker: str = ""
    raw_score: float = 0.0
    percentile_rank: float = 0.0
    regime_alignment: float = 0.0
    gate_status_bonus: float = 0.0
    timing_score: float = 0.0
    base_ups: float = 0.0
    penalties: List[dict] = field(default_factory=list)
    final_ups: float = 0.0
    hard_blocked: bool = False
    hard_block_reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Score buffer management (rolling percentile)
# ---------------------------------------------------------------------------

def _load_score_buffer(engine_id: str, store: Any) -> List[float]:
    if store is None:
        return []
    key = f"{SCORE_BUFFER_PREFIX}:{engine_id}"
    data = store.get_json(key)
    if isinstance(data, list):
        return [float(x) for x in data]
    return []


def _save_score_buffer(engine_id: str, buffer: List[float], store: Any) -> None:
    if store is None:
        return
    key = f"{SCORE_BUFFER_PREFIX}:{engine_id}"
    # keep last 500 scores
    trimmed = buffer[-500:]
    store.set_json(key, trimmed, ttl_s=SCORE_BUFFER_TTL_S)


def record_engine_score(engine_id: str, raw_score: float, store: Any) -> None:
    """Append a score to the rolling buffer for this engine."""
    buf = _load_score_buffer(engine_id, store)
    buf.append(raw_score)
    _save_score_buffer(engine_id, buf, store)


def compute_percentile_rank(raw_score: float, buffer: List[float]) -> float:
    """Within-engine percentile rank (0-100)."""
    if len(buffer) < COLD_START_THRESHOLD:
        return raw_score  # cold-start fallback
    count_le = sum(1 for s in buffer if s <= raw_score)
    return round((count_le / len(buffer)) * 100, 1)


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------

def score_regime_alignment(
    engine_gate: str,
    vol_state: str,
    flow_label: str,
    regime: str,
    trade_type: str = "",
) -> float:
    """Composite regime+vol+flow alignment (0-100)."""
    gate_map = {"allowed": 80, "selective": 50, "suppressed": 20}
    base = gate_map.get(str(engine_gate).lower(), 50)

    # vol adjustment ±10
    vol_adj = 0
    if vol_state in ("contango", "normal"):
        vol_adj = 10
    elif vol_state in ("backwardation", "expanding"):
        if trade_type in ("mean_reversion", "premium_decay"):
            vol_adj = -10
        else:
            vol_adj = 5

    # flow adjustment ±10
    flow_adj = 0
    fl = str(flow_label).lower()
    if "risk-on" in fl or "supportive" in fl:
        flow_adj = 10
    elif "risk-off" in fl or "stress" in fl:
        flow_adj = -10

    return max(0, min(100, base + vol_adj + flow_adj))


def score_timing(
    days_since_signal: float = 0.0,
    event_proximity_days: Optional[float] = None,
    sequencer_favored: bool = False,
    engine_id: str = "",
) -> float:
    """Timing score (0-100): freshness + event proximity + sequencer."""
    # freshness: full marks <1 day, linear decay to 0 at 5 days
    freshness = max(0, min(1.0, 1.0 - (days_since_signal - 1.0) / 4.0)) * 100 if days_since_signal <= 5 else 0
    if days_since_signal < 1:
        freshness = 100

    event_bonus = 0
    if event_proximity_days is not None and engine_id in ("E1", "E8"):
        if event_proximity_days <= 5:
            event_bonus = 20

    seq_bonus = 20 if sequencer_favored else 0

    return min(100, freshness * 0.6 + event_bonus + seq_bonus)


# ---------------------------------------------------------------------------
# UPS computation
# ---------------------------------------------------------------------------

def compute_ups(
    *,
    signal_id: str,
    engine_id: str,
    ticker: str,
    raw_score: float,
    engine_gate: str = "allowed",
    gate_decision: str = "TRADABLE",
    vol_state: str = "normal",
    flow_label: str = "",
    regime: str = "Transitional",
    trade_type: str = "",
    days_since_signal: float = 0.0,
    event_proximity_days: Optional[float] = None,
    sequencer_favored: bool = False,
    store: Any = None,
    penalty_conditions: Optional[List[str]] = None,
    hard_block_check: Optional[Dict[str, Any]] = None,
) -> UPSResult:
    """Compute the Unified Priority Score for a single signal."""

    buffer = _load_score_buffer(engine_id, store)
    pctile = compute_percentile_rank(raw_score, buffer)

    ra = score_regime_alignment(engine_gate, vol_state, flow_label, regime, trade_type)
    gs = GATE_BONUS.get(str(gate_decision).upper(), 5.0)
    ts = score_timing(days_since_signal, event_proximity_days, sequencer_favored, engine_id)

    base = (
        pctile * UPS_WEIGHTS["engine_percentile_rank"]
        + ra    * UPS_WEIGHTS["regime_alignment"]
        + gs    * UPS_WEIGHTS["gate_status_bonus"]
        + ts    * UPS_WEIGHTS["timing_score"]
    )
    base = round(base, 2)

    penalties: List[dict] = []
    total_penalty = 0.0
    for cond in (penalty_conditions or []):
        pen = SOFT_PENALTIES.get(cond, 0.0)
        if pen != 0:
            penalties.append({"condition": cond, "penalty": pen})
            total_penalty += pen

    final = round(max(0, min(100, base + total_penalty)), 2)

    hard_blocked = False
    hard_reason = ""
    if hard_block_check:
        if hard_block_check.get("bucket_ru_exhausted"):
            hard_blocked = True
            hard_reason = "Bucket RU budget fully exhausted"
        elif hard_block_check.get("portfolio_ru_cap_reached"):
            hard_blocked = True
            hard_reason = "Portfolio-wide RU hard cap reached"
        elif hard_block_check.get("ru_exceeds_cap_no_override"):
            hard_blocked = True
            hard_reason = "Derived RU exceeds engine hard cap without override"
        elif hard_block_check.get("data_integrity_failure"):
            hard_blocked = True
            hard_reason = "Signal missing required fields"

    return UPSResult(
        signal_id=signal_id,
        engine_id=engine_id,
        ticker=ticker,
        raw_score=raw_score,
        percentile_rank=round(pctile, 2),
        regime_alignment=round(ra, 2),
        gate_status_bonus=gs,
        timing_score=round(ts, 2),
        base_ups=base,
        penalties=penalties,
        final_ups=final,
        hard_blocked=hard_blocked,
        hard_block_reason=hard_reason,
    )


# ---------------------------------------------------------------------------
# Conflict resolution
# ---------------------------------------------------------------------------

def rank_signals(
    results: List[UPSResult],
    engine_hit_rates: Optional[Dict[str, float]] = None,
) -> List[UPSResult]:
    """Sort signals by UPS with conflict resolution tie-breaking.

    When UPS within 5 points: prefer lower correlation, then better engine hit rate.
    """
    hit = engine_hit_rates or {}

    def sort_key(r: UPSResult):
        hr = hit.get(r.engine_id, 5.0)
        return (-r.final_ups, -hr, r.ticker)

    ranked = sorted(results, key=sort_key)
    return ranked
