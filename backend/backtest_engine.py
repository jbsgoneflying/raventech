"""Backtesting & Paper-Trade Tracking Framework.

Provides systematic signal quality validation over historical data and
live paper-trade tracking with P&L attribution per engine.

Two modes:
1. **Paper Trading**: Record hypothetical trades from engine signals,
   track them forward, and compute realized P&L.
2. **Historical Backtest**: Replay engine logic over historical data
   and compute signal quality metrics.

Storage: Redis with `backtest:` prefix.
"""
from __future__ import annotations

import datetime as dt
import json
import logging
import uuid
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

_PAPER_TRADES_KEY = "backtest:paper_trades"
_PERFORMANCE_KEY = "backtest:performance"


@dataclass
class PaperTrade:
    """A paper trade tracked by the backtesting framework."""
    trade_id: str = ""
    engine_id: int = 0
    engine_name: str = ""
    ticker: str = ""
    direction: str = ""         # "long", "short", "spread"
    structure: str = ""         # e.g., "iron_condor", "pairs", "call_spread"
    entry_date: str = ""
    entry_price: float = 0.0
    quantity: float = 1.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    expiry_date: Optional[str] = None
    signal_score: float = 0.0
    signal_context: Dict[str, Any] = field(default_factory=dict)
    status: str = "open"        # "open", "closed", "expired"
    exit_date: Optional[str] = None
    exit_price: Optional[float] = None
    pnl: Optional[float] = None
    pnl_pct: Optional[float] = None
    exit_reason: Optional[str] = None
    notes: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class EnginePerformance:
    """Aggregated performance metrics for one engine."""
    engine_id: int = 0
    engine_name: str = ""
    total_trades: int = 0
    open_trades: int = 0
    closed_trades: int = 0
    winning_trades: int = 0
    losing_trades: int = 0
    win_rate: float = 0.0
    total_pnl: float = 0.0
    avg_pnl: float = 0.0
    max_win: float = 0.0
    max_loss: float = 0.0
    avg_hold_days: float = 0.0
    sharpe_estimate: Optional[float] = None
    last_trade_date: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class BacktestSummary:
    """System-wide backtesting summary."""
    as_of: str = ""
    total_paper_trades: int = 0
    open_count: int = 0
    closed_count: int = 0
    total_pnl: float = 0.0
    by_engine: List[EnginePerformance] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["by_engine"] = [asdict(e) for e in self.by_engine]
        return d


def log_paper_trade(
    trade: PaperTrade,
    *,
    store: Any = None,
) -> str:
    """Record a new paper trade. Returns trade_id."""
    if not trade.trade_id:
        trade.trade_id = f"pt_{uuid.uuid4().hex[:12]}"
    if not trade.entry_date:
        trade.entry_date = dt.date.today().isoformat()

    _LOG.info(
        "Paper trade logged: %s engine=%d %s %s @ %.2f (score=%.0f)",
        trade.trade_id, trade.engine_id, trade.direction,
        trade.ticker, trade.entry_price, trade.signal_score,
    )

    if store is not None:
        try:
            all_trades = store.get_json(_PAPER_TRADES_KEY) or []
            all_trades.append(trade.to_dict())
            store.set_json(_PAPER_TRADES_KEY, all_trades)
        except Exception as exc:
            _LOG.warning("Failed to persist paper trade: %s", exc)

    return trade.trade_id


def close_paper_trade(
    trade_id: str,
    *,
    exit_price: float,
    exit_reason: str = "manual",
    store: Any = None,
) -> Optional[PaperTrade]:
    """Close an open paper trade and compute P&L."""
    if store is None:
        return None

    try:
        all_trades = store.get_json(_PAPER_TRADES_KEY) or []
    except Exception:
        return None

    for i, t in enumerate(all_trades):
        if t.get("trade_id") == trade_id and t.get("status") == "open":
            t["status"] = "closed"
            t["exit_date"] = dt.date.today().isoformat()
            t["exit_price"] = exit_price
            t["exit_reason"] = exit_reason

            entry = float(t.get("entry_price", 0))
            if entry > 0:
                if t.get("direction") == "short":
                    pnl = (entry - exit_price) * float(t.get("quantity", 1))
                else:
                    pnl = (exit_price - entry) * float(t.get("quantity", 1))
                t["pnl"] = round(pnl, 2)
                t["pnl_pct"] = round((pnl / entry) * 100, 2) if entry else 0

            all_trades[i] = t
            try:
                store.set_json(_PAPER_TRADES_KEY, all_trades)
            except Exception:
                pass

            _LOG.info(
                "Paper trade closed: %s P&L=%.2f (%s)",
                trade_id, t.get("pnl", 0), exit_reason,
            )
            return PaperTrade(**{k: v for k, v in t.items()
                                if k in PaperTrade.__dataclass_fields__})

    return None


def get_paper_trades(
    *,
    store: Any = None,
    engine_id: Optional[int] = None,
    status: Optional[str] = None,
) -> List[PaperTrade]:
    """Retrieve paper trades with optional filtering."""
    if store is None:
        return []

    try:
        all_trades = store.get_json(_PAPER_TRADES_KEY) or []
    except Exception:
        return []

    results = []
    for t in all_trades:
        if engine_id is not None and t.get("engine_id") != engine_id:
            continue
        if status is not None and t.get("status") != status:
            continue
        results.append(PaperTrade(**{k: v for k, v in t.items()
                                     if k in PaperTrade.__dataclass_fields__}))

    return results


def compute_performance(
    trades: List[PaperTrade],
    engine_id: int,
    engine_name: str,
) -> EnginePerformance:
    """Compute aggregated performance metrics for one engine."""
    closed = [t for t in trades if t.status == "closed" and t.pnl is not None]
    open_trades = [t for t in trades if t.status == "open"]

    if not closed:
        return EnginePerformance(
            engine_id=engine_id, engine_name=engine_name,
            total_trades=len(trades), open_trades=len(open_trades),
        )

    pnls = [t.pnl for t in closed if t.pnl is not None]
    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p <= 0]

    hold_days = []
    for t in closed:
        if t.entry_date and t.exit_date:
            try:
                d1 = dt.date.fromisoformat(t.entry_date)
                d2 = dt.date.fromisoformat(t.exit_date)
                hold_days.append((d2 - d1).days)
            except (ValueError, TypeError):
                pass

    import math
    sharpe = None
    if len(pnls) >= 5:
        mean_pnl = sum(pnls) / len(pnls)
        var_pnl = sum((p - mean_pnl) ** 2 for p in pnls) / len(pnls)
        std_pnl = math.sqrt(var_pnl) if var_pnl > 0 else 0
        if std_pnl > 0:
            sharpe = round(mean_pnl / std_pnl * math.sqrt(252), 2)

    last_date = None
    for t in sorted(closed, key=lambda x: x.exit_date or ""):
        last_date = t.exit_date

    return EnginePerformance(
        engine_id=engine_id,
        engine_name=engine_name,
        total_trades=len(trades),
        open_trades=len(open_trades),
        closed_trades=len(closed),
        winning_trades=len(wins),
        losing_trades=len(losses),
        win_rate=round(len(wins) / len(closed) * 100, 1) if closed else 0,
        total_pnl=round(sum(pnls), 2),
        avg_pnl=round(sum(pnls) / len(pnls), 2) if pnls else 0,
        max_win=round(max(pnls), 2) if pnls else 0,
        max_loss=round(min(pnls), 2) if pnls else 0,
        avg_hold_days=round(sum(hold_days) / len(hold_days), 1) if hold_days else 0,
        sharpe_estimate=sharpe,
        last_trade_date=last_date,
    )


def get_backtest_summary(*, store: Any = None) -> BacktestSummary:
    """Compute system-wide backtesting summary."""
    from backend.config import ENGINE_REGISTRY

    all_trades = get_paper_trades(store=store)

    by_engine: Dict[int, List[PaperTrade]] = {}
    for t in all_trades:
        by_engine.setdefault(t.engine_id, []).append(t)

    engine_perfs: List[EnginePerformance] = []
    for eid, trades in sorted(by_engine.items()):
        reg = ENGINE_REGISTRY.get(eid, {})
        name = reg.get("name", f"Engine {eid}")
        engine_perfs.append(compute_performance(trades, eid, name))

    open_count = sum(1 for t in all_trades if t.status == "open")
    closed_count = sum(1 for t in all_trades if t.status == "closed")
    total_pnl = sum(t.pnl or 0 for t in all_trades if t.pnl is not None)

    return BacktestSummary(
        as_of=dt.datetime.utcnow().isoformat() + "Z",
        total_paper_trades=len(all_trades),
        open_count=open_count,
        closed_count=closed_count,
        total_pnl=round(total_pnl, 2),
        by_engine=engine_perfs,
    )
