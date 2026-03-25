"""Engine 2 — Trade Persistence (Redis).

Redis key layout (mirrors Engine 12 pattern):
    e2:trades:{trade_id}    — individual trade JSON document
    e2:trades:index         — ordered list of trade IDs (newest last)
"""

from __future__ import annotations

import datetime as dt
import logging
import uuid
from typing import Any, Dict, List, Optional

from backend.config import FeatureFlags, get_flags
from backend.redis_store import RedisStore, get_store_optional

LOG = logging.getLogger(__name__)

_TRADE_KEY_PREFIX = "e2:trades:"
_TRADE_INDEX_KEY = "e2:trades:index"


def _trade_ttl(flags: Optional[FeatureFlags] = None) -> int:
    f = flags or get_flags()
    return int(f.ENGINE2_TRADE_TTL_S)


def _trade_max_index(flags: Optional[FeatureFlags] = None) -> int:
    f = flags or get_flags()
    return int(f.ENGINE2_TRADE_MAX_INDEX)


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _generate_trade_id(underlying: str = "SPX") -> str:
    today = dt.date.today().strftime("%Y%m%d")
    short_uuid = uuid.uuid4().hex[:8]
    return f"e2-{today}-{underlying}-{short_uuid}"


def log_trade(
    trade_data: Dict[str, Any],
    store: Optional[RedisStore] = None,
    flags: Optional[FeatureFlags] = None,
) -> Optional[str]:
    """Persist a new trade to Redis. Returns trade_id or None on failure."""
    s = store or get_store_optional()
    if s is None:
        LOG.warning("engine2_trades.log_trade: Redis unavailable")
        return None

    underlying = str(trade_data.get("entry", {}).get("underlying", "SPX"))
    trade_id = _generate_trade_id(underlying)
    ttl = _trade_ttl(flags)
    max_idx = _trade_max_index(flags)

    trade = {
        "tradeId": trade_id,
        "status": "active",
        "loggedAt": _utcnow_iso(),
        "source": trade_data.get("source", "advisor"),
        "entry": trade_data.get("entry", {}),
        "entryContext": trade_data.get("entryContext", {}),
        "advisorVerdict": trade_data.get("advisorVerdict"),
        "checkIns": [],
        "closedAt": None,
        "closeReason": None,
        "outcome": None,
    }

    if not s.set_json(f"{_TRADE_KEY_PREFIX}{trade_id}", trade, ttl_s=ttl):
        LOG.error("engine2_trades.log_trade: failed to write trade %s", trade_id)
        return None

    index: list = s.get_json(_TRADE_INDEX_KEY) or []
    index.append(trade_id)
    index = index[-max_idx:]
    s.set_json(_TRADE_INDEX_KEY, index, ttl_s=ttl)

    LOG.info("engine2_trades: logged trade %s", trade_id)
    return trade_id


def _load_trade(trade_id: str, store: RedisStore) -> Optional[Dict[str, Any]]:
    return store.get_json(f"{_TRADE_KEY_PREFIX}{trade_id}")


def list_active_trades(
    store: Optional[RedisStore] = None,
) -> List[Dict[str, Any]]:
    """Return all active trades (not closed)."""
    s = store or get_store_optional()
    if s is None:
        return []

    index: list = s.get_json(_TRADE_INDEX_KEY) or []
    trades: List[Dict[str, Any]] = []
    for tid in index:
        t = _load_trade(str(tid), s)
        if t and t.get("status") in ("active", "monitoring"):
            trades.append(t)
    return trades


def get_trade(
    trade_id: str,
    store: Optional[RedisStore] = None,
) -> Optional[Dict[str, Any]]:
    """Load a single trade by ID."""
    s = store or get_store_optional()
    if s is None:
        return None
    return _load_trade(trade_id, s)


def close_trade(
    trade_id: str,
    close_data: Optional[Dict[str, Any]] = None,
    store: Optional[RedisStore] = None,
    flags: Optional[FeatureFlags] = None,
) -> Optional[Dict[str, Any]]:
    """Close an active trade. Returns updated trade or None."""
    s = store or get_store_optional()
    if s is None:
        return None

    trade = _load_trade(trade_id, s)
    if trade is None:
        return None

    cd = close_data or {}
    trade["status"] = "closed"
    trade["closedAt"] = _utcnow_iso()
    trade["closeReason"] = cd.get("reason", "manual")
    trade["outcome"] = cd.get("outcome")

    ttl = _trade_ttl(flags)
    s.set_json(f"{_TRADE_KEY_PREFIX}{trade_id}", trade, ttl_s=ttl)
    LOG.info("engine2_trades: closed trade %s reason=%s", trade_id, trade["closeReason"])
    return trade


def add_checkin(
    trade_id: str,
    checkin_data: Dict[str, Any],
    store: Optional[RedisStore] = None,
    flags: Optional[FeatureFlags] = None,
) -> Optional[Dict[str, Any]]:
    """Append a check-in record to a trade. Returns updated trade or None."""
    s = store or get_store_optional()
    if s is None:
        return None

    trade = _load_trade(trade_id, s)
    if trade is None:
        return None

    checkin = {
        "timestamp": _utcnow_iso(),
        **checkin_data,
    }

    checkins = trade.get("checkIns") or []
    checkins.append(checkin)
    trade["checkIns"] = checkins[-20:]

    if checkin_data.get("status") in ("adjust", "exit"):
        trade["status"] = "monitoring"

    ttl = _trade_ttl(flags)
    s.set_json(f"{_TRADE_KEY_PREFIX}{trade_id}", trade, ttl_s=ttl)
    LOG.info("engine2_trades: check-in for %s status=%s", trade_id, checkin_data.get("status"))
    return trade
