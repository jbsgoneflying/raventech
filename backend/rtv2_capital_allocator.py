"""RTv2.0 — Capital Allocator.

Manages strategy bucket allocation, regime-influenced weighting, and
Risk Unit (RU) tracking.  RU is derived from actual risk (max loss),
not assigned.

Storage:
  - Redis: rtv2:allocation:{date}  TTL 180 days
  - Redis: rtv2:allocation:current (pointer)
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

ALLOCATION_TTL_S = 180 * 86400
ALLOCATION_KEY_PREFIX = "rtv2:allocation"

# ---------------------------------------------------------------------------
# Regime-based allocation tables
# ---------------------------------------------------------------------------

REGIME_ALLOCATIONS: Dict[str, Dict[str, dict]] = {
    "Risk-On": {
        "income_core":   {"pct": 0.60, "max_ru": 12, "max_concurrent": 6},
        "directional":   {"pct": 0.25, "max_ru": 5,  "max_concurrent": 4},
        "opportunistic": {"pct": 0.15, "max_ru": 3,  "max_concurrent": 2},
    },
    "Transitional": {
        "income_core":   {"pct": 0.55, "max_ru": 10, "max_concurrent": 5},
        "directional":   {"pct": 0.25, "max_ru": 5,  "max_concurrent": 3},
        "opportunistic": {"pct": 0.20, "max_ru": 4,  "max_concurrent": 2},
    },
    "Risk-Off": {
        "income_core":   {"pct": 0.40, "max_ru": 8,  "max_concurrent": 3},
        "directional":   {"pct": 0.35, "max_ru": 7,  "max_concurrent": 3},
        "opportunistic": {"pct": 0.25, "max_ru": 5,  "max_concurrent": 2},
    },
    "Stressed": {
        "income_core":   {"pct": 0.20, "max_ru": 4,  "max_concurrent": 2},
        "directional":   {"pct": 0.30, "max_ru": 6,  "max_concurrent": 2},
        "opportunistic": {"pct": 0.10, "max_ru": 2,  "max_concurrent": 1},
        "cash_reserve":  {"pct": 0.40},
    },
}

ENGINE_BUCKET_MAP: Dict[str, str] = {
    "E1": "income_core",
    "E2": "income_core",
    "E3": "directional",
    "E4": "directional",
    "E5": "opportunistic",
    "E7": "directional",
    "E8": "opportunistic",
    "manual": "directional",
}

ENGINE_RU_HARD_CAP: Dict[str, float] = {
    "E1": 2.0,
    "E2": 2.5,
    "E3": 1.5,
    "E4": 1.5,
    "E5": 1.5,
    "E7": 2.0,
    "E8": 2.0,
    "manual": 2.5,
}

PORTFOLIO_RU_HARD_CAP = 15.0
REBALANCE_STEP_PCT = 0.05
CONSECUTIVE_WEEKS_THRESHOLD = 2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class BucketState:
    name: str = ""
    target_pct: float = 0.0
    max_ru: float = 0.0
    max_concurrent: int = 0
    used_ru: float = 0.0
    active_count: int = 0
    adjustment_pct: float = 0.0

    @property
    def available_ru(self) -> float:
        return max(0.0, self.max_ru - self.used_ru)

    @property
    def available_slots(self) -> int:
        return max(0, self.max_concurrent - self.active_count)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["available_ru"] = round(self.available_ru, 2)
        d["available_slots"] = self.available_slots
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "BucketState":
        if not isinstance(d, dict):
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class AllocationSnapshot:
    date: str = ""
    generated_at: str = ""
    regime: str = "Transitional"
    portfolio_ru_cap: float = PORTFOLIO_RU_HARD_CAP
    total_used_ru: float = 0.0
    buckets: Dict[str, dict] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "AllocationSnapshot":
        if not isinstance(d, dict):
            return cls()
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ---------------------------------------------------------------------------
# RU derivation
# ---------------------------------------------------------------------------

def derive_ru(
    *,
    max_loss_per_unit: Optional[float],
    units: int,
    portfolio_capital: float,
    engine_id: str = "",
) -> Dict[str, Any]:
    """Compute derived RU from actual risk.

    Returns dict with derived_ru, capped_ru, units_adjusted, and cap_applied.
    """
    if portfolio_capital <= 0:
        return {"derived_ru": 0.0, "capped_ru": 0.0, "units_adjusted": units, "cap_applied": False}

    one_ru = portfolio_capital * 0.01

    if max_loss_per_unit is None or max_loss_per_unit <= 0:
        return {
            "derived_ru": 1.0,
            "capped_ru": 1.0,
            "units_adjusted": units,
            "cap_applied": False,
            "desk_defined": True,
        }

    raw_ru = (max_loss_per_unit * units) / one_ru
    hard_cap = ENGINE_RU_HARD_CAP.get(engine_id, 2.5)

    if raw_ru <= hard_cap:
        return {
            "derived_ru": round(raw_ru, 3),
            "capped_ru": round(raw_ru, 3),
            "units_adjusted": units,
            "cap_applied": False,
        }

    capped_units = max(1, int(math.floor((hard_cap * one_ru) / max_loss_per_unit)))
    capped_ru = (max_loss_per_unit * capped_units) / one_ru

    return {
        "derived_ru": round(raw_ru, 3),
        "capped_ru": round(min(capped_ru, hard_cap), 3),
        "units_adjusted": capped_units,
        "cap_applied": True,
        "original_units": units,
    }


# ---------------------------------------------------------------------------
# Allocation builder
# ---------------------------------------------------------------------------

def build_allocation(
    *,
    regime_label: str = "Transitional",
    active_trades: Optional[List[dict]] = None,
    weekly_adjustments: Optional[Dict[str, float]] = None,
    portfolio_capital: float = 0.0,
) -> AllocationSnapshot:
    """Build current allocation snapshot from regime and active positions."""

    now = dt.datetime.now(dt.timezone.utc)
    regime = regime_label if regime_label in REGIME_ALLOCATIONS else "Transitional"
    regime_config = REGIME_ALLOCATIONS[regime]

    buckets: Dict[str, BucketState] = {}
    for bname, cfg in regime_config.items():
        if bname == "cash_reserve":
            continue
        buckets[bname] = BucketState(
            name=bname,
            target_pct=cfg.get("pct", 0.0),
            max_ru=cfg.get("max_ru", 0),
            max_concurrent=cfg.get("max_concurrent", 0),
        )

    adj = weekly_adjustments or {}
    for bname, delta in adj.items():
        if bname in buckets:
            clamped = max(-0.15, min(0.15, delta))
            buckets[bname].adjustment_pct = clamped

    total_used = 0.0
    for trade in (active_trades or []):
        bucket_name = trade.get("bucket", "")
        ru = float(trade.get("derived_ru", 0) or trade.get("capped_ru", 0))
        if bucket_name in buckets:
            buckets[bucket_name].used_ru = round(buckets[bucket_name].used_ru + ru, 3)
            buckets[bucket_name].active_count += 1
            total_used += ru

    return AllocationSnapshot(
        date=now.strftime("%Y-%m-%d"),
        generated_at=now.isoformat() + "Z",
        regime=regime,
        portfolio_ru_cap=PORTFOLIO_RU_HARD_CAP,
        total_used_ru=round(total_used, 3),
        buckets={k: v.to_dict() for k, v in buckets.items()},
    )


def compute_weekly_adjustments(
    bucket_streaks: Dict[str, int],
) -> Dict[str, float]:
    """Compute allocation adjustments from consecutive win/loss streaks.

    bucket_streaks: {bucket_name: streak}  positive = wins, negative = losses.
    """
    adj: Dict[str, float] = {}
    for bname, streak in bucket_streaks.items():
        if streak >= CONSECUTIVE_WEEKS_THRESHOLD:
            adj[bname] = REBALANCE_STEP_PCT
        elif streak <= -CONSECUTIVE_WEEKS_THRESHOLD:
            adj[bname] = -REBALANCE_STEP_PCT
    return adj


def bucket_for_engine(engine_id: str) -> str:
    return ENGINE_BUCKET_MAP.get(engine_id, "directional")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_allocation(snap: AllocationSnapshot, store: Any, ttl_s: int = ALLOCATION_TTL_S) -> bool:
    if store is None:
        return False
    key = f"{ALLOCATION_KEY_PREFIX}:{snap.date}"
    ok = store.set_json(key, snap.to_dict(), ttl_s=ttl_s)
    if ok:
        store.set_json(f"{ALLOCATION_KEY_PREFIX}:current", snap.to_dict(), ttl_s=ttl_s)
    return ok


def load_allocation(store: Any) -> Optional[AllocationSnapshot]:
    if store is None:
        return None
    data = store.get_json(f"{ALLOCATION_KEY_PREFIX}:current")
    if data is None:
        return None
    return AllocationSnapshot.from_dict(data)
