"""Engine 8 – Pre-Earnings Scenario Playbook.

Builds a conditional scenario matrix BEFORE earnings so the desk has a
complete play sheet: "if gap X%, do Y."

Uses the same historical event rows (with forward returns) that the
Engine 8 pattern layer already computes, but re-organizes them into
scenario buckets rather than trying to match a single post-event state.

Scenario dimensions:
  - Magnitude: contained (<1.0x EM), extended (1.0-1.5x EM), extreme (>1.5x EM)
  - Direction: UP, DOWN
  - Structure: HOLD (gap sustains), FADE (gap reverses), ANY (collapsed)

For each bucket the playbook computes:
  - Continuation rate and avg drift at 1d, 3d, 5d
  - Reversion rate and avg magnitude
  - Recommended action: CONTINUE / FADE / PASS
  - Confidence level based on sample size

Also computes threshold price levels from current stock price + EM.
"""

from __future__ import annotations

import datetime as dt
import logging
from typing import Any, Dict, List, Optional, Tuple

from backend.config import FeatureFlags, get_flags

LOG = logging.getLogger(__name__)

_HORIZONS = (1, 3, 5)

# Playbook magnitude buckets (different from Engine 8 pattern layer — cleaner for desk use)
_MAG_CONTAINED = "contained"   # < 1.0x EM
_MAG_EXTENDED = "extended"     # 1.0x - 1.5x EM
_MAG_EXTREME = "extreme"       # > 1.5x EM

_SCENARIO_MIN_EVENTS = 3


def _mag_bucket(move_vs_em: Optional[float]) -> str:
    if move_vs_em is None:
        return "unknown"
    if move_vs_em >= 1.5:
        return _MAG_EXTREME
    if move_vs_em >= 1.0:
        return _MAG_EXTENDED
    return _MAG_CONTAINED


def _struct_simplify(structure_bucket: Optional[str]) -> str:
    if structure_bucket is None:
        return "ANY"
    s = str(structure_bucket).upper()
    if "HOLD" in s:
        return "HOLD"
    if "FADE" in s:
        return "FADE"
    return "ANY"


def _compute_scenario_stats(events: List[dict], direction: str) -> Dict[str, Any]:
    """Compute continuation/reversion stats for a set of events."""
    is_up = direction == "UP"
    stats: Dict[str, Any] = {"count": len(events)}

    for h in _HORIZONS:
        cont_count = 0
        rev_count = 0
        cont_mags: list[float] = []
        rev_mags: list[float] = []
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
            stats[f"continuation_rate_{h}d"] = round(cont_count / total, 4)
            stats[f"reversion_rate_{h}d"] = round(rev_count / total, 4)
            stats[f"avg_continuation_{h}d"] = round(sum(cont_mags) / len(cont_mags), 2) if cont_mags else None
            stats[f"avg_reversion_{h}d"] = round(sum(rev_mags) / len(rev_mags), 2) if rev_mags else None
            stats[f"total_{h}d"] = total
        else:
            stats[f"continuation_rate_{h}d"] = None
            stats[f"reversion_rate_{h}d"] = None
            stats[f"avg_continuation_{h}d"] = None
            stats[f"avg_reversion_{h}d"] = None
            stats[f"total_{h}d"] = 0

    return stats


def _derive_action(stats: Dict[str, Any], direction: str) -> Dict[str, Any]:
    """Derive recommended action from scenario stats.

    Uses the 5-day horizon as primary signal (captures PEAD drift),
    with 1-day as confirmation.
    """
    cont_5d = stats.get("continuation_rate_5d")
    rev_5d = stats.get("reversion_rate_5d")
    cont_1d = stats.get("continuation_rate_1d")
    count = stats.get("count", 0)

    if cont_5d is None or count < _SCENARIO_MIN_EVENTS:
        return {"action": "PASS", "confidence": "INSUFFICIENT", "reason": "Not enough historical events for this scenario."}

    confidence = "LOW" if count < 6 else "MEDIUM" if count < 10 else "HIGH"

    if cont_5d >= 0.65 and (cont_1d is None or cont_1d >= 0.55):
        return {
            "action": "CONTINUE",
            "confidence": confidence,
            "reason": f"{round(cont_5d * 100)}% continuation rate over 5 days ({count} events).",
        }

    if rev_5d is not None and rev_5d >= 0.60 and (cont_1d is not None and cont_1d < 0.45):
        return {
            "action": "FADE",
            "confidence": confidence,
            "reason": f"{round(rev_5d * 100)}% reversion rate over 5 days ({count} events).",
        }

    return {
        "action": "PASS",
        "confidence": confidence,
        "reason": f"Ambiguous: {round(cont_5d * 100)}% cont / {round((rev_5d or 0) * 100)}% rev ({count} events).",
    }


def compute_threshold_prices(
    stock_price: float,
    em_pct: float,
) -> Dict[str, Any]:
    """Compute threshold price levels at EM multiples.

    Returns price levels the desk can watch on earnings day:
    "If opens above $X, scenario Y activates."
    """
    em_decimal = em_pct / 100.0

    thresholds = {
        "stock_price": round(stock_price, 2),
        "em_pct": round(em_pct, 2),
        "levels": {},
    }

    for mult_label, mult in [("1.0x", 1.0), ("1.5x", 1.5), ("2.0x", 2.0)]:
        delta = stock_price * em_decimal * mult
        thresholds["levels"][mult_label] = {
            "multiple": mult,
            "up_price": round(stock_price + delta, 2),
            "down_price": round(stock_price - delta, 2),
            "gap_pct": round(em_pct * mult, 2),
        }

    return thresholds


def compute_scenario_playbook(
    *,
    all_event_rows: List[dict],
    stock_price: Optional[float] = None,
    em_pct: Optional[float] = None,
    flags: Optional[FeatureFlags] = None,
) -> Dict[str, Any]:
    """Build the full pre-earnings scenario playbook.

    Parameters
    ----------
    all_event_rows : list
        Historical event rows from ``_build_all_event_rows()`` — each has
        magnitude_bucket, direction, gap_structure, forward_returns.
    stock_price : float, optional
        Current stock price for threshold calculations.
    em_pct : float, optional
        Current implied move percent for threshold calculations.
    flags : FeatureFlags, optional

    Returns
    -------
    dict with 'scenarios', 'thresholds', 'quick_reference', 'meta'.
    """
    if flags is None:
        flags = get_flags()

    # Re-bucket events into playbook magnitude buckets
    for ev in all_event_rows:
        ev["_pb_mag"] = _mag_bucket(ev.get("move_vs_em"))
        ev["_pb_struct"] = _struct_simplify(ev.get("structure_bucket") or ev.get("gap_structure"))

    # Build scenario matrix
    scenarios: List[Dict[str, Any]] = []
    seen_keys = set()

    for mag in [_MAG_CONTAINED, _MAG_EXTENDED, _MAG_EXTREME]:
        for direction in ["UP", "DOWN"]:
            # Full scenario with structure breakdown
            for struct in ["HOLD", "FADE"]:
                key = (mag, direction, struct)
                if key in seen_keys:
                    continue
                seen_keys.add(key)

                matched = [
                    ev for ev in all_event_rows
                    if ev["_pb_mag"] == mag and ev.get("direction") == direction and ev["_pb_struct"] == struct
                ]

                if len(matched) >= _SCENARIO_MIN_EVENTS:
                    stats = _compute_scenario_stats(matched, direction)
                    action = _derive_action(stats, direction)
                    scenarios.append({
                        "magnitude": mag,
                        "direction": direction,
                        "structure": struct,
                        "key": f"{mag}_{direction}_{struct}",
                        **stats,
                        **action,
                    })

            # Collapsed structure ("ANY") for when HOLD/FADE individually are too thin
            all_dir = [
                ev for ev in all_event_rows
                if ev["_pb_mag"] == mag and ev.get("direction") == direction
            ]
            # Only add collapsed if we didn't get enough granular scenarios
            granular_count = sum(1 for s in scenarios if s["magnitude"] == mag and s["direction"] == direction)
            if granular_count == 0 and len(all_dir) >= _SCENARIO_MIN_EVENTS:
                stats = _compute_scenario_stats(all_dir, direction)
                action = _derive_action(stats, direction)
                scenarios.append({
                    "magnitude": mag,
                    "direction": direction,
                    "structure": "ANY",
                    "key": f"{mag}_{direction}_ANY",
                    **stats,
                    **action,
                })

    # Sort: highest-confidence actionable scenarios first
    action_priority = {"CONTINUE": 0, "FADE": 1, "PASS": 2}
    conf_priority = {"HIGH": 0, "MEDIUM": 1, "LOW": 2, "INSUFFICIENT": 3}
    scenarios.sort(key=lambda s: (action_priority.get(s.get("action", "PASS"), 2), conf_priority.get(s.get("confidence", "LOW"), 2)))

    # Threshold prices
    thresholds = None
    if stock_price and em_pct and stock_price > 0 and em_pct > 0:
        thresholds = compute_threshold_prices(stock_price, em_pct)

    # Quick reference: summarize actionable scenarios into desk-ready lines
    quick_ref: List[str] = []
    for s in scenarios:
        if s.get("action") == "PASS":
            continue
        mag_label = s["magnitude"].upper()
        dir_label = s["direction"]
        struct_label = s["structure"]
        act = s["action"]
        conf = s.get("confidence", "LOW")

        if thresholds and thresholds.get("levels"):
            mult_key = "1.0x" if mag_label == "CONTAINED" else "1.5x" if mag_label == "EXTENDED" else "2.0x"
            lvl = thresholds["levels"].get(mult_key, {})
            price_ref = lvl.get("up_price") if dir_label == "UP" else lvl.get("down_price")
            if price_ref:
                dir_word = "above" if dir_label == "UP" else "below"
                struct_note = f" with gap-and-{struct_label.lower()}" if struct_label != "ANY" else ""
                quick_ref.append(
                    f"If opens {dir_word} ${price_ref}{struct_note} → {act} ({conf})"
                )
        else:
            gap_range = "<1.0x EM" if mag_label == "CONTAINED" else "1.0-1.5x EM" if mag_label == "EXTENDED" else ">1.5x EM"
            struct_note = f", gap-and-{struct_label.lower()}" if struct_label != "ANY" else ""
            quick_ref.append(
                f"{dir_label} gap {gap_range}{struct_note} → {act} ({conf})"
            )

    if not quick_ref:
        quick_ref.append("No high-conviction scenarios found. Default: PASS on all outcomes.")

    # Clean up temp keys
    for ev in all_event_rows:
        ev.pop("_pb_mag", None)
        ev.pop("_pb_struct", None)

    return {
        "scenarios": scenarios,
        "thresholds": thresholds,
        "quick_reference": quick_ref,
        "meta": {
            "total_historical_events": len(all_event_rows),
            "scenarios_computed": len(scenarios),
            "actionable_scenarios": sum(1 for s in scenarios if s.get("action") != "PASS"),
            "min_events_per_scenario": _SCENARIO_MIN_EVENTS,
        },
    }
