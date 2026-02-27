"""RTv2.0 — Portfolio State.

Canonical snapshot of the current portfolio: active positions, bucket
utilisation, position health summary, and risk metrics.  Analogous to
DailyMarketState but for positions.

Storage:
  - Redis: rtv2:portfolio:{date}  TTL 180 days
  - Redis: rtv2:portfolio:current (pointer)
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

PORTFOLIO_TTL_S = 180 * 86400
PORTFOLIO_KEY_PREFIX = "rtv2:portfolio"


@dataclass
class PositionHealthSummary:
    on_track: int = 0
    near_target: int = 0
    risk_increasing: int = 0
    thesis_weakening: int = 0
    invalidated: int = 0

    @property
    def total(self) -> int:
        return (self.on_track + self.near_target + self.risk_increasing
                + self.thesis_weakening + self.invalidated)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["total"] = self.total
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "PositionHealthSummary":
        if not isinstance(d, dict):
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class PortfolioSnapshot:
    date: str = ""
    generated_at: str = ""
    allocation: dict = field(default_factory=dict)
    active_positions: List[dict] = field(default_factory=list)
    position_health: dict = field(default_factory=dict)
    total_used_ru: float = 0.0
    portfolio_ru_cap: float = 15.0
    directional_tilt: str = "neutral"
    sector_exposure: Dict[str, float] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "PortfolioSnapshot":
        if not isinstance(d, dict):
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


def compute_directional_tilt(positions: List[dict]) -> str:
    """Compute net directional tilt from active positions."""
    if not positions:
        return "neutral"
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
    total = long_ru + short_ru
    if total == 0:
        return "neutral"
    long_pct = long_ru / total
    if long_pct >= 0.70:
        return "long_heavy"
    if long_pct <= 0.30:
        return "short_heavy"
    return "neutral"


def compute_sector_exposure(positions: List[dict]) -> Dict[str, float]:
    """Aggregate RU exposure by GICS sector."""
    sectors: Dict[str, float] = {}
    for p in positions:
        sector = str(p.get("sector", "Unknown"))
        ru = float(p.get("derived_ru", 0))
        sectors[sector] = round(sectors.get(sector, 0.0) + ru, 3)
    return sectors


def build_health_summary(positions: List[dict]) -> PositionHealthSummary:
    """Count positions by PIL state."""
    s = PositionHealthSummary()
    for p in positions:
        state = str(p.get("position_state", "")).upper()
        if state == "ON_TRACK":
            s.on_track += 1
        elif state == "NEAR_TARGET":
            s.near_target += 1
        elif state == "RISK_INCREASING":
            s.risk_increasing += 1
        elif state == "THESIS_WEAKENING":
            s.thesis_weakening += 1
        elif state == "INVALIDATED":
            s.invalidated += 1
    return s


def build_portfolio_snapshot(
    *,
    allocation: Optional[dict] = None,
    active_positions: Optional[List[dict]] = None,
) -> PortfolioSnapshot:
    """Build the canonical portfolio snapshot."""
    now = dt.datetime.now(dt.timezone.utc)
    positions = active_positions or []

    total_ru = sum(float(p.get("derived_ru", 0)) for p in positions)
    tilt = compute_directional_tilt(positions)
    sectors = compute_sector_exposure(positions)
    health = build_health_summary(positions)

    return PortfolioSnapshot(
        date=now.strftime("%Y-%m-%d"),
        generated_at=now.isoformat() + "Z",
        allocation=allocation or {},
        active_positions=positions,
        position_health=health.to_dict(),
        total_used_ru=round(total_ru, 3),
        portfolio_ru_cap=15.0,
        directional_tilt=tilt,
        sector_exposure=sectors,
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_portfolio(snap: PortfolioSnapshot, store: Any, ttl_s: int = PORTFOLIO_TTL_S) -> bool:
    if store is None:
        return False
    key = f"{PORTFOLIO_KEY_PREFIX}:{snap.date}"
    ok = store.set_json(key, snap.to_dict(), ttl_s=ttl_s)
    if ok:
        store.set_json(f"{PORTFOLIO_KEY_PREFIX}:current", snap.to_dict(), ttl_s=ttl_s)
    return ok


def load_portfolio(store: Any) -> Optional[PortfolioSnapshot]:
    if store is None:
        return None
    data = store.get_json(f"{PORTFOLIO_KEY_PREFIX}:current")
    if data is None:
        return None
    return PortfolioSnapshot.from_dict(data)
