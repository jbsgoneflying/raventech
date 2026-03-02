"""Engine 12 — Structure Selection and Position Sizing.

Decision tree selects optimal VIX options structure based on edge analysis,
MC results, and scenario probabilities. Position sizing uses CVaR-based
risk budgeting.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.engine12_mc import MCResult, StructurePnL


@dataclass
class StructureRecommendation:
    primary: str = ""
    primary_rationale: str = ""
    ranked: List[Dict[str, Any]] = field(default_factory=list)
    position_size: Dict[str, Any] = field(default_factory=dict)
    guardrails: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "primary": self.primary,
            "primaryRationale": self.primary_rationale,
            "ranked": self.ranked,
            "positionSize": self.position_size,
            "guardrails": self.guardrails,
        }


def recommend_structure(
    *,
    edge_score: float,
    edge_details: Dict[str, float],
    mc_result: MCResult,
    severity_score: float,
    p_contained: float,
    p_disruption: float,
    p_escalation: float,
    secondary_spike_threshold: float = 0.25,
    contained_threshold: float = 0.60,
    risk_budget_dollars: float = 5000.0,
) -> StructureRecommendation:
    """Select optimal structure and compute position sizing."""

    guardrails: List[str] = []

    # Rank structures by Sharpe ratio from MC
    ranked = sorted(mc_result.structures, key=lambda s: s.sharpe, reverse=True)
    ranked_dicts = []
    for i, s in enumerate(ranked):
        ranked_dicts.append({
            "rank": i + 1,
            "name": s.name,
            "expectedPnL": round(s.expected_pnl, 2),
            "pProfit": round(s.p_profit, 3),
            "sharpe": round(s.sharpe, 3),
            "cvar95": round(s.cvar95, 2),
            "maxLoss": round(s.max_loss, 2),
        })

    # Decision tree
    secondary_spike_prob = p_disruption * 0.5 + p_escalation * 0.8
    persistence_score = edge_details.get("persistence_mispricing", 50)
    iv_rv_score = edge_details.get("iv_vs_rv", 50)
    term_structure_score = edge_details.get("term_structure_shape", 50)

    primary = ""
    rationale = ""

    if secondary_spike_prob > secondary_spike_threshold:
        guardrails.append(
            f"Secondary spike probability ({secondary_spike_prob:.0%}) exceeds {secondary_spike_threshold:.0%} threshold — "
            "avoid aggressive short premium."
        )

    if p_escalation > 0.30:
        primary = "Long Put Spread"
        rationale = (
            f"Escalation probability elevated ({p_escalation:.0%}). Defined-risk directional "
            "put spread captures VIX mean-reversion while limiting tail exposure."
        )
        guardrails.append("Escalation risk >30%: size conservatively, wider strikes.")

    elif term_structure_score >= 80 and persistence_score >= 60:
        primary = "Calendar Spread"
        rationale = (
            "Extreme backwardation + persistence mispricing = double edge. "
            "Calendar spread profits from term structure normalization AND mispriced decay speed."
        )

    elif p_contained > contained_threshold and iv_rv_score >= 60:
        primary = "Short Call Spread"
        rationale = (
            f"Contained probability ({p_contained:.0%}) exceeds {contained_threshold:.0%} threshold. "
            f"IV overpriced vs expected realized vol (edge score {iv_rv_score:.0f}). "
            "Short call spread monetizes IV collapse with defined risk."
        )

    elif persistence_score < 45 and edge_score > 55:
        primary = "Long Put Spread"
        rationale = (
            "Market correctly pricing vol decay speed (persistence edge minimal), "
            "but overall edge composite favorable. Directional put spread captures "
            "VIX price mean-reversion rather than IV collapse."
        )

    else:
        # Default to highest Sharpe from MC
        if ranked:
            primary = ranked[0].name
            rationale = (
                f"No dominant structural edge. Defaulting to highest Sharpe structure "
                f"from MC simulation ({ranked[0].sharpe:.2f})."
            )
        else:
            primary = "Short Call Spread"
            rationale = "Default recommendation."

    # Position sizing via CVaR
    primary_struct = next((s for s in mc_result.structures if s.name == primary), None)
    position_size = _compute_position_size(
        structure=primary_struct,
        risk_budget=risk_budget_dollars,
        severity_score=severity_score,
        p_escalation=p_escalation,
    )

    return StructureRecommendation(
        primary=primary,
        primary_rationale=rationale,
        ranked=ranked_dicts,
        position_size=position_size,
        guardrails=guardrails,
    )


def _compute_position_size(
    *,
    structure: Optional[StructurePnL],
    risk_budget: float,
    severity_score: float,
    p_escalation: float,
) -> Dict[str, Any]:
    """Size position so that CVaR95 loss stays within risk budget.

    Reduces size for high severity or elevated escalation probability.
    """
    if structure is None or structure.cvar95 <= 0:
        return {
            "contracts": 0,
            "maxLossPerContract": 0,
            "totalMaxLoss": 0,
            "riskBudget": risk_budget,
            "note": "Unable to size: no CVaR data.",
        }

    # Severity scaling: reduce budget for extreme events
    severity_scale = 1.0
    if severity_score > 70:
        severity_scale = 0.5
    elif severity_score > 50:
        severity_scale = 0.7
    elif severity_score > 30:
        severity_scale = 0.85

    # Escalation scaling
    esc_scale = 1.0
    if p_escalation > 0.30:
        esc_scale = 0.5
    elif p_escalation > 0.20:
        esc_scale = 0.7

    adjusted_budget = risk_budget * severity_scale * esc_scale

    # Size so CVaR95 loss <= adjusted budget
    max_loss_per_contract = abs(structure.cvar95)
    if max_loss_per_contract < 1:
        max_loss_per_contract = abs(structure.max_loss) if structure.max_loss < 0 else 100

    contracts = max(1, int(adjusted_budget / max(1, max_loss_per_contract)))
    total_max_loss = contracts * abs(structure.max_loss) if structure.max_loss < 0 else 0

    return {
        "contracts": contracts,
        "maxLossPerContract": round(max_loss_per_contract, 2),
        "totalMaxLoss": round(total_max_loss, 2),
        "riskBudget": round(risk_budget, 2),
        "adjustedBudget": round(adjusted_budget, 2),
        "severityScale": round(severity_scale, 2),
        "escalationScale": round(esc_scale, 2),
        "note": (
            f"Sized to {contracts} contracts. CVaR95 loss per contract: ${max_loss_per_contract:.0f}. "
            f"Risk budget: ${adjusted_budget:.0f} (severity {severity_scale:.0%}, escalation {esc_scale:.0%})."
        ),
    }
