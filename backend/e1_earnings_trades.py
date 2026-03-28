"""Engine 1 — Earnings IC Trade Persistence (Redis).

Redis key layout:
    e1:trades:{trade_id}    — individual trade JSON document
    e1:trades:index         — ordered list of trade IDs (newest last)

Cross-ticker learning: the performance digest buckets by VRP score,
breach rate, EM, wing width, AMC/BMO timing, and regime so the LLM
can learn patterns that transfer across names.
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

_PREFIX = "e1:trades"
_INDEX_KEY = f"{_PREFIX}:index"


def _utcnow_iso() -> str:
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _trade_key(trade_id: str) -> str:
    return f"{_PREFIX}:{trade_id}"


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def log_trade(
    trade_data: Dict[str, Any],
    store: Optional[RedisStore] = None,
    flags: Optional[FeatureFlags] = None,
) -> Optional[str]:
    """Persist a new earnings IC trade to Redis. Returns trade_id or None."""
    s = store or get_store_optional()
    if s is None:
        LOG.warning("E1 trade log: Redis unavailable")
        return None

    f = flags or get_flags()
    trade_id = str(uuid.uuid4())[:12]
    source = str(trade_data.get("source", "advisor"))

    trade: Dict[str, Any] = {
        "tradeId": trade_id,
        "status": "active",
        "loggedAt": _utcnow_iso(),
        "source": source,
        "ticker": str(trade_data.get("ticker", "")).upper(),
        "entry": trade_data.get("entry", {}),
        "entryContext": trade_data.get("entryContext", {}),
        "advisorVerdict": trade_data.get("advisorVerdict"),
        "checkIns": [],
    }

    if source == "adjusted":
        trade["originalTicket"] = trade_data.get("originalTicket")
        trade["adjustmentNote"] = trade_data.get("adjustmentNote")

    ttl = int(f.E1_TRADE_TTL_S)
    try:
        s.set_json(_trade_key(trade_id), trade, ex=ttl)
        _append_index(s, trade_id, max_len=int(f.E1_TRADE_MAX_INDEX), ttl=ttl)
        return trade_id
    except Exception as e:
        LOG.warning("E1 trade log failed: %s", e)
        return None


def close_trade(
    trade_id: str,
    close_data: Optional[Dict[str, Any]] = None,
    store: Optional[RedisStore] = None,
) -> Optional[Dict[str, Any]]:
    """Close an active trade with outcome data."""
    s = store or get_store_optional()
    if s is None:
        return None

    trade = get_trade(trade_id, store=s)
    if trade is None:
        return None

    cd = close_data or {}
    entry_credit = float(trade.get("entry", {}).get("entryCredit", 0) or 0)
    exit_credit = cd.get("exitCredit")
    realized_pnl = None
    if exit_credit is not None:
        realized_pnl = entry_credit - float(exit_credit)

    outcome_class = "scratch"
    if realized_pnl is not None:
        if realized_pnl > 0.05:
            outcome_class = "win"
        elif realized_pnl < -0.05:
            outcome_class = "loss"

    trade["status"] = "closed"
    trade["closedAt"] = _utcnow_iso()
    trade["closeReason"] = cd.get("closeReason", "manual")
    trade["outcome"] = {
        "entryCredit": entry_credit,
        "exitCredit": float(exit_credit) if exit_credit is not None else None,
        "realizedPnl": float(realized_pnl) if realized_pnl is not None else None,
        "outcomeClass": outcome_class,
        "expiredWorthless": bool(cd.get("expiredWorthless", False)),
        "notes": cd.get("notes"),
    }

    try:
        ttl = int(get_flags().E1_TRADE_TTL_S)
        s.set_json(_trade_key(trade_id), trade, ex=ttl)
        return trade
    except Exception as e:
        LOG.warning("E1 close trade failed: %s", e)
        return None


def get_trade(trade_id: str, store: Optional[RedisStore] = None) -> Optional[Dict[str, Any]]:
    s = store or get_store_optional()
    if s is None:
        return None
    return s.get_json(_trade_key(trade_id))


def list_active_trades(store: Optional[RedisStore] = None) -> List[Dict[str, Any]]:
    return [t for t in _list_all(store) if t.get("status") in ("active", "monitoring")]


def list_closed_trades(store: Optional[RedisStore] = None, limit: int = 100) -> List[Dict[str, Any]]:
    closed = [t for t in _list_all(store) if t.get("status") == "closed"]
    closed.sort(key=lambda t: t.get("closedAt", ""), reverse=True)
    return closed[:limit]


def add_checkin(
    trade_id: str,
    checkin_data: Dict[str, Any],
    store: Optional[RedisStore] = None,
) -> bool:
    s = store or get_store_optional()
    if s is None:
        return False
    trade = get_trade(trade_id, store=s)
    if trade is None:
        return False

    checkins = trade.get("checkIns") or []
    checkin_data["timestamp"] = _utcnow_iso()
    checkins.append(checkin_data)
    trade["checkIns"] = checkins[-20:]

    try:
        ttl = int(get_flags().E1_TRADE_TTL_S)
        s.set_json(_trade_key(trade_id), trade, ex=ttl)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Cross-Ticker Performance Digest
# ---------------------------------------------------------------------------

def compute_e1_trade_performance_digest(
    store: Optional[RedisStore] = None,
) -> Dict[str, Any]:
    """Aggregate closed-trade performance with cross-ticker bucketing.

    Unlike Engine 2 (always SPX), Engine 1 trades different names each time.
    The digest buckets by profile characteristics that transfer across tickers:
    VRP score bucket, breach rate bucket, EM, wing, AMC/BMO timing, regime.
    """
    closed = list_closed_trades(store=store, limit=100)
    if not closed:
        return {"totalClosed": 0, "hasData": False}

    wins, losses, scratches = 0, 0, 0
    pnl_list: List[float] = []
    em_buckets: Dict[str, List[Dict[str, Any]]] = {}
    wing_buckets: Dict[str, List[Dict[str, Any]]] = {}
    regime_buckets: Dict[str, List[Dict[str, Any]]] = {}
    vrp_buckets: Dict[str, List[Dict[str, Any]]] = {}
    breach_buckets: Dict[str, List[Dict[str, Any]]] = {}
    timing_buckets: Dict[str, List[Dict[str, Any]]] = {}
    verdict_outcomes: Dict[str, Dict[str, int]] = {}

    for t in closed:
        outcome = t.get("outcome") or {}
        oc = outcome.get("outcomeClass")
        pnl = outcome.get("realizedPnl")
        entry = t.get("entry", {})
        ctx = t.get("entryContext", {})
        advisor = t.get("advisorVerdict") or {}

        if oc == "win":
            wins += 1
        elif oc == "loss":
            losses += 1
        elif oc == "scratch":
            scratches += 1

        if pnl is not None:
            pnl_list.append(float(pnl))

        rec = {"outcomeClass": oc, "pnl": pnl, "ticker": t.get("ticker")}

        # EM bucket
        em_key = str(entry.get("emMultiple", "?"))
        em_buckets.setdefault(em_key, []).append(rec)

        # Wing bucket
        wing_key = f"${entry.get('wingWidth', '?')}"
        wing_buckets.setdefault(wing_key, []).append(rec)

        # Regime bucket
        regime_key = str(ctx.get("regimeBucket", "?"))
        regime_buckets.setdefault(regime_key, []).append(rec)

        # VRP score bucket
        vrp_score = ctx.get("vrpScore")
        if vrp_score is not None:
            vs = float(vrp_score)
            if vs >= 75:
                vk = "75+"
            elif vs >= 60:
                vk = "60-75"
            elif vs >= 40:
                vk = "40-60"
            else:
                vk = "<40"
        else:
            vk = "?"
        vrp_buckets.setdefault(vk, []).append(rec)

        # Breach rate bucket
        breach_pct = ctx.get("breachPct")
        if breach_pct is not None:
            bp = float(breach_pct)
            if bp < 10:
                bk = "<10%"
            elif bp < 20:
                bk = "10-20%"
            elif bp < 30:
                bk = "20-30%"
            else:
                bk = "30%+"
        else:
            bk = "?"
        breach_buckets.setdefault(bk, []).append(rec)

        # AMC/BMO timing bucket
        timing_key = str(ctx.get("earningsTiming", "?")).upper()
        timing_buckets.setdefault(timing_key, []).append(rec)

        # Verdict calibration
        verdict = advisor.get("verdict", "?")
        if verdict not in verdict_outcomes:
            verdict_outcomes[verdict] = {"win": 0, "loss": 0, "scratch": 0, "total": 0}
        verdict_outcomes[verdict]["total"] += 1
        if oc in ("win", "loss", "scratch"):
            verdict_outcomes[verdict][oc] += 1

    total = len(closed)
    total_decided = wins + losses + scratches
    win_rate = round(wins / total_decided * 100, 1) if total_decided > 0 else None
    avg_pnl = round(statistics.mean(pnl_list), 2) if pnl_list else None
    total_pnl = round(sum(pnl_list), 2) if pnl_list else None
    median_pnl = round(statistics.median(pnl_list), 2) if pnl_list else None

    avg_win = None
    avg_loss = None
    if pnl_list:
        win_pnls = [p for p in pnl_list if p > 0]
        loss_pnls = [p for p in pnl_list if p < 0]
        avg_win = round(statistics.mean(win_pnls), 2) if win_pnls else None
        avg_loss = round(statistics.mean(loss_pnls), 2) if loss_pnls else None

    risk_tendency = "balanced"
    if win_rate is not None:
        if win_rate > 85 and avg_pnl is not None and avg_pnl < 0.5:
            risk_tendency = "too_conservative"
        elif win_rate < 40:
            risk_tendency = "too_aggressive"
        elif avg_loss is not None and avg_win is not None and abs(avg_loss) > avg_win * 3:
            risk_tendency = "risk_reward_skewed"

    def _bucket_summary(bucket: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        out: Dict[str, Any] = {}
        for k, recs in sorted(bucket.items()):
            ws = sum(1 for r in recs if r["outcomeClass"] == "win")
            ls = sum(1 for r in recs if r["outcomeClass"] == "loss")
            ps = [r["pnl"] for r in recs if r["pnl"] is not None]
            n = ws + ls + sum(1 for r in recs if r["outcomeClass"] == "scratch")
            out[k] = {
                "n": len(recs),
                "winRate": round(ws / n * 100, 1) if n > 0 else None,
                "avgPnl": round(statistics.mean(ps), 2) if ps else None,
            }
        return out

    # Recent trades with ticker names for LLM context
    recent_trades: List[Dict[str, Any]] = []
    for t in closed[:5]:
        rt: Dict[str, Any] = {
            "ticker": t.get("ticker"),
            "earningsTiming": (t.get("entryContext") or {}).get("earningsTiming"),
            "emMultiple": (t.get("entry") or {}).get("emMultiple"),
            "wingWidth": (t.get("entry") or {}).get("wingWidth"),
            "outcome": (t.get("outcome") or {}).get("outcomeClass"),
            "pnl": (t.get("outcome") or {}).get("realizedPnl"),
            "closedAt": t.get("closedAt"),
        }
        recent_trades.append(rt)

    return {
        "totalClosed": total,
        "hasData": True,
        "wins": wins,
        "losses": losses,
        "scratches": scratches,
        "winRate": win_rate,
        "avgPnl": avg_pnl,
        "medianPnl": median_pnl,
        "totalPnl": total_pnl,
        "avgWin": avg_win,
        "avgLoss": avg_loss,
        "riskTendency": risk_tendency,
        "byEm": _bucket_summary(em_buckets),
        "byWing": _bucket_summary(wing_buckets),
        "byRegime": _bucket_summary(regime_buckets),
        "byVrpBucket": _bucket_summary(vrp_buckets),
        "byBreachBucket": _bucket_summary(breach_buckets),
        "byTiming": _bucket_summary(timing_buckets),
        "verdictCalibration": verdict_outcomes,
        "recentTrades": recent_trades,
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _list_all(store: Optional[RedisStore] = None) -> List[Dict[str, Any]]:
    s = store or get_store_optional()
    if s is None:
        return []
    try:
        raw = s.get_json(_INDEX_KEY)
        if not isinstance(raw, list):
            return []
        out: List[Dict[str, Any]] = []
        for tid in raw:
            t = s.get_json(_trade_key(str(tid)))
            if isinstance(t, dict):
                out.append(t)
        return out
    except Exception:
        return []


def _append_index(
    store: RedisStore,
    trade_id: str,
    max_len: int = 200,
    ttl: int = 60 * 86400,
) -> None:
    idx = store.get_json(_INDEX_KEY)
    if not isinstance(idx, list):
        idx = []
    idx.append(trade_id)
    if len(idx) > max_len:
        idx = idx[-max_len:]
    store.set_json(_INDEX_KEY, idx, ex=ttl)
