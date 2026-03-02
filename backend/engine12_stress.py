"""Engine 12 — Geopolitical Cross-Asset Stress Composite.

Computes a shock-specific stress score from z-scored 3-day changes of:
  - Oil (USO.US)         — direct geopolitical proxy
  - Gold (GLD.US)        — flight-to-safety bid
  - HYG (HYG.US)         — credit spread proxy (CDX substitute)
  - DXY (UUP.US)         — dollar strength / risk-off
  - TLT (TLT.US) 10d RV — rates vol proxy (MOVE substitute)

All instruments available via EODHD with no new API integrations.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

STRESS_SYMBOLS = {
    "oil":     "USO.US",
    "gold":    "GLD.US",
    "hyg":     "HYG.US",
    "dxy":     "UUP.US",
    "tlt":     "TLT.US",
}

# Stress direction: positive means "price going up = stress"
STRESS_DIRECTION = {
    "oil":   "up",       # oil spike = supply disruption fear
    "gold":  "up",       # gold bid = fear
    "hyg":   "down",     # HYG decline = credit stress widening
    "dxy":   "up",       # dollar strength = global risk-off
    "tlt":   "rv_up",    # TLT realized vol increase = rates stress
}


@dataclass
class AssetStressDetail:
    key: str = ""
    symbol: str = ""
    change_3d_pct: float = 0.0
    z_score: float = 0.0
    stress_contribution: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "key": self.key,
            "symbol": self.symbol,
            "change3dPct": round(self.change_3d_pct, 4),
            "zScore": round(self.z_score, 2),
            "stressContribution": round(self.stress_contribution, 2),
        }


@dataclass
class GeoStressComposite:
    score: float = 50.0
    label: str = "Neutral"
    details: List[AssetStressDetail] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "score": round(self.score, 1),
            "label": self.label,
            "details": [d.to_dict() for d in self.details],
        }


def _pct_change(series: List[float], lag: int) -> Optional[float]:
    if len(series) < lag + 1:
        return None
    current = series[-1]
    prior = series[-(lag + 1)]
    if prior == 0 or not math.isfinite(prior) or not math.isfinite(current):
        return None
    return (current - prior) / abs(prior) * 100.0


def _z_score(value: float, series: List[float], window: int = 60) -> float:
    """Z-score of value relative to rolling history of changes."""
    if len(series) < window + 1:
        return 0.0
    changes = []
    for i in range(max(1, len(series) - window), len(series)):
        if series[i - 1] != 0 and math.isfinite(series[i]) and math.isfinite(series[i - 1]):
            changes.append((series[i] - series[i - 1]) / abs(series[i - 1]) * 100.0)
    if len(changes) < 10:
        return 0.0
    mean = sum(changes) / len(changes)
    var = sum((c - mean) ** 2 for c in changes) / max(1, len(changes) - 1)
    std = math.sqrt(var) if var > 0 else 1e-6
    return (value - mean) / std


def _realized_vol(closes: List[float], window: int = 10) -> Optional[float]:
    """Annualized realized vol from daily log returns."""
    if len(closes) < window + 1:
        return None
    returns = []
    start = len(closes) - window - 1
    for i in range(start + 1, len(closes)):
        if closes[i] > 0 and closes[i - 1] > 0:
            returns.append(math.log(closes[i] / closes[i - 1]))
    if len(returns) < 5:
        return None
    mean = sum(returns) / len(returns)
    var = sum((r - mean) ** 2 for r in returns) / max(1, len(returns) - 1)
    return math.sqrt(var) * math.sqrt(252.0) * 100.0


def compute_geopolitical_stress(
    *,
    oil_closes: List[float],
    gold_closes: List[float],
    hyg_closes: List[float],
    dxy_closes: List[float],
    tlt_closes: List[float],
    weights: Optional[Dict[str, float]] = None,
) -> GeoStressComposite:
    """Compute geopolitical-specific cross-asset stress composite.

    Args:
        *_closes: Daily close price series (oldest first, most recent last).
                  Need at least 60 bars for robust z-scoring.
        weights: Override weight dict with keys oil, gold, hyg, dxy, tlt_vol.
    """
    w = weights or {}
    w_oil = w.get("oil", 0.30)
    w_gold = w.get("gold", 0.20)
    w_hyg = w.get("hyg", 0.20)
    w_dxy = w.get("dxy", 0.15)
    w_tlt_vol = w.get("tlt_vol", 0.15)

    details: List[AssetStressDetail] = []
    weighted_z_sum = 0.0
    weight_sum = 0.0

    for key, closes, weight, direction in [
        ("oil", oil_closes, w_oil, "up"),
        ("gold", gold_closes, w_gold, "up"),
        ("hyg", hyg_closes, w_hyg, "down"),
        ("dxy", dxy_closes, w_dxy, "up"),
    ]:
        change = _pct_change(closes, 3)
        if change is None:
            details.append(AssetStressDetail(key=key, symbol=STRESS_SYMBOLS.get(key, "")))
            continue

        z = _z_score(change, closes, window=60)

        # Flip sign for "down" stress direction so positive z = stress
        stress_z = z if direction == "up" else -z

        contribution = stress_z * weight
        weighted_z_sum += contribution
        weight_sum += weight

        details.append(AssetStressDetail(
            key=key,
            symbol=STRESS_SYMBOLS.get(key, ""),
            change_3d_pct=change,
            z_score=z,
            stress_contribution=contribution,
        ))

    # TLT realized vol (MOVE proxy)
    tlt_rv = _realized_vol(tlt_closes, window=10)
    if tlt_rv is not None and len(tlt_closes) >= 60:
        # Compute z-score of current 10d RV vs rolling 60d of 10d RV samples
        rv_series = []
        for end in range(20, len(tlt_closes)):
            rv_i = _realized_vol(tlt_closes[:end + 1], window=10)
            if rv_i is not None:
                rv_series.append(rv_i)
        if len(rv_series) >= 10:
            mean_rv = sum(rv_series) / len(rv_series)
            var_rv = sum((r - mean_rv) ** 2 for r in rv_series) / max(1, len(rv_series) - 1)
            std_rv = math.sqrt(var_rv) if var_rv > 0 else 1e-6
            tlt_z = (tlt_rv - mean_rv) / std_rv
        else:
            tlt_z = 0.0
        contribution = tlt_z * w_tlt_vol
        weighted_z_sum += contribution
        weight_sum += w_tlt_vol
        details.append(AssetStressDetail(
            key="tlt_vol",
            symbol=STRESS_SYMBOLS["tlt"],
            change_3d_pct=tlt_rv,
            z_score=tlt_z,
            stress_contribution=contribution,
        ))
    else:
        details.append(AssetStressDetail(key="tlt_vol", symbol=STRESS_SYMBOLS["tlt"]))

    if weight_sum > 0:
        raw_composite = weighted_z_sum / weight_sum
    else:
        raw_composite = 0.0

    # Map composite z-score to 0-100 scale
    # z=0 -> 50, z=2 -> ~85, z=-2 -> ~15
    score = 50.0 + raw_composite * 17.5
    score = max(0.0, min(100.0, score))

    if score >= 75:
        label = "Extreme Stress"
    elif score >= 60:
        label = "Elevated"
    elif score >= 40:
        label = "Neutral"
    elif score >= 25:
        label = "Calm"
    else:
        label = "Very Calm"

    return GeoStressComposite(score=score, label=label, details=details)
