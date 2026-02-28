"""Engine 5: Global Lead-Lag Engine routes."""
from __future__ import annotations

import asyncio
import json
import logging
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

from backend.config import get_flags
from backend.deps import LOG, get_client, get_client_optional
from backend.orats_client import OratsError

router = APIRouter()


def _engine5_snapshot_response(snap: dict) -> dict:
    """Merge snapshot metadata into the data payload for the frontend."""
    meta = snap.get("meta", {})
    data = snap.get("data", {})
    data["meta"] = meta
    return data


def _engine5_get_best_snapshot(store, flags):
    """Return the best snapshot from cache, or None."""
    from backend.engine5_snapshot import select_best_snapshot

    return select_best_snapshot(
        store,
        max_age_days=flags.ENGINE5_SNAPSHOT_BEST_MAX_AGE_DAYS,
        snapshot_ttl=flags.ENGINE5_SNAPSHOT_TTL_S,
    )


def _get_store_optional():
    from backend.redis_store import get_store_optional

    return get_store_optional()


@router.get("/api/engine5/weekly-ideas")
async def engine5_weekly_ideas(view: str = "best", date: str = ""):
    """Smart Engine 5 endpoint with immutable snapshot selection.

    Query parameter ``view``:
    - **best**  (default): Return the highest-quality recent snapshot (Grade A/B).
      If no A/B exists, return newest with a warning.  If NO snapshots exist at
      all, auto-bootstrap and run the pipeline, then return the result.
    - **latest**: Return the newest snapshot regardless of quality.
    - **asof**: Return snapshot matching ``date`` (YYYY-MM-DD) as the US as-of date.
    - **run**: Explicitly trigger a new pipeline run and return the new snapshot.
    """
    flags = get_flags()
    if not flags.ENABLE_ENGINE5_LEAD_LAG:
        raise HTTPException(status_code=404, detail="Engine 5 is not enabled")

    store = _get_store_optional()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    # ---- view=run  --------------------------------------------------------
    if view == "run":
        from backend.engine5_pipeline import run_pipeline

        best_before = _engine5_get_best_snapshot(store, flags)
        best_before_meta = best_before.get("meta", {}) if best_before else None

        try:
            loop = asyncio.get_event_loop()
            exit_code, snapshot_id = await loop.run_in_executor(
                None, lambda: run_pipeline(force=True, source="manual"),
            )
        except Exception as e:
            LOG.exception("Engine 5 pipeline run failed")
            raise HTTPException(status_code=500, detail=f"Pipeline error: {e}") from e

        if exit_code != 0 or snapshot_id is None:
            raise HTTPException(status_code=500, detail="Pipeline completed with errors. Check server logs.")

        snap = store.get_json(f"engine5:snapshot:{snapshot_id}")
        if snap is None:
            raise HTTPException(status_code=500, detail="Pipeline succeeded but snapshot not found.")

        resp = _engine5_snapshot_response(snap)

        if best_before_meta:
            new_meta = resp.get("meta", {})
            best_sid = best_before_meta.get("snapshotId", "")
            new_sid = new_meta.get("snapshotId", "")
            if best_sid and best_sid != new_sid:
                new_meta["bestSnapshotMeta"] = best_before_meta
                resp["meta"] = new_meta

        return resp

    # ---- view=latest  -----------------------------------------------------
    if view == "latest":
        from backend.engine5_snapshot import select_latest_snapshot

        snap = select_latest_snapshot(store)
        if snap is not None:
            return _engine5_snapshot_response(snap)
        raise HTTPException(status_code=404, detail="No snapshots available yet.")

    # ---- view=asof  -------------------------------------------------------
    if view == "asof":
        if not date:
            raise HTTPException(status_code=400, detail="date parameter required for view=asof")
        from backend.engine5_snapshot import select_asof_snapshot

        snap = select_asof_snapshot(store, target_date=date)
        if snap is not None:
            return _engine5_snapshot_response(snap)
        raise HTTPException(status_code=404, detail=f"No snapshot found for as-of date {date}")

    # ---- view=best (default)  ---------------------------------------------
    snap = _engine5_get_best_snapshot(store, flags)
    if snap is not None:
        return _engine5_snapshot_response(snap)

    LOG.info("No Engine 5 snapshots found — auto-bootstrapping pipeline...")
    from backend.engine5_pipeline import run_pipeline

    try:
        loop = asyncio.get_event_loop()
        exit_code, snapshot_id = await loop.run_in_executor(
            None, lambda: run_pipeline(force=True, source="auto"),
        )
    except Exception as e:
        LOG.exception("Engine 5 auto-bootstrap failed")
        raise HTTPException(status_code=500, detail=f"Auto-bootstrap error: {e}") from e

    if exit_code != 0 or snapshot_id is None:
        raise HTTPException(
            status_code=500,
            detail="Auto-bootstrap pipeline completed with errors. Check server logs.",
        )

    snap = store.get_json(f"engine5:snapshot:{snapshot_id}")
    if snap is None:
        raise HTTPException(status_code=500, detail="Pipeline succeeded but snapshot not found.")

    return _engine5_snapshot_response(snap)


@router.get("/api/engine5/regime")
async def engine5_regime():
    """Return the current global regime state from the best snapshot."""
    flags = get_flags()
    if not flags.ENABLE_ENGINE5_LEAD_LAG:
        raise HTTPException(status_code=404, detail="Engine 5 is not enabled")

    store = _get_store_optional()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    snap = _engine5_get_best_snapshot(store, flags)
    if snap is None:
        raise HTTPException(status_code=404, detail="No regime data available")

    data = snap.get("data", {})
    regime_data = data.get("regime")
    if not regime_data:
        raise HTTPException(status_code=404, detail="No regime data in snapshot")

    return regime_data


@router.get("/api/engine5/signals")
async def engine5_signals():
    """Return lead-lag signals from the best snapshot (debugging/transparency)."""
    flags = get_flags()
    if not flags.ENABLE_ENGINE5_LEAD_LAG:
        raise HTTPException(status_code=404, detail="Engine 5 is not enabled")

    store = _get_store_optional()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    snap = _engine5_get_best_snapshot(store, flags)
    if snap is None:
        raise HTTPException(status_code=404, detail="No signal data available")

    data = snap.get("data", {})
    summary = data.get("globalSignalSummary", {})
    return {"signals": summary, "meta": snap.get("meta", {})}


@router.get("/api/engine5/global-summary")
async def engine5_global_summary():
    """Return global bar summary from the best snapshot."""
    flags = get_flags()
    if not flags.ENABLE_ENGINE5_LEAD_LAG:
        raise HTTPException(status_code=404, detail="Engine 5 is not enabled")

    store = _get_store_optional()
    if store is None:
        raise HTTPException(status_code=503, detail="Redis unavailable")

    snap = _engine5_get_best_snapshot(store, flags)
    if snap is None:
        raise HTTPException(status_code=404, detail="No global summary available")

    data = snap.get("data", {})
    meta = snap.get("meta", {})
    return {
        "globalSignalSummary": data.get("globalSignalSummary", {}),
        "regime": data.get("regime", {}),
        "meta": meta,
    }
