"""RTv2.0 — Performance Feedback.

Structured tracking of trade outcomes that feeds back into UPS
tie-breaking and capital allocation rebalancing.  Simple aggregates
only — no ML, no opaque adaptation.

Storage:
  - Redis: rtv2:outcomes:{trade_id}             TTL 180 days
  - Redis: rtv2:outcomes:index                  rolling index
  - Redis: rtv2:perf:engine:{engine_id}         90-day metrics
  - Redis: rtv2:perf:bucket:{bucket_id}         90-day metrics
"""

from __future__ import annotations

import datetime as dt
import logging
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

OUTCOME_TTL_S = 180 * 86400
PERF_TTL_S = 180 * 86400
OUTCOME_KEY_PREFIX = "rtv2:outcomes"
INDEX_KEY = "rtv2:outcomes:index"
ENGINE_PERF_PREFIX = "rtv2:perf:engine"
BUCKET_PERF_PREFIX = "rtv2:perf:bucket"

ROLLING_WINDOW_DAYS = 90


@dataclass
class TradeOutcome:
    trade_id: str = ""
    engine_source: str = ""
    bucket: str = ""
    trade_type: str = ""
    entry_date: str = ""
    exit_date: str = ""
    days_held: int = 0
    pnl_dollars: float = 0.0
    pnl_pct: float = 0.0
    hit_target: bool = False
    hit_stop: bool = False
    exit_reason: str = ""
    derived_ru_at_entry: float = 0.0
    regime_at_entry: str = ""
    regime_at_exit: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "TradeOutcome":
        if not isinstance(d, dict):
            return cls()
        flds = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in d.items() if k in flds})


@dataclass
class RollingMetrics:
    entity_id: str = ""
    entity_type: str = ""
    window_days: int = ROLLING_WINDOW_DAYS
    trade_count: int = 0
    win_count: int = 0
    loss_count: int = 0
    win_rate: float = 0.0
    avg_return_per_ru: float = 0.0
    avg_days_held: float = 0.0
    total_pnl: float = 0.0
    worst_drawdown: float = 0.0
    consecutive_wins: int = 0
    consecutive_losses: int = 0
    computed_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "RollingMetrics":
        if not isinstance(d, dict):
            return cls()
        flds = set(cls.__dataclass_fields__.keys())
        return cls(**{k: v for k, v in d.items() if k in flds})


# ---------------------------------------------------------------------------
# Outcome creation
# ---------------------------------------------------------------------------

def create_outcome(
    trade: dict,
    *,
    pnl_dollars: float = 0.0,
    exit_reason: str = "desk_discretion",
    regime_at_exit: str = "",
) -> TradeOutcome:
    """Build a TradeOutcome from a closed trade record."""
    entry_price = float(trade.get("entry_price", 0))
    thesis_target = float(trade.get("thesis_target", 0))
    thesis_stop = float(trade.get("thesis_stop", 0))
    derived_ru = float(trade.get("derived_ru", 0)) or 1.0

    pnl_pct = pnl_dollars / (derived_ru * 100) if derived_ru else 0

    hit_target = pnl_dollars > 0 and abs(pnl_pct) >= 0.80
    hit_stop = exit_reason in ("stop", "thesis_invalidated")

    entry_date = str(trade.get("entry_date", ""))
    exit_date = dt.date.today().isoformat()
    try:
        d1 = dt.date.fromisoformat(entry_date)
        d2 = dt.date.fromisoformat(exit_date)
        days_held = max(0, (d2 - d1).days)
    except (ValueError, TypeError):
        days_held = 0

    return TradeOutcome(
        trade_id=str(trade.get("trade_id", "")),
        engine_source=str(trade.get("engine_source", "")),
        bucket=str(trade.get("bucket", "")),
        trade_type=str(trade.get("trade_type", "")),
        entry_date=entry_date,
        exit_date=exit_date,
        days_held=days_held,
        pnl_dollars=round(pnl_dollars, 2),
        pnl_pct=round(pnl_pct, 4),
        hit_target=hit_target,
        hit_stop=hit_stop,
        exit_reason=exit_reason,
        derived_ru_at_entry=round(derived_ru, 3),
        regime_at_entry=str(trade.get("regime_at_entry", trade.get("regime", ""))),
        regime_at_exit=regime_at_exit,
    )


# ---------------------------------------------------------------------------
# Rolling metrics computation
# ---------------------------------------------------------------------------

def _filter_recent(outcomes: List[TradeOutcome], window_days: int = ROLLING_WINDOW_DAYS) -> List[TradeOutcome]:
    cutoff = dt.date.today() - dt.timedelta(days=window_days)
    result = []
    for o in outcomes:
        try:
            ed = dt.date.fromisoformat(o.exit_date)
            if ed >= cutoff:
                result.append(o)
        except (ValueError, TypeError):
            pass
    return result


def _compute_streaks(outcomes: List[TradeOutcome]) -> tuple:
    """Compute current consecutive win and loss streaks."""
    sorted_out = sorted(outcomes, key=lambda o: o.exit_date)
    wins = 0
    losses = 0
    for o in reversed(sorted_out):
        if o.pnl_dollars > 0:
            if losses > 0:
                break
            wins += 1
        elif o.pnl_dollars < 0:
            if wins > 0:
                break
            losses += 1
    return wins, losses


def _compute_worst_drawdown(outcomes: List[TradeOutcome]) -> float:
    """Peak-to-trough drawdown from cumulative P&L series."""
    sorted_out = sorted(outcomes, key=lambda o: o.exit_date)
    if not sorted_out:
        return 0.0
    cumulative = 0.0
    peak = 0.0
    worst_dd = 0.0
    for o in sorted_out:
        cumulative += o.pnl_dollars
        if cumulative > peak:
            peak = cumulative
        dd = peak - cumulative
        if dd > worst_dd:
            worst_dd = dd
    return round(worst_dd, 2)


def compute_metrics(
    entity_id: str,
    entity_type: str,
    outcomes: List[TradeOutcome],
    window_days: int = ROLLING_WINDOW_DAYS,
) -> RollingMetrics:
    """Compute rolling metrics for an engine or bucket."""
    recent = _filter_recent(outcomes, window_days)
    now_iso = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"

    if not recent:
        return RollingMetrics(
            entity_id=entity_id,
            entity_type=entity_type,
            window_days=window_days,
            computed_at=now_iso,
        )

    wins = [o for o in recent if o.pnl_dollars > 0]
    losses = [o for o in recent if o.pnl_dollars <= 0]
    win_rate = len(wins) / len(recent) if recent else 0

    returns_per_ru = []
    for o in recent:
        if o.derived_ru_at_entry > 0:
            returns_per_ru.append(o.pnl_dollars / o.derived_ru_at_entry)

    avg_rpr = statistics.mean(returns_per_ru) if returns_per_ru else 0
    avg_days = statistics.mean([o.days_held for o in recent]) if recent else 0
    total_pnl = sum(o.pnl_dollars for o in recent)
    worst_dd = _compute_worst_drawdown(recent)
    consec_wins, consec_losses = _compute_streaks(recent)

    return RollingMetrics(
        entity_id=entity_id,
        entity_type=entity_type,
        window_days=window_days,
        trade_count=len(recent),
        win_count=len(wins),
        loss_count=len(losses),
        win_rate=round(win_rate, 4),
        avg_return_per_ru=round(avg_rpr, 2),
        avg_days_held=round(avg_days, 1),
        total_pnl=round(total_pnl, 2),
        worst_drawdown=worst_dd,
        consecutive_wins=consec_wins,
        consecutive_losses=consec_losses,
        computed_at=now_iso,
    )


def engine_hit_rate_bonus(engine_id: str, outcomes: List[TradeOutcome]) -> float:
    """Rolling 90-day hit rate → 0-10 bonus for UPS tiebreaking."""
    recent = _filter_recent(
        [o for o in outcomes if o.engine_source == engine_id],
        ROLLING_WINDOW_DAYS,
    )
    if len(recent) < 5:
        return 5.0  # neutral until sufficient sample
    win_rate = sum(1 for o in recent if o.pnl_pct > 0) / len(recent)
    return round(win_rate * 10, 1)


def compute_bucket_streaks(
    outcomes: List[TradeOutcome],
) -> Dict[str, int]:
    """Compute weekly consecutive win/loss streak per bucket.

    Positive = consecutive winning weeks, negative = consecutive losing weeks.
    """
    buckets: Dict[str, List[TradeOutcome]] = {}
    for o in outcomes:
        buckets.setdefault(o.bucket, []).append(o)

    streaks: Dict[str, int] = {}
    today = dt.date.today()

    for bname, outs in buckets.items():
        weekly_pnl: Dict[int, float] = {}
        for o in outs:
            try:
                ed = dt.date.fromisoformat(o.exit_date)
                week_num = (today - ed).days // 7
                if week_num < 8:
                    weekly_pnl[week_num] = weekly_pnl.get(week_num, 0) + o.pnl_dollars
            except (ValueError, TypeError):
                pass

        streak = 0
        for w in sorted(weekly_pnl.keys()):
            pnl = weekly_pnl[w]
            if pnl > 0:
                if streak < 0:
                    break
                streak += 1
            elif pnl < 0:
                if streak > 0:
                    break
                streak -= 1
        streaks[bname] = streak

    return streaks


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

def persist_outcome(outcome: TradeOutcome, store: Any) -> bool:
    if store is None:
        return False
    key = f"{OUTCOME_KEY_PREFIX}:{outcome.trade_id}"
    ok = store.set_json(key, outcome.to_dict(), ttl_s=OUTCOME_TTL_S)
    if ok:
        idx = store.get_json(INDEX_KEY) or []
        idx.append(outcome.trade_id)
        idx = idx[-2000:]
        store.set_json(INDEX_KEY, idx, ttl_s=OUTCOME_TTL_S)
    return ok


def load_all_outcomes(store: Any) -> List[TradeOutcome]:
    if store is None:
        return []
    idx = store.get_json(INDEX_KEY)
    if not isinstance(idx, list):
        return []
    outcomes = []
    for tid in idx:
        data = store.get_json(f"{OUTCOME_KEY_PREFIX}:{tid}")
        if data:
            outcomes.append(TradeOutcome.from_dict(data))
    return outcomes


def persist_engine_metrics(metrics: RollingMetrics, store: Any) -> bool:
    if store is None:
        return False
    key = f"{ENGINE_PERF_PREFIX}:{metrics.entity_id}"
    return store.set_json(key, metrics.to_dict(), ttl_s=PERF_TTL_S)


def persist_bucket_metrics(metrics: RollingMetrics, store: Any) -> bool:
    if store is None:
        return False
    key = f"{BUCKET_PERF_PREFIX}:{metrics.entity_id}"
    return store.set_json(key, metrics.to_dict(), ttl_s=PERF_TTL_S)


def load_engine_metrics(engine_id: str, store: Any) -> Optional[RollingMetrics]:
    if store is None:
        return None
    data = store.get_json(f"{ENGINE_PERF_PREFIX}:{engine_id}")
    if data is None:
        return None
    return RollingMetrics.from_dict(data)


def load_bucket_metrics(bucket_id: str, store: Any) -> Optional[RollingMetrics]:
    if store is None:
        return None
    data = store.get_json(f"{BUCKET_PERF_PREFIX}:{bucket_id}")
    if data is None:
        return None
    return RollingMetrics.from_dict(data)


def refresh_all_metrics(store: Any) -> Dict[str, Any]:
    """Recompute and persist all rolling metrics."""
    outcomes = load_all_outcomes(store)
    if not outcomes:
        return {"engines": {}, "buckets": {}}

    engines: Dict[str, List[TradeOutcome]] = {}
    buckets: Dict[str, List[TradeOutcome]] = {}
    for o in outcomes:
        engines.setdefault(o.engine_source, []).append(o)
        buckets.setdefault(o.bucket, []).append(o)

    result: Dict[str, Any] = {"engines": {}, "buckets": {}}
    for eid, outs in engines.items():
        m = compute_metrics(eid, "engine", outs)
        persist_engine_metrics(m, store)
        result["engines"][eid] = m.to_dict()

    for bid, outs in buckets.items():
        m = compute_metrics(bid, "bucket", outs)
        persist_bucket_metrics(m, store)
        result["buckets"][bid] = m.to_dict()

    return result
