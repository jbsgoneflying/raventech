"""RTv2.0 — Trade Lifecycle Manager.

Flexible state model: trades can skip stages, revert, or take
non-linear paths.  Every transition is logged with timestamp and
reason.

Storage:
  - Redis: rtv2:trades:{id}         individual trade (TTL 90 days)
  - Redis: rtv2:trades:active       set of active trade IDs
  - Redis: rtv2:trades:history      rolling index of closed trades
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

TRADE_TTL_S = 90 * 86400
HISTORY_TTL_S = 180 * 86400
TRADE_KEY_PREFIX = "rtv2:trades"
ACTIVE_SET_KEY = "rtv2:trades:active"
HISTORY_KEY = "rtv2:trades:history"

VALID_STATES = {
    "SOURCED", "QUEUED", "STAGED", "ACTIVE", "EXTENDING",
    "CLOSING", "CLOSED", "EXPIRED", "WITHDRAWN",
}

TERMINAL_STATES = {"CLOSED", "EXPIRED", "WITHDRAWN"}

KNOWN_TRANSITIONS = {
    ("SOURCED", "QUEUED"), ("SOURCED", "STAGED"), ("SOURCED", "ACTIVE"),
    ("QUEUED", "STAGED"), ("QUEUED", "ACTIVE"), ("QUEUED", "EXPIRED"),
    ("QUEUED", "SOURCED"),
    ("STAGED", "ACTIVE"), ("STAGED", "QUEUED"), ("STAGED", "WITHDRAWN"),
    ("ACTIVE", "EXTENDING"), ("ACTIVE", "CLOSING"), ("ACTIVE", "CLOSED"),
    ("EXTENDING", "ACTIVE"), ("EXTENDING", "CLOSING"),
    ("CLOSING", "CLOSED"),
}

QUEUE_MAX_AGE_DAYS = 3
STAGED_MAX_AGE_DAYS = 1

ENGINE_THESIS_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "E1": {
        "trade_type": "premium_decay",
        "thesis_max_days": 5,
    },
    "E2": {
        "trade_type": "premium_decay",
        "thesis_max_days": 5,
    },
    "E3": {
        "trade_type": "mean_reversion",
        "thesis_max_days": 7,
    },
    "E4": {
        "trade_type": "trend_continuation",
        "thesis_max_days": 10,
    },
    "E5": {
        "trade_type": "varies",
        "thesis_max_days": 7,
    },
    "E7": {
        "trade_type": "relative_value",
        "thesis_max_days": 6,
    },
    "E8": {
        "trade_type": "event_continuation",
        "thesis_max_days": 3,
    },
}


@dataclass
class TransitionRecord:
    from_state: str = ""
    to_state: str = ""
    timestamp: str = ""
    reason: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class TradeRecord:
    trade_id: str = ""
    ticker: str = ""
    engine_source: str = ""
    bucket: str = ""
    lifecycle_state: str = "SOURCED"

    # scoring
    ups_score: float = 0.0
    raw_engine_score: float = 0.0

    # entry details (populated on ACTIVE)
    entry_price: float = 0.0
    entry_date: str = ""
    direction: str = ""
    derived_ru: float = 0.0
    capped_ru: float = 0.0
    max_loss_per_unit: float = 0.0
    units: int = 0
    sector: str = ""

    # thesis profile (populated on ACTIVE)
    trade_type: str = ""
    thesis_target: float = 0.0
    thesis_stop: float = 0.0
    thesis_max_days: int = 0
    invalidation_conditions: List[str] = field(default_factory=list)

    # PIL monitoring (populated by position monitor)
    position_state: str = ""
    suggested_action: str = ""
    current_pnl_pct: float = 0.0
    days_in_trade: int = 0
    state_reason: str = ""
    last_evaluated: str = ""

    # meta
    created_at: str = ""
    updated_at: str = ""
    transitions: List[dict] = field(default_factory=list)
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TradeRecord":
        if not isinstance(d, dict):
            return cls()
        flds = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in d.items() if k in flds})


# ---------------------------------------------------------------------------
# Lifecycle operations
# ---------------------------------------------------------------------------

def generate_trade_id() -> str:
    return f"rtv2-{uuid.uuid4().hex[:12]}"


def create_trade(
    *,
    ticker: str,
    engine_source: str,
    bucket: str,
    raw_engine_score: float = 0.0,
    ups_score: float = 0.0,
    direction: str = "",
    sector: str = "",
    max_loss_per_unit: float = 0.0,
    units: int = 0,
    derived_ru: float = 0.0,
    notes: str = "",
    initial_state: str = "SOURCED",
) -> TradeRecord:
    """Create a new trade record in the specified initial state."""
    now = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
    tid = generate_trade_id()

    tr = TradeRecord(
        trade_id=tid,
        ticker=ticker,
        engine_source=engine_source,
        bucket=bucket,
        lifecycle_state=initial_state,
        ups_score=ups_score,
        raw_engine_score=raw_engine_score,
        direction=direction,
        sector=sector,
        max_loss_per_unit=max_loss_per_unit,
        units=units,
        derived_ru=derived_ru,
        created_at=now,
        updated_at=now,
        notes=notes,
    )

    tr.transitions.append(TransitionRecord(
        from_state="",
        to_state=initial_state,
        timestamp=now,
        reason="Trade created",
    ).to_dict())

    return tr


def transition(
    trade: TradeRecord,
    to_state: str,
    reason: str = "",
) -> TradeRecord:
    """Transition a trade to a new state, recording the change."""
    if to_state not in VALID_STATES:
        raise ValueError(f"Invalid state: {to_state}")

    if trade.lifecycle_state in TERMINAL_STATES:
        raise ValueError(f"Cannot transition from terminal state {trade.lifecycle_state}")

    pair = (trade.lifecycle_state, to_state)
    if pair not in KNOWN_TRANSITIONS:
        LOG.warning("Non-standard transition %s → %s (allowed with reason)", trade.lifecycle_state, to_state)
        if not reason:
            reason = f"Non-standard transition from {trade.lifecycle_state}"

    now = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
    trade.transitions.append(TransitionRecord(
        from_state=trade.lifecycle_state,
        to_state=to_state,
        timestamp=now,
        reason=reason or f"Transitioned to {to_state}",
    ).to_dict())

    trade.lifecycle_state = to_state
    trade.updated_at = now
    return trade


def activate_trade(
    trade: TradeRecord,
    *,
    entry_price: float,
    units: int,
    direction: str = "",
    max_loss_per_unit: float = 0.0,
    derived_ru: float = 0.0,
    thesis_target: float = 0.0,
    thesis_stop: float = 0.0,
    thesis_max_days: int = 0,
    invalidation_conditions: Optional[List[str]] = None,
    trade_type: str = "",
) -> TradeRecord:
    """Move a trade to ACTIVE and populate entry + thesis fields."""
    now_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")

    trade.entry_price = entry_price
    trade.entry_date = now_str
    trade.units = units
    trade.direction = direction or trade.direction
    trade.max_loss_per_unit = max_loss_per_unit
    trade.derived_ru = derived_ru

    defaults = ENGINE_THESIS_DEFAULTS.get(trade.engine_source, {})
    trade.trade_type = trade_type or defaults.get("trade_type", "")
    trade.thesis_target = thesis_target
    trade.thesis_stop = thesis_stop
    trade.thesis_max_days = thesis_max_days or defaults.get("thesis_max_days", 5)
    trade.invalidation_conditions = invalidation_conditions or []

    transition(trade, "ACTIVE", reason="Trade activated with entry details")
    return trade


def create_manual_trade(
    *,
    ticker: str,
    direction: str,
    entry_price: float,
    units: int,
    trade_type: str,
    thesis_stop: float,
    thesis_target: float,
    thesis_max_days: int,
    invalidation_conditions: List[str],
    bucket: str,
    sector: str = "",
    max_loss_per_unit: float = 0.0,
    derived_ru: float = 0.0,
    notes: str = "",
) -> TradeRecord:
    """Create a manual trade that starts directly in ACTIVE state."""
    now_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
    tid = generate_trade_id()

    if max_loss_per_unit <= 0 and thesis_stop > 0 and entry_price > 0:
        max_loss_per_unit = abs(entry_price - thesis_stop)

    tr = TradeRecord(
        trade_id=tid,
        ticker=ticker,
        engine_source="manual",
        bucket=bucket,
        lifecycle_state="ACTIVE",
        direction=direction,
        sector=sector,
        entry_price=entry_price,
        entry_date=now_str,
        units=units,
        max_loss_per_unit=max_loss_per_unit,
        derived_ru=derived_ru,
        trade_type=trade_type,
        thesis_target=thesis_target,
        thesis_stop=thesis_stop,
        thesis_max_days=thesis_max_days,
        invalidation_conditions=invalidation_conditions,
        created_at=now_iso,
        updated_at=now_iso,
        notes=notes,
    )

    tr.transitions.append(TransitionRecord(
        from_state="",
        to_state="ACTIVE",
        timestamp=now_iso,
        reason="Manual trade entry — starts ACTIVE",
    ).to_dict())

    return tr


# ---------------------------------------------------------------------------
# Auto-expiration
# ---------------------------------------------------------------------------

def check_expirations(trades: List[TradeRecord]) -> List[TradeRecord]:
    """Auto-expire QUEUED (>3d) and revert STAGED (>1d) trades."""
    today = dt.date.today()
    expired = []

    for tr in trades:
        if tr.lifecycle_state == "QUEUED" and tr.created_at:
            try:
                created = dt.date.fromisoformat(tr.created_at[:10])
                if (today - created).days > QUEUE_MAX_AGE_DAYS:
                    transition(tr, "EXPIRED", reason=f"QUEUED > {QUEUE_MAX_AGE_DAYS} days")
                    expired.append(tr)
            except (ValueError, TypeError):
                pass

        elif tr.lifecycle_state == "STAGED" and tr.updated_at:
            try:
                updated = dt.date.fromisoformat(tr.updated_at[:10])
                if (today - updated).days > STAGED_MAX_AGE_DAYS:
                    transition(tr, "QUEUED", reason=f"STAGED > {STAGED_MAX_AGE_DAYS} day — returned to queue")
                    expired.append(tr)
            except (ValueError, TypeError):
                pass

    return expired


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_trade(trade: TradeRecord, store: Any) -> bool:
    if store is None:
        return False
    key = f"{TRADE_KEY_PREFIX}:{trade.trade_id}"
    ok = store.set_json(key, trade.to_dict(), ttl_s=TRADE_TTL_S)
    if not ok:
        return False

    if trade.lifecycle_state in TERMINAL_STATES:
        _remove_from_active(trade.trade_id, store)
        _add_to_history(trade.trade_id, store)
    else:
        _add_to_active(trade.trade_id, store)
    return True


def load_trade(trade_id: str, store: Any) -> Optional[TradeRecord]:
    if store is None:
        return None
    data = store.get_json(f"{TRADE_KEY_PREFIX}:{trade_id}")
    if data is None:
        return None
    return TradeRecord.from_dict(data)


def load_active_trades(store: Any) -> List[TradeRecord]:
    if store is None:
        return []
    ids = store.get_json(ACTIVE_SET_KEY)
    if not isinstance(ids, list):
        return []
    trades = []
    for tid in ids:
        tr = load_trade(str(tid), store)
        if tr is not None and tr.lifecycle_state not in TERMINAL_STATES:
            trades.append(tr)
    return trades


def _add_to_active(trade_id: str, store: Any) -> None:
    ids = store.get_json(ACTIVE_SET_KEY) or []
    if trade_id not in ids:
        ids.append(trade_id)
    store.set_json(ACTIVE_SET_KEY, ids, ttl_s=TRADE_TTL_S)


def _remove_from_active(trade_id: str, store: Any) -> None:
    ids = store.get_json(ACTIVE_SET_KEY) or []
    ids = [i for i in ids if i != trade_id]
    store.set_json(ACTIVE_SET_KEY, ids, ttl_s=TRADE_TTL_S)


def _add_to_history(trade_id: str, store: Any) -> None:
    hist = store.get_json(HISTORY_KEY) or []
    hist.append(trade_id)
    hist = hist[-1000:]  # keep last 1000
    store.set_json(HISTORY_KEY, hist, ttl_s=HISTORY_TTL_S)
