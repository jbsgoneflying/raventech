"""Engine 10 — Multi-Ticker Portfolio Session Persistence (Redis).

Redis key layout:
    e10:sessions:{session_id}   — individual session JSON document
    e10:sessions:index          — ordered list of session IDs (newest last)

Tracks portfolio-level outcomes so the LLM can learn whether concentrated
vs diversified sessions, sector overlap patterns, and regime-based sizing
produced positive results.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import statistics
import uuid
from typing import Any, Dict, List, Optional

from backend.config import get_flags, FeatureFlags
from backend.redis_store import RedisStore, get_store_optional

LOG = logging.getLogger(__name__)

_PREFIX = "e10:sessions"
_INDEX_KEY = f"{_PREFIX}:index"
_TTL_S = 180 * 86400  # 180 days
_MAX_INDEX = 100


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _session_key(session_id: str) -> str:
    return f"{_PREFIX}:{session_id}"


def _append_index(store: RedisStore, session_id: str) -> None:
    idx = store.get_json(_INDEX_KEY)
    if not isinstance(idx, list):
        idx = []
    idx.append(session_id)
    if len(idx) > _MAX_INDEX:
        idx = idx[-_MAX_INDEX:]
    store.set_json(_INDEX_KEY, idx, ttl_s=_TTL_S)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def log_session(
    session_data: Dict[str, Any],
    store: Optional[RedisStore] = None,
) -> Optional[str]:
    """Persist a new portfolio advisor session to Redis. Returns session_id or None."""
    s = store or get_store_optional()
    if s is None:
        LOG.warning("E10 session log: Redis unavailable")
        return None

    session_id = str(uuid.uuid4())[:12]

    session: Dict[str, Any] = {
        "sessionId": session_id,
        "status": "active",
        "createdAt": _utcnow_iso(),
        "tickers": session_data.get("tickers", []),
        "allocationPlan": session_data.get("allocationPlan", []),
        "deterministicAllocation": session_data.get("deterministicAllocation", {}),
        "advisorOutput": session_data.get("advisorOutput", {}),
        "regimeLabel": session_data.get("regimeLabel", "moderate"),
        "concentrationChoice": session_data.get("concentrationChoice"),
        "sectorBuckets": session_data.get("sectorBuckets", {}),
        "tradeIds": [],
        "outcome": None,
    }

    try:
        s.set_json(_session_key(session_id), session, ttl_s=_TTL_S)
        _append_index(s, session_id)
        return session_id
    except Exception as e:
        LOG.warning("E10 session log failed: %s", e)
        return None


def get_session(
    session_id: str,
    store: Optional[RedisStore] = None,
) -> Optional[Dict[str, Any]]:
    s = store or get_store_optional()
    if s is None:
        return None
    return s.get_json(_session_key(session_id))


def link_trade(
    session_id: str,
    trade_id: str,
    ticker: str,
    store: Optional[RedisStore] = None,
) -> bool:
    """Link an Engine 1 trade to this portfolio session."""
    s = store or get_store_optional()
    if s is None:
        return False
    session = s.get_json(_session_key(session_id))
    if session is None:
        return False
    trade_ids = session.get("tradeIds") or []
    trade_ids.append({"tradeId": trade_id, "ticker": ticker, "linkedAt": _utcnow_iso()})
    session["tradeIds"] = trade_ids
    s.set_json(_session_key(session_id), session, ttl_s=_TTL_S)
    return True


def close_session(
    session_id: str,
    outcome_data: Dict[str, Any],
    store: Optional[RedisStore] = None,
) -> bool:
    """Close a portfolio session with aggregate outcome data."""
    s = store or get_store_optional()
    if s is None:
        return False
    session = s.get_json(_session_key(session_id))
    if session is None:
        return False

    per_trade = outcome_data.get("perTrade", [])
    pnl_list = [float(t.get("realizedPnl", 0)) for t in per_trade if t.get("realizedPnl") is not None]
    total_pnl = sum(pnl_list)
    wins = sum(1 for p in pnl_list if p > 0)
    losses = sum(1 for p in pnl_list if p < 0)
    scratches = sum(1 for p in pnl_list if p == 0)

    session["status"] = "closed"
    session["closedAt"] = _utcnow_iso()
    session["outcome"] = {
        "totalPnl": round(total_pnl, 2),
        "perTrade": per_trade,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "sessionWin": total_pnl > 0,
        "tradeCount": len(per_trade),
    }
    s.set_json(_session_key(session_id), session, ttl_s=_TTL_S)
    return True


# ---------------------------------------------------------------------------
# List helpers
# ---------------------------------------------------------------------------

def list_sessions(
    status: Optional[str] = None,
    store: Optional[RedisStore] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    s = store or get_store_optional()
    if s is None:
        return []
    idx = s.get_json(_INDEX_KEY)
    if not isinstance(idx, list):
        return []

    sessions = []
    for sid in reversed(idx):
        if len(sessions) >= limit:
            break
        sess = s.get_json(_session_key(sid))
        if sess is None:
            continue
        if status and sess.get("status") != status:
            continue
        sessions.append(sess)
    return sessions


def list_closed_sessions(
    store: Optional[RedisStore] = None,
    limit: int = 50,
) -> List[Dict[str, Any]]:
    return list_sessions(status="closed", store=store, limit=limit)


# ---------------------------------------------------------------------------
# Portfolio-Level Performance Digest
# ---------------------------------------------------------------------------

def compute_e10_portfolio_digest(
    store: Optional[RedisStore] = None,
) -> Dict[str, Any]:
    """Aggregate closed portfolio session performance for LLM learning context."""
    closed = list_closed_sessions(store=store, limit=50)
    if not closed:
        return {"totalClosed": 0, "hasData": False}

    total = len(closed)
    session_pnls: List[float] = []
    session_wins = 0
    concentrated: List[float] = []
    balanced: List[float] = []
    diversified: List[float] = []
    regime_buckets: Dict[str, List[float]] = {}
    sector_overlap_outcomes: Dict[str, List[float]] = {}

    for sess in closed:
        outcome = sess.get("outcome") or {}
        pnl = outcome.get("totalPnl", 0.0)
        session_pnls.append(pnl)
        if pnl > 0:
            session_wins += 1

        trade_count = outcome.get("tradeCount", 0)
        if trade_count <= 1:
            concentrated.append(pnl)
        elif trade_count <= 2:
            balanced.append(pnl)
        else:
            diversified.append(pnl)

        regime = sess.get("regimeLabel", "unknown")
        regime_buckets.setdefault(regime, []).append(pnl)

        sector_buckets = sess.get("sectorBuckets") or {}
        has_overlap = any(len(v) > 1 for v in sector_buckets.values() if isinstance(v, list))
        key = "overlap" if has_overlap else "no_overlap"
        sector_overlap_outcomes.setdefault(key, []).append(pnl)

    def _bucket_stats(pnl_list: List[float]) -> Dict[str, Any]:
        if not pnl_list:
            return {"count": 0}
        return {
            "count": len(pnl_list),
            "winRate": round(sum(1 for p in pnl_list if p > 0) / len(pnl_list) * 100, 1),
            "avgPnl": round(statistics.mean(pnl_list), 2),
            "totalPnl": round(sum(pnl_list), 2),
        }

    return {
        "hasData": True,
        "totalClosed": total,
        "sessionWinRate": round(session_wins / total * 100, 1) if total > 0 else 0,
        "avgSessionPnl": round(statistics.mean(session_pnls), 2) if session_pnls else 0,
        "totalPnl": round(sum(session_pnls), 2),
        "byConcentration": {
            "concentrated": _bucket_stats(concentrated),
            "balanced": _bucket_stats(balanced),
            "diversified": _bucket_stats(diversified),
        },
        "byRegime": {k: _bucket_stats(v) for k, v in regime_buckets.items()},
        "bySectorOverlap": {k: _bucket_stats(v) for k, v in sector_overlap_outcomes.items()},
    }
