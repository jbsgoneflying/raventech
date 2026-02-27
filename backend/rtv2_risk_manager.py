"""RTv2.0 — Risk Manager.

Portfolio-level risk limits, correlation checks, drawdown monitoring.
Uses soft penalties by default; hard blocks only for capacity constraints.

Regime requires multi-layer confirmation before suppression.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

PORTFOLIO_RU_HARD_CAP = 15.0
PER_UNDERLYING_MAX_RU = 2.0
PER_SECTOR_MAX_RU = 4.0
DIRECTIONAL_TILT_THRESHOLD = 0.70
WEEKLY_DRAWDOWN_LIMIT_PCT = 0.02
CONSECUTIVE_LOSS_THRESHOLD = 3
CORRELATION_WARNING_THRESHOLD = 0.70
THESIS_WEAKENING_PENALTY_THRESHOLD = 3

REGIME_RISK_TABLE: Dict[str, Dict[str, Any]] = {
    "Risk-On": {
        "max_portfolio_ru": 15,
        "ups_threshold": None,
        "stop_tightening": 1.0,
        "hard_block_requires": None,
    },
    "Transitional": {
        "max_portfolio_ru": 12,
        "ups_threshold": 55,
        "stop_tightening": 0.90,
        "hard_block_requires": None,
    },
    "Risk-Off": {
        "max_portfolio_ru": 10,
        "ups_threshold": 65,
        "stop_tightening": 0.80,
        "hard_block_requires": ["vol_confirms", "flow_confirms"],
    },
    "Stressed": {
        "max_portfolio_ru": 7,
        "ups_threshold": 75,
        "stop_tightening": 0.70,
        "hard_block_requires": ["vol_backwardation", "flow_risk_off", "e9_above_70"],
    },
}


@dataclass
class RiskCheck:
    """Result of a risk evaluation for a proposed or active trade."""
    passed: bool = True
    hard_blocked: bool = False
    hard_block_reason: str = ""
    penalties: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    adjusted_ups: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class RiskDashboard:
    """Snapshot of portfolio-wide risk metrics."""
    total_ru: float = 0.0
    portfolio_ru_cap: float = PORTFOLIO_RU_HARD_CAP
    ru_utilisation_pct: float = 0.0
    bucket_ru: Dict[str, float] = field(default_factory=dict)
    directional_tilt: str = "neutral"
    long_pct: float = 0.50
    sector_exposure: Dict[str, float] = field(default_factory=dict)
    sector_warnings: List[str] = field(default_factory=list)
    correlation_warnings: List[str] = field(default_factory=list)
    weekly_drawdown_pct: float = 0.0
    drawdown_warning: bool = False
    regime: str = "Transitional"
    regime_max_ru: float = 15.0
    e9_level: float = 0.0
    credit_stress_warning: bool = False
    thesis_weakening_count: int = 0

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RiskDashboard":
        if not isinstance(d, dict):
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# Risk checks for new trade proposals
# ---------------------------------------------------------------------------

def check_trade_risk(
    *,
    ticker: str,
    bucket: str,
    derived_ru: float,
    direction: str = "long",
    sector: str = "",
    ups_score: float = 0.0,
    active_positions: Optional[List[dict]] = None,
    regime: str = "Transitional",
    vol_state: str = "normal",
    flow_label: str = "",
    e9_level: float = 0.0,
    bucket_used_ru: float = 0.0,
    bucket_max_ru: float = 10.0,
    bucket_active_count: int = 0,
    bucket_max_concurrent: int = 5,
    weekly_drawdown_pct: float = 0.0,
    position_states: Optional[Dict[str, int]] = None,
) -> RiskCheck:
    """Run all risk checks for a proposed new trade."""
    positions = active_positions or []
    p_states = position_states or {}
    result = RiskCheck()

    total_ru = sum(float(p.get("derived_ru", 0)) for p in positions) + derived_ru
    regime_cfg = REGIME_RISK_TABLE.get(regime, REGIME_RISK_TABLE["Transitional"])

    # --- Hard blocks (capacity only) ---
    if bucket_used_ru + derived_ru > bucket_max_ru:
        result.hard_blocked = True
        result.hard_block_reason = f"Bucket '{bucket}' RU budget exhausted ({bucket_used_ru:.1f}/{bucket_max_ru:.1f})"
        result.passed = False
        return result

    if total_ru > PORTFOLIO_RU_HARD_CAP:
        result.hard_blocked = True
        result.hard_block_reason = f"Portfolio RU cap would be exceeded ({total_ru:.1f}/{PORTFOLIO_RU_HARD_CAP})"
        result.passed = False
        return result

    if bucket_active_count >= bucket_max_concurrent:
        result.hard_blocked = True
        result.hard_block_reason = f"Bucket '{bucket}' max concurrent reached ({bucket_active_count}/{bucket_max_concurrent})"
        result.passed = False
        return result

    # --- Regime-based multi-layer hard block ---
    if regime in ("Risk-Off", "Stressed"):
        requirements = regime_cfg.get("hard_block_requires") or []
        confirmations = 0
        if vol_state.lower() in ("backwardation", "expanding"):
            confirmations += 1
        if "risk-off" in flow_label.lower() or "stress" in flow_label.lower():
            confirmations += 1
        if e9_level > 70:
            confirmations += 1

        needed = 2 if regime == "Stressed" else 1
        if confirmations >= needed:
            result.hard_blocked = True
            result.hard_block_reason = (
                f"Multi-layer confirmation: regime={regime}, vol={vol_state}, "
                f"flow={flow_label}, E9={e9_level:.0f}"
            )
            result.passed = False
            return result

    # --- Soft penalties ---

    # per-underlying concentration
    underlying_ru = sum(
        float(p.get("derived_ru", 0))
        for p in positions if p.get("ticker") == ticker
    ) + derived_ru
    if underlying_ru > PER_UNDERLYING_MAX_RU:
        result.penalties.append("same_underlying_overlap")
        result.warnings.append(f"Underlying {ticker} would have {underlying_ru:.1f} RU (max {PER_UNDERLYING_MAX_RU})")

    # sector concentration
    if sector:
        sector_ru = sum(
            float(p.get("derived_ru", 0))
            for p in positions if p.get("sector") == sector
        ) + derived_ru
        if sector_ru > PER_SECTOR_MAX_RU:
            result.warnings.append(f"Sector {sector} would have {sector_ru:.1f} RU (max {PER_SECTOR_MAX_RU})")

    # directional tilt
    dir_lower = direction.lower()
    is_long = dir_lower in ("long", "bullish", "bull")
    same_dir_ru = sum(
        float(p.get("derived_ru", 0))
        for p in positions
        if (str(p.get("direction", "")).lower() in ("long", "bullish", "bull")) == is_long
    ) + derived_ru
    total_dir_ru = sum(float(p.get("derived_ru", 0)) for p in positions) + derived_ru
    if total_dir_ru > 0 and same_dir_ru / total_dir_ru > DIRECTIONAL_TILT_THRESHOLD:
        result.penalties.append("directional_tilt_excess")
        result.warnings.append(f"Directional tilt > {DIRECTIONAL_TILT_THRESHOLD:.0%}")

    # regime UPS threshold (soft)
    ups_thresh = regime_cfg.get("ups_threshold")
    if ups_thresh and ups_score < ups_thresh:
        result.warnings.append(
            f"UPS {ups_score:.1f} below regime threshold {ups_thresh} (soft — not blocked)"
        )

    # weekly drawdown
    if weekly_drawdown_pct > WEEKLY_DRAWDOWN_LIMIT_PCT:
        result.warnings.append(f"Weekly drawdown {weekly_drawdown_pct:.2%} exceeds {WEEKLY_DRAWDOWN_LIMIT_PCT:.0%}")

    # thesis weakening in bucket
    tw_count = p_states.get("THESIS_WEAKENING", 0)
    if tw_count >= THESIS_WEAKENING_PENALTY_THRESHOLD:
        result.penalties.append("thesis_weakening_bucket")
        result.warnings.append(f"{tw_count} positions currently THESIS_WEAKENING")

    return result


# ---------------------------------------------------------------------------
# Dashboard builder
# ---------------------------------------------------------------------------

def build_risk_dashboard(
    *,
    active_positions: Optional[List[dict]] = None,
    regime: str = "Transitional",
    e9_level: float = 0.0,
    weekly_pnl: float = 0.0,
    portfolio_capital: float = 0.0,
) -> RiskDashboard:
    """Build the risk dashboard snapshot."""
    positions = active_positions or []

    total_ru = sum(float(p.get("derived_ru", 0)) for p in positions)
    regime_cfg = REGIME_RISK_TABLE.get(regime, REGIME_RISK_TABLE["Transitional"])
    regime_max = regime_cfg["max_portfolio_ru"]

    # bucket RU
    bucket_ru: Dict[str, float] = {}
    for p in positions:
        b = str(p.get("bucket", ""))
        bucket_ru[b] = round(bucket_ru.get(b, 0.0) + float(p.get("derived_ru", 0)), 3)

    # directional tilt
    long_ru = sum(
        float(p.get("derived_ru", 0))
        for p in positions
        if str(p.get("direction", "")).lower() in ("long", "bullish", "bull")
    )
    short_ru = sum(
        float(p.get("derived_ru", 0))
        for p in positions
        if str(p.get("direction", "")).lower() in ("short", "bearish", "bear")
    )
    total_dir = long_ru + short_ru
    long_pct = long_ru / total_dir if total_dir > 0 else 0.50
    if long_pct >= 0.70:
        tilt = "long_heavy"
    elif long_pct <= 0.30:
        tilt = "short_heavy"
    else:
        tilt = "neutral"

    # sector exposure
    sectors: Dict[str, float] = {}
    sector_warns: List[str] = []
    for p in positions:
        s = str(p.get("sector", "Unknown"))
        sectors[s] = round(sectors.get(s, 0.0) + float(p.get("derived_ru", 0)), 3)
    for s, ru in sectors.items():
        if ru > PER_SECTOR_MAX_RU:
            sector_warns.append(f"{s}: {ru:.1f} RU (max {PER_SECTOR_MAX_RU})")

    # drawdown
    dd_pct = abs(weekly_pnl / portfolio_capital) if portfolio_capital > 0 and weekly_pnl < 0 else 0

    # thesis weakening count
    tw = sum(1 for p in positions if str(p.get("position_state", "")).upper() == "THESIS_WEAKENING")

    return RiskDashboard(
        total_ru=round(total_ru, 3),
        portfolio_ru_cap=PORTFOLIO_RU_HARD_CAP,
        ru_utilisation_pct=round(total_ru / PORTFOLIO_RU_HARD_CAP * 100, 1),
        bucket_ru=bucket_ru,
        directional_tilt=tilt,
        long_pct=round(long_pct, 2),
        sector_exposure=sectors,
        sector_warnings=sector_warns,
        weekly_drawdown_pct=round(dd_pct, 4),
        drawdown_warning=dd_pct > WEEKLY_DRAWDOWN_LIMIT_PCT,
        regime=regime,
        regime_max_ru=regime_max,
        e9_level=round(e9_level, 1),
        credit_stress_warning=e9_level > 70,
        thesis_weakening_count=tw,
    )
