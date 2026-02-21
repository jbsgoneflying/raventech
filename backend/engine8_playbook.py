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
_HIGH_VOLUME_THRESHOLD = 1.5  # event-day volume > 1.5x 20d avg = "high volume"

# Playbook magnitude buckets (different from Engine 8 pattern layer — cleaner for desk use)
_MAG_CONTAINED = "contained"   # < 1.0x EM
_MAG_EXTENDED = "extended"     # 1.0x - 1.5x EM
_MAG_EXTREME = "extreme"       # > 1.5x EM

_SCENARIO_MIN_EVENTS = 2
_MATCHED_EVENTS_LIMIT = 8  # max historical events to attach per scenario for LLM context


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


def _summarize_matched_events(events: List[dict]) -> List[Dict[str, Any]]:
    """Return a compact summary of the most recent events for LLM context."""
    dated = sorted(
        events,
        key=lambda e: str(e.get("earnings_date", "")),
        reverse=True,
    )[:_MATCHED_EVENTS_LIMIT]

    summaries: List[Dict[str, Any]] = []
    for ev in dated:
        fwd = ev.get("forward_returns", {})
        summaries.append({
            "date": str(ev.get("earnings_date", ""))[:10],
            "actual_move_pct": ev.get("actual_move_pct"),
            "move_vs_em": ev.get("move_vs_em"),
            "direction": ev.get("direction"),
            "gap_structure": _struct_simplify(
                ev.get("structure_bucket") or ev.get("gap_structure")
            ),
            "rel_volume": ev.get("rel_volume"),
            "fwd_1d": fwd.get("1"),
            "fwd_3d": fwd.get("3"),
            "fwd_5d": fwd.get("5"),
        })
    return summaries


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

    # Volume confirmation: % of events with high relative volume
    vols = [ev.get("rel_volume") for ev in events if ev.get("rel_volume") is not None]
    if vols:
        high_vol_count = sum(1 for v in vols if v >= _HIGH_VOLUME_THRESHOLD)
        stats["high_vol_pct"] = round(high_vol_count / len(vols), 4)
        stats["avg_rel_volume"] = round(sum(vols) / len(vols), 2)
    else:
        stats["high_vol_pct"] = None
        stats["avg_rel_volume"] = None

    # HOLD structure rate (strong PEAD signal when gap is sustained)
    hold_count = sum(1 for ev in events if _struct_simplify(ev.get("structure_bucket") or ev.get("gap_structure")) == "HOLD")
    stats["hold_pct"] = round(hold_count / len(events), 4) if events else None

    # Optimal horizon: pick the horizon with highest continuation (or reversion for FADE)
    best_h = 3
    best_rate = 0.0
    for h in _HORIZONS:
        cr = stats.get(f"continuation_rate_{h}d")
        if cr is not None and cr > best_rate:
            best_rate = cr
            best_h = h
    stats["optimal_hold_days"] = best_h

    return stats


def _derive_action(stats: Dict[str, Any], direction: str) -> Dict[str, Any]:
    """Derive recommended action from scenario stats.

    Primary signal: 5-day continuation/reversion rate (captures PEAD drift).
    Confirmation: 1-day rate + volume confirmation + HOLD structure.
    """
    cont_5d = stats.get("continuation_rate_5d")
    cont_3d = stats.get("continuation_rate_3d")
    rev_5d = stats.get("reversion_rate_5d")
    cont_1d = stats.get("continuation_rate_1d")
    count = stats.get("count", 0)
    high_vol_pct = stats.get("high_vol_pct")
    hold_pct = stats.get("hold_pct")

    if cont_5d is None or count < _SCENARIO_MIN_EVENTS:
        return {"action": "PASS", "confidence": "INSUFFICIENT", "reason": "Not enough historical events for this scenario."}

    # Base confidence from sample size
    confidence = "LOW" if count < 6 else "MEDIUM" if count < 10 else "HIGH"

    # Volume and structure can upgrade confidence by one tier
    vol_confirmed = high_vol_pct is not None and high_vol_pct >= 0.6
    hold_confirmed = hold_pct is not None and hold_pct >= 0.5
    confirmations = sum([vol_confirmed, hold_confirmed])
    if confirmations >= 1 and confidence == "LOW":
        confidence = "MEDIUM"
    elif confirmations >= 1 and confidence == "MEDIUM":
        confidence = "HIGH"

    # Build reason with context
    reason_parts: list[str] = []

    if cont_5d >= 0.65 and (cont_1d is None or cont_1d >= 0.55):
        reason_parts.append(f"{round(cont_5d * 100)}% continuation over 5d ({count} events)")
        if vol_confirmed:
            reason_parts.append(f"{round(high_vol_pct * 100)}% on high volume")
        if hold_confirmed:
            reason_parts.append(f"{round(hold_pct * 100)}% held the gap intraday")
        return {
            "action": "CONTINUE",
            "confidence": confidence,
            "reason": ". ".join(reason_parts) + ".",
        }

    if rev_5d is not None and rev_5d >= 0.60 and (cont_1d is not None and cont_1d < 0.45):
        reason_parts.append(f"{round(rev_5d * 100)}% reversion over 5d ({count} events)")
        if high_vol_pct is not None and high_vol_pct < 0.4:
            reason_parts.append("low volume confirms overreaction")
        return {
            "action": "FADE",
            "confidence": confidence,
            "reason": ". ".join(reason_parts) + ".",
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

    # Re-bucket events into playbook magnitude buckets.
    # When move_vs_em is null (historical EM unavailable), approximate using
    # the CURRENT EM as denominator — not perfect but close enough for bucketing.
    for ev in all_event_rows:
        mve = ev.get("move_vs_em")
        if mve is None and em_pct and em_pct > 0:
            amp = ev.get("actual_move_pct")
            if amp is not None:
                mve = abs(amp) / em_pct
        ev["_pb_mag"] = _mag_bucket(mve)
        ev["_pb_struct"] = _struct_simplify(ev.get("structure_bucket") or ev.get("gap_structure"))

    # Build scenario matrix — strategy:
    # 1. Try granular (mag × direction × structure) if enough events
    # 2. Always add collapsed (mag × direction, structure=ANY)
    # 3. Add "all" direction-level aggregates as baseline
    scenarios: List[Dict[str, Any]] = []

    for mag in [_MAG_CONTAINED, _MAG_EXTENDED, _MAG_EXTREME]:
        for direction in ["UP", "DOWN"]:
            # Collapsed (mag × direction) — always try this first
            all_dir = [
                ev for ev in all_event_rows
                if ev["_pb_mag"] == mag and ev.get("direction") == direction
            ]
            if len(all_dir) >= _SCENARIO_MIN_EVENTS:
                stats = _compute_scenario_stats(all_dir, direction)
                action = _derive_action(stats, direction)
                scenarios.append({
                    "magnitude": mag,
                    "direction": direction,
                    "structure": "ANY",
                    "key": f"{mag}_{direction}_ANY",
                    "matched_events": _summarize_matched_events(all_dir),
                    **stats,
                    **action,
                })

            # Granular structure breakdown (only if meaningfully different from collapsed)
            for struct in ["HOLD", "FADE"]:
                matched = [
                    ev for ev in all_dir
                    if ev["_pb_struct"] == struct
                ]
                if len(matched) >= _SCENARIO_MIN_EVENTS and len(matched) < len(all_dir):
                    stats = _compute_scenario_stats(matched, direction)
                    action = _derive_action(stats, direction)
                    scenarios.append({
                        "magnitude": mag,
                        "direction": direction,
                        "structure": struct,
                        "key": f"{mag}_{direction}_{struct}",
                        "matched_events": _summarize_matched_events(matched),
                        **stats,
                        **action,
                    })

    # Baseline: direction-only aggregates (all magnitudes)
    for direction in ["UP", "DOWN"]:
        all_events_dir = [ev for ev in all_event_rows if ev.get("direction") == direction]
        if len(all_events_dir) >= _SCENARIO_MIN_EVENTS:
            stats = _compute_scenario_stats(all_events_dir, direction)
            action = _derive_action(stats, direction)
            scenarios.append({
                "magnitude": "all",
                "direction": direction,
                "structure": "ANY",
                "key": f"all_{direction}_ANY",
                "matched_events": _summarize_matched_events(all_events_dir),
                **stats,
                **action,
            })

    # Sort: magnitude-specific first (contained → extended → extreme), then "all" baseline.
    # Within each magnitude, UP before DOWN.
    mag_order = {_MAG_CONTAINED: 0, _MAG_EXTENDED: 1, _MAG_EXTREME: 2, "all": 3}
    dir_order = {"UP": 0, "DOWN": 1}
    struct_order = {"ANY": 0, "HOLD": 1, "FADE": 2}
    scenarios.sort(key=lambda s: (
        mag_order.get(s.get("magnitude", "all"), 3),
        dir_order.get(s.get("direction", "DOWN"), 1),
        struct_order.get(s.get("structure", "ANY"), 0),
    ))

    # Threshold prices
    thresholds = None
    if stock_price and em_pct and stock_price > 0 and em_pct > 0:
        thresholds = compute_threshold_prices(stock_price, em_pct)

    # Quick reference: summarize actionable scenarios into desk-ready lines.
    # Skip "all" baseline if we have magnitude-specific scenarios for that direction.
    has_mag_specific = {d: False for d in ("UP", "DOWN")}
    for s in scenarios:
        if s.get("action") != "PASS" and s["magnitude"] != "all":
            has_mag_specific[s["direction"]] = True

    quick_ref: List[str] = []
    for s in scenarios:
        if s.get("action") == "PASS":
            continue
        mag_label = s["magnitude"].upper()
        dir_label = s["direction"]
        struct_label = s["structure"]
        act = s["action"]
        conf = s.get("confidence", "LOW")
        n = s.get("count", 0)
        c5 = s.get("continuation_rate_5d")
        rate_note = f" — {round(c5 * 100)}% cont. over 5d" if c5 is not None else ""

        if mag_label == "ALL":
            if has_mag_specific.get(dir_label):
                continue
            dir_word = "up" if dir_label == "UP" else "down"
            struct_note = f" with gap-and-{struct_label.lower()}" if struct_label != "ANY" else ""
            quick_ref.append(f"Any {dir_word} gap{struct_note} → {act} ({conf}, {n} events{rate_note})")
        elif thresholds and thresholds.get("levels"):
            mult_key = "1.0x" if mag_label == "CONTAINED" else "1.5x" if mag_label == "EXTENDED" else "2.0x"
            lvl = thresholds["levels"].get(mult_key, {})
            price_ref = lvl.get("up_price") if dir_label == "UP" else lvl.get("down_price")
            if price_ref:
                dir_word = "above" if dir_label == "UP" else "below"
                struct_note = f" with gap-and-{struct_label.lower()}" if struct_label != "ANY" else ""
                quick_ref.append(
                    f"If opens {dir_word} ${price_ref}{struct_note} → {act} ({conf}, {n} events{rate_note})"
                )
        else:
            gap_range = "<1.0× EM" if mag_label == "CONTAINED" else "1.0–1.5× EM" if mag_label == "EXTENDED" else ">1.5× EM"
            struct_note = f", gap-and-{struct_label.lower()}" if struct_label != "ANY" else ""
            quick_ref.append(
                f"{dir_label} gap {gap_range}{struct_note} → {act} ({conf}, {n} events{rate_note})"
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
