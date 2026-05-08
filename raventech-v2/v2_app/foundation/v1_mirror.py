"""v1 → v2 conformal calibration mirror.

Walks v1's Redis trade journals and replays each closed trade's
(predicted breach probability, realized breach outcome) pair into the
v2 conformal calibrator. After running this once on a populated v1
desk, the conformal tile on the v2 dashboard reads non-synthetic
empirical coverage on real PnL outcomes.

v1 schema (confirmed via grep across the repo):

- E1 (earnings IC): keys ``e1:trades:{trade_id}``, index ``e1:trades:index``
- E2 (SPX IC):       keys ``e2:trades:{trade_id}``, index ``e2:trades:index``

Each trade JSON document has::

    {
      "tradeId":    str,
      "status":     "active" | "monitoring" | "closed",
      "entryContext": {
          "breachPct": float,        # 0..100 (percent), set by frontend
          "vrpScore":  float,        # E1 only
          ...
      },
      "outcome": {                   # only on closed trades
          "outcomeClass": "win" | "loss" | "scratch",
          "realizedPnl":  float,
          "maxBreachProximity": float,   # 0..100
          ...
      },
      ...
    }

Mirror logic for breach_probability:

- prediction: ``entryContext.breachPct / 100.0`` (normalize to [0, 1])
- realized:   ``1.0`` if ``outcome.outcomeClass == "loss"`` else ``0.0``

We use loss-class as the breach indicator because, on credit-spread iron
condors, the dominant loss driver is the underlying breaching one of the
short strikes. Trades that scratched or won did not breach. This is the
same definition the v1 desk reasons about implicitly when it tracks
"breach rate".

Future iterations will add:

- per-ticker calibrators for E1 (earnings tickers vary widely in vol)
- a "PnL %% of credit" continuous metric using the realizedPnl / entryCredit
- a "max breach proximity" continuous metric (regression rather than binary)
"""

from __future__ import annotations

import logging
from typing import Any

from .conformal_store import _redis_client, load_calibrator, now_ts, save_calibrator

LOG = logging.getLogger("v2.v1_mirror")


# ── Engine map ───────────────────────────────────────────


_ENGINE_SOURCES = {
    "e1": {"index_key": "e1:trades:index", "trade_prefix": "e1:trades:"},
    "e2": {"index_key": "e2:trades:index", "trade_prefix": "e2:trades:"},
}


# ── Public API ───────────────────────────────────────────


def mirror_v1_breach_probability(
    *,
    only_engine: str | None = None,
    reset: bool = True,
    max_per_engine: int = 5000,
) -> dict[str, Any]:
    """Replay v1 closed trades into the v2 ``breach_probability`` calibrator.

    Args:
        only_engine: If set to "e1" or "e2", restrict to that engine only.
        reset:       If True (default), clear each touched calibrator before
                     replaying so re-running the mirror is idempotent.
                     If False, append onto the existing rolling window.
        max_per_engine: Hard cap on trades processed per engine (defensive).

    Returns:
        Summary dict with per-engine counts and skip reasons.
    """
    summary: dict[str, Any] = {
        "ok": True,
        "started_at": now_ts(),
        "metric": "breach_probability",
        "reset": reset,
        "engines": {},
        "redis_available": True,
    }

    client = _redis_client()
    if client is None:
        summary["ok"] = False
        summary["redis_available"] = False
        return summary

    targets = (
        {only_engine: _ENGINE_SOURCES[only_engine]}
        if only_engine and only_engine in _ENGINE_SOURCES
        else _ENGINE_SOURCES
    )

    for engine, src in targets.items():
        engine_summary = _mirror_one_engine(
            engine=engine,
            client=client,
            index_key=src["index_key"],
            trade_prefix=src["trade_prefix"],
            reset=reset,
            cap=max_per_engine,
        )
        summary["engines"][engine] = engine_summary

    # Convenience aggregate.
    summary["n_observations_logged"] = sum(
        e.get("n_observations_logged", 0) for e in summary["engines"].values()
    )
    summary["n_trades_seen"] = sum(
        e.get("n_trades_seen", 0) for e in summary["engines"].values()
    )
    summary["finished_at"] = now_ts()
    return summary


# ── Internal ─────────────────────────────────────────────


def _mirror_one_engine(
    *,
    engine: str,
    client: Any,
    index_key: str,
    trade_prefix: str,
    reset: bool,
    cap: int,
) -> dict[str, Any]:
    out: dict[str, Any] = {
        "n_trades_seen": 0,
        "n_closed": 0,
        "n_observations_logged": 0,
        "skips": {
            "not_closed": 0,
            "no_breach_prediction": 0,
            "no_outcome_class": 0,
            "out_of_range_prediction": 0,
            "load_failed": 0,
        },
    }

    try:
        index = _get_json(client, index_key) or []
    except Exception as exc:
        LOG.warning("mirror: failed to read %s: %s", index_key, exc)
        return out

    trade_ids = list(index)[:cap]

    metric = "breach_probability"
    cal = load_calibrator(engine, metric)
    if reset:
        cal.state.scores.clear()

    for tid in trade_ids:
        out["n_trades_seen"] += 1
        try:
            doc = _get_json(client, f"{trade_prefix}{tid}")
        except Exception as exc:
            LOG.debug("mirror: load failed for %s: %s", tid, exc)
            out["skips"]["load_failed"] += 1
            continue
        if not isinstance(doc, dict):
            out["skips"]["load_failed"] += 1
            continue

        if doc.get("status") != "closed":
            out["skips"]["not_closed"] += 1
            continue
        out["n_closed"] += 1

        ctx = doc.get("entryContext") or {}
        breach_pct = ctx.get("breachPct")
        if breach_pct is None:
            out["skips"]["no_breach_prediction"] += 1
            continue

        try:
            prediction = float(breach_pct) / 100.0
        except (TypeError, ValueError):
            out["skips"]["no_breach_prediction"] += 1
            continue
        if not (0.0 <= prediction <= 1.0):
            out["skips"]["out_of_range_prediction"] += 1
            continue

        outcome = doc.get("outcome") or {}
        oc = outcome.get("outcomeClass")
        if oc not in ("win", "loss", "scratch"):
            out["skips"]["no_outcome_class"] += 1
            continue

        realized = 1.0 if oc == "loss" else 0.0
        ts = doc.get("closedAt") or doc.get("loggedAt")
        cal.observe(prediction=prediction, realized=realized, ts=ts)
        out["n_observations_logged"] += 1

    persisted = save_calibrator(engine, metric, cal)
    out["persisted"] = persisted
    out["final_n_calibration"] = cal.state.n
    return out


def _get_json(client: Any, key: str) -> Any:
    """Read a JSON value from Redis. Returns None on miss."""
    raw = client.get(key)
    if raw is None:
        return None
    try:
        import json
        return json.loads(raw)
    except Exception:
        return None
