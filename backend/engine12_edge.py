"""Engine 12 — Four-Edge Decomposition for VIX Spike Fade.

Edge 1: Spot / term-structure dislocation
Edge 2: Implied vol vs expected realized vol
Edge 3: Term structure shape (contango/backwardation)
Edge 4: Persistence mispricing (implied half-life vs modeled half-life)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.engine12_ou_model import (
    OUParams,
    implied_half_life_from_term_structure,
    implied_half_life_from_ou_vs_market,
    implied_decay_from_vixy,
    modeled_half_life_days,
    persistence_mispricing,
)

LOG = logging.getLogger(__name__)


@dataclass
class EdgeResult:
    edge_id: str = ""
    label: str = ""
    score: float = 0.0          # 0-100 (higher = more edge for short vol)
    raw_value: float = 0.0
    interpretation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "edgeId": self.edge_id,
            "label": self.label,
            "score": round(self.score, 1),
            "rawValue": round(self.raw_value, 4),
            "interpretation": self.interpretation,
        }


@dataclass
class EdgeComposite:
    score: float = 0.0
    label: str = ""
    edges: List[EdgeResult] = None

    def __post_init__(self):
        if self.edges is None:
            self.edges = []

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "label": self.label,
            "edges": [e.to_dict() for e in self.edges],
        }


def _clamp(lo: float, hi: float, x: float) -> float:
    return max(lo, min(hi, x))


def compute_edge1_spot_term_dislocation(
    vix_spot: float,
    iv_30d: Optional[float],
) -> EdgeResult:
    """Edge 1: Spot VIX vs 30d implied forward dislocation.

    High divergence (spot >> forward) historically predicts mean reversion.
    """
    if iv_30d is None or iv_30d <= 0 or vix_spot <= 0:
        return EdgeResult(
            edge_id="spot_term_dislocation",
            label="Spot/Term Dislocation",
            interpretation="Insufficient term structure data.",
        )

    dislocation = (vix_spot - iv_30d) / iv_30d
    # Map dislocation to 0-100: 0% dislocation -> 50, 20%+ -> ~90
    score = _clamp(0, 100, 50.0 + dislocation * 200.0)

    if dislocation > 0.15:
        interp = "Strong dislocation: spot significantly above forward — high mean-reversion edge"
    elif dislocation > 0.05:
        interp = "Moderate dislocation: spot above forward — some mean-reversion edge"
    elif dislocation > -0.05:
        interp = "Minimal dislocation: spot near forward — limited edge"
    else:
        interp = "Negative dislocation: spot below forward — inverted, no short vol edge"

    return EdgeResult(
        edge_id="spot_term_dislocation",
        label="Spot/Term Dislocation",
        score=score,
        raw_value=dislocation,
        interpretation=interp,
    )


def compute_edge2_iv_vs_rv(
    vix_spot: float,
    historical_rv_post_events: List[float],
) -> EdgeResult:
    """Edge 2: Current implied vol vs expected post-event realized vol.

    VIX IS annualized implied vol. Historical rv_5d_after values from the
    shock DB are also annualized. Compare directly — the spread tells us
    how much the market is overpricing (or underpricing) post-shock realized.
    """
    if vix_spot <= 0:
        return EdgeResult(
            edge_id="iv_vs_rv",
            label="IV vs Expected RV",
            interpretation="No VIX data available.",
        )

    implied_vol = vix_spot  # VIX level = annualized implied vol

    if historical_rv_post_events and len(historical_rv_post_events) >= 3:
        expected_rv = sum(historical_rv_post_events) / len(historical_rv_post_events)
    else:
        expected_rv = implied_vol * 0.70

    spread = implied_vol - expected_rv
    # Normalize: 0 spread -> 50, +5 vol points -> ~80
    score = _clamp(0, 100, 50.0 + spread * 6.0)

    if spread > 5:
        interp = f"IV significantly overpriced vs historical post-event RV (VIX {implied_vol:.1f} vs {expected_rv:.1f} avg RV) — strong short vol edge"
    elif spread > 2:
        interp = f"IV moderately overpriced (VIX {implied_vol:.1f} vs {expected_rv:.1f} avg RV) — moderate edge"
    elif spread > 0:
        interp = f"IV slightly above expected RV (VIX {implied_vol:.1f} vs {expected_rv:.1f} avg RV) — marginal edge"
    else:
        interp = f"IV below expected post-event RV (VIX {implied_vol:.1f} vs {expected_rv:.1f} avg RV) — no short vol edge"

    return EdgeResult(
        edge_id="iv_vs_rv",
        label="IV vs Expected RV",
        score=score,
        raw_value=spread,
        interpretation=interp,
    )


def compute_edge3_term_structure_shape(
    iv_30d: Optional[float],
    iv_60d: Optional[float],
    iv_90d: Optional[float],
) -> EdgeResult:
    """Edge 3: Term structure shape classification.

    Extreme backwardation = near peak stress, decay imminent.
    """
    vals = [(d, v) for d, v in [(30, iv_30d), (60, iv_60d), (90, iv_90d)] if v is not None and v > 0]
    if len(vals) < 2:
        return EdgeResult(
            edge_id="term_structure_shape",
            label="Term Structure Shape",
            interpretation="Insufficient DTE data for term structure.",
        )

    vals.sort(key=lambda x: x[0])
    front = vals[0][1]
    back = vals[-1][1]
    slope = (back - front) / front if front > 0 else 0

    if slope < -0.10:
        shape = "extreme_backwardation"
        score = 85.0
        interp = "Extreme backwardation — peak stress, decay historically imminent"
    elif slope < -0.03:
        shape = "backwardation"
        score = 70.0
        interp = "Backwardation — stress elevated, vol compression likely"
    elif slope < 0.03:
        shape = "flat"
        score = 50.0
        interp = "Flat term structure — no directional edge from shape"
    else:
        shape = "contango"
        score = 30.0
        interp = "Contango — market already pricing vol decline, limited edge"

    return EdgeResult(
        edge_id="term_structure_shape",
        label="Term Structure Shape",
        score=score,
        raw_value=slope,
        interpretation=interp,
    )


def compute_edge4_persistence_mispricing(
    ou_params: Optional[OUParams],
    iv_30d: Optional[float],
    iv_60d: Optional[float],
    vix_spot: float = 0.0,
    vixy_closes: Optional[List[float]] = None,
) -> EdgeResult:
    """Edge 4: Persistence mispricing — implied half-life vs modeled half-life.

    Tries three methods in priority order:
    A) Direct: implied half-life from IV term structure slope (30d vs 60d)
    B) OU vs Market: compare OU forward forecast to market IV absolute level
    C) VIXY decay: recent VIXY price decay rate as futures-implied half-life
    """
    if ou_params is None:
        return EdgeResult(
            edge_id="persistence_mispricing",
            label="Persistence Mispricing",
            interpretation="OU model not calibrated.",
        )

    m_hl = modeled_half_life_days(ou_params.kappa)

    # Method A: direct term structure slope
    i_hl = implied_half_life_from_term_structure(
        iv_30d if iv_30d else 0, iv_60d if iv_60d else 0,
    )
    method = "term_structure"

    # Method B: OU absolute level vs market IV
    if i_hl is None and iv_30d and iv_30d > 0 and vix_spot > 0:
        i_hl = implied_half_life_from_ou_vs_market(ou_params, vix_spot, iv_30d)
        if i_hl is not None:
            method = "ou_vs_market"

    # Method C: VIXY decay rate
    if i_hl is None and vixy_closes and len(vixy_closes) > 10:
        i_hl = implied_decay_from_vixy(vixy_closes)
        if i_hl is not None:
            method = "vixy_decay"

    mispricing = persistence_mispricing(i_hl, m_hl)

    if mispricing is None:
        return EdgeResult(
            edge_id="persistence_mispricing",
            label="Persistence Mispricing",
            score=50.0,
            raw_value=0.0,
            interpretation=f"All persistence methods unavailable. Modeled half-life: {m_hl:.0f}d.",
        )

    method_label = {
        "term_structure": "IV slope",
        "ou_vs_market": "OU vs market",
        "vixy_decay": "VIXY decay",
    }.get(method, method)

    score = _clamp(0, 100, 50.0 + mispricing * 1.5)

    if mispricing > 15:
        interp = (
            f"Strong mispricing ({method_label}): market implies {i_hl:.0f}d persistence vs model {m_hl:.0f}d "
            f"(+{mispricing:.0f}d overpriced) — significant short vol edge"
        )
    elif mispricing > 5:
        interp = (
            f"Moderate mispricing ({method_label}): implied {i_hl:.0f}d vs model {m_hl:.0f}d "
            f"(+{mispricing:.0f}d) — some edge"
        )
    elif mispricing > -5:
        interp = (
            f"Minimal mispricing ({method_label}): implied {i_hl:.0f}d vs model {m_hl:.0f}d — "
            f"fairly priced"
        )
    else:
        interp = (
            f"Negative mispricing ({method_label}): market prices faster decay "
            f"(implied {i_hl:.0f}d vs model {m_hl:.0f}d) — caution"
        )

    return EdgeResult(
        edge_id="persistence_mispricing",
        label="Persistence Mispricing",
        score=score,
        raw_value=mispricing,
        interpretation=interp,
    )


def compute_edge_composite(
    vix_spot: float,
    iv_30d: Optional[float],
    iv_60d: Optional[float],
    iv_90d: Optional[float],
    ou_params: Optional[OUParams],
    historical_rv_post_events: List[float],
    vixy_closes: Optional[List[float]] = None,
) -> EdgeComposite:
    """Compute all four edges and produce weighted composite."""

    e1 = compute_edge1_spot_term_dislocation(vix_spot, iv_30d)
    e2 = compute_edge2_iv_vs_rv(vix_spot, historical_rv_post_events)
    e3 = compute_edge3_term_structure_shape(iv_30d, iv_60d, iv_90d)
    e4 = compute_edge4_persistence_mispricing(
        ou_params, iv_30d, iv_60d, vix_spot=vix_spot, vixy_closes=vixy_closes,
    )

    # Weights: Edge1 25%, Edge2 30%, Edge3 15%, Edge4 30%
    composite = (
        e1.score * 0.25
        + e2.score * 0.30
        + e3.score * 0.15
        + e4.score * 0.30
    )

    if composite >= 70:
        label = "Strong Short-Vol Edge"
    elif composite >= 55:
        label = "Moderate Edge"
    elif composite >= 45:
        label = "Marginal"
    else:
        label = "No Edge / Caution"

    return EdgeComposite(score=composite, label=label, edges=[e1, e2, e3, e4])
