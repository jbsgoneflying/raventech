"""Cross-Engine Consensus Engine.

Aggregates recommendations across all active engines into a single
agreement/disagreement view with a composite conviction score.

When Engine 3 says "sell vol via credit spreads", Engine 12 says "fade VIX
with short call spread", and Engine 2 says "1.0x EM iron condor" — they're
all aligned. This engine quantifies that alignment.
"""
from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)


@dataclass
class EngineSignal:
    """Distilled signal from one engine."""
    engine_id: int
    engine_name: str
    direction: str          # "sell_vol", "buy_vol", "neutral", "risk_off", "risk_on"
    conviction: float       # 0-100
    structure: str          # e.g., "iron_condor", "credit_spread", "pairs_trade"
    summary: str            # Human-readable one-liner
    updated_at: str = ""
    active: bool = True


@dataclass
class ConsensusResult:
    """Cross-engine consensus assessment."""
    as_of: str = ""
    signals: List[EngineSignal] = field(default_factory=list)
    consensus_score: float = 0.0       # 0-100, higher = more agreement
    consensus_direction: str = "mixed"  # dominant direction
    direction_breakdown: Dict[str, int] = field(default_factory=dict)
    alignment_pairs: List[str] = field(default_factory=list)
    conflicts: List[str] = field(default_factory=list)
    composite_conviction: float = 0.0
    desk_note: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signals"] = [asdict(s) for s in self.signals]
        return d


def _extract_regime_signal(regime_data: dict) -> Optional[EngineSignal]:
    """Extract signal from Engine 3 (global lead-lag regime)."""
    label = str(regime_data.get("label", "")).lower()
    score = float(regime_data.get("score", 50))

    if label == "risk-on":
        direction = "sell_vol"
        structure = "credit_spreads"
    elif label == "stressed":
        direction = "risk_off"
        structure = "none"
    elif label == "risk-off":
        direction = "buy_vol"
        structure = "protective_puts"
    else:
        direction = "neutral"
        structure = "reduced_exposure"

    return EngineSignal(
        engine_id=3, engine_name="Global Lead-Lag Regime",
        direction=direction,
        conviction=max(0, 100 - score) if direction == "sell_vol" else score,
        structure=structure,
        summary=f"Regime: {label.title()} (score {score:.0f})",
    )


def _extract_ic_signal(ic_data: dict) -> Optional[EngineSignal]:
    """Extract signal from Engine 2 (SPX Iron Condor)."""
    rec = ic_data.get("recommendation") or ic_data.get("recSimple") or {}
    if isinstance(rec, str):
        return EngineSignal(
            engine_id=2, engine_name="SPX Iron Condor",
            direction="sell_vol", conviction=50,
            structure="iron_condor",
            summary=f"IC recommendation: {rec}",
        )
    em_pref = float(rec.get("emPreference", 1.0)) if isinstance(rec, dict) else 1.0
    go_no_go = str(rec.get("goNoGo", "")).lower() if isinstance(rec, dict) else ""

    if "no_go" in go_no_go or "no-go" in go_no_go:
        direction = "risk_off"
        conviction = 20
    elif em_pref >= 1.5:
        direction = "sell_vol"
        conviction = 80
    elif em_pref >= 1.0:
        direction = "sell_vol"
        conviction = 60
    else:
        direction = "neutral"
        conviction = 40

    return EngineSignal(
        engine_id=2, engine_name="SPX Iron Condor",
        direction=direction, conviction=conviction,
        structure="iron_condor",
        summary=f"IC: {em_pref:.1f}x EM, {go_no_go or 'pending'}",
    )


def _extract_vix_signal(vix_data: dict) -> Optional[EngineSignal]:
    """Extract signal from Engine 12 (VIX Spike Fade)."""
    spike = vix_data.get("spike", {})
    if not spike.get("detected"):
        return EngineSignal(
            engine_id=12, engine_name="VIX Spike Fade",
            direction="neutral", conviction=0,
            structure="none",
            summary="No spike detected",
            active=False,
        )
    edge = vix_data.get("edgeComposite", {})
    composite = float(edge.get("compositeScore", 0))
    rec = vix_data.get("recommendation", {})
    structure = str(rec.get("structure", "")) if isinstance(rec, dict) else ""

    return EngineSignal(
        engine_id=12, engine_name="VIX Spike Fade",
        direction="sell_vol", conviction=min(100, composite),
        structure=structure or "short_call_spread",
        summary=f"VIX fade: edge {composite:.0f}, {structure}",
    )


def _extract_credit_signal(credit_data: dict) -> Optional[EngineSignal]:
    """Extract signal from Engine 8 (Credit Stress)."""
    composite = credit_data.get("composite", {})
    phase = int(composite.get("phase", 1))
    score = float(composite.get("score", 0))

    if phase >= 3:
        direction = "risk_off"
        conviction = min(100, score)
        structure = "credit_hedges"
    elif phase == 2:
        direction = "neutral"
        conviction = 50
        structure = "reduced_credit"
    else:
        direction = "risk_on"
        conviction = max(0, 100 - score)
        structure = "credit_exposure"

    return EngineSignal(
        engine_id=8, engine_name="Credit Stress Drift",
        direction=direction, conviction=conviction,
        structure=structure,
        summary=f"Credit phase {phase}, score {score:.0f}",
    )


def compute_consensus(
    signals: List[EngineSignal],
) -> ConsensusResult:
    """Compute cross-engine consensus from individual signals.

    Agreement is measured by how many active signals share the same direction.
    Conviction is the weighted average of individual convictions.
    """
    now = dt.datetime.utcnow().isoformat() + "Z"
    active = [s for s in signals if s.active and s.conviction > 0]

    if not active:
        return ConsensusResult(
            as_of=now, signals=signals,
            desk_note="No active signals across engines.",
        )

    # Direction tally
    direction_counts: Dict[str, int] = {}
    direction_conviction: Dict[str, float] = {}
    for s in active:
        direction_counts[s.direction] = direction_counts.get(s.direction, 0) + 1
        direction_conviction[s.direction] = (
            direction_conviction.get(s.direction, 0) + s.conviction
        )

    dominant_dir = max(direction_counts, key=direction_counts.get)  # type: ignore
    dominant_count = direction_counts[dominant_dir]

    # Consensus score: proportion of engines agreeing * average conviction
    agreement_ratio = dominant_count / len(active)
    avg_conviction = sum(s.conviction for s in active) / len(active)
    consensus_score = agreement_ratio * avg_conviction

    # Identify alignment pairs and conflicts
    alignment_pairs: List[str] = []
    conflicts: List[str] = []
    for i, s1 in enumerate(active):
        for s2 in active[i + 1:]:
            if s1.direction == s2.direction:
                alignment_pairs.append(
                    f"E{s1.engine_id} + E{s2.engine_id}: both {s1.direction}"
                )
            elif (s1.direction in ("sell_vol", "risk_on") and
                  s2.direction in ("buy_vol", "risk_off")) or \
                 (s2.direction in ("sell_vol", "risk_on") and
                  s1.direction in ("buy_vol", "risk_off")):
                conflicts.append(
                    f"E{s1.engine_id} ({s1.direction}) vs E{s2.engine_id} ({s2.direction})"
                )

    # Desk note
    if consensus_score >= 75 and not conflicts:
        desk_note = f"Strong consensus: {dominant_count}/{len(active)} engines aligned {dominant_dir}. High-conviction window."
    elif consensus_score >= 50:
        desk_note = f"Moderate consensus: {dominant_count}/{len(active)} engines lean {dominant_dir}. Normal sizing."
    elif conflicts:
        desk_note = f"Conflicting signals: {len(conflicts)} disagreement(s). Reduce exposure or wait."
    else:
        desk_note = "Mixed signals. No clear directional consensus. Selective positioning only."

    return ConsensusResult(
        as_of=now,
        signals=signals,
        consensus_score=round(consensus_score, 1),
        consensus_direction=dominant_dir,
        direction_breakdown=direction_counts,
        alignment_pairs=alignment_pairs,
        conflicts=conflicts,
        composite_conviction=round(avg_conviction, 1),
        desk_note=desk_note,
    )


def build_consensus_from_apis(
    *,
    regime_data: Optional[dict] = None,
    ic_data: Optional[dict] = None,
    vix_data: Optional[dict] = None,
    credit_data: Optional[dict] = None,
) -> ConsensusResult:
    """Build consensus from raw API response dicts."""
    signals: List[EngineSignal] = []

    if regime_data:
        sig = _extract_regime_signal(regime_data)
        if sig:
            signals.append(sig)

    if ic_data:
        sig = _extract_ic_signal(ic_data)
        if sig:
            signals.append(sig)

    if vix_data:
        sig = _extract_vix_signal(vix_data)
        if sig:
            signals.append(sig)

    if credit_data:
        sig = _extract_credit_signal(credit_data)
        if sig:
            signals.append(sig)

    return compute_consensus(signals)
