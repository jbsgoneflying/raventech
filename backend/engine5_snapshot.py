"""Engine 5 – Immutable Snapshot Metadata, Grading & Selection.

Each pipeline run produces an immutable snapshot stored in Redis with:
- A unique snapshot_id  (YYYYMMDD-HHmmss UTC)
- Metadata including freshness grade (A / B / C), completeness score,
  and per-region as-of dates.
- The full WeeklyIdeas payload.

Snapshots are never overwritten.  Pointers (best / latest) and a small
index list enable fast retrieval without scanning all keys.
"""

from __future__ import annotations

import datetime as dt
import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

# Representative tickers per region used for as-of date derivation.
REGION_SENTINELS: Dict[str, str] = {
    "us": "GSPC.INDX",
    "eu": "STOXX50E.INDX",
    "asia": "N225.INDX",
    "au": "AXJO.INDX",
}

GRADE_LABELS: Dict[str, str] = {
    "A": "Fully synced global closes",
    "B": "Usable overnight view",
    "C": "Partial data — use caution",
}


# ---------------------------------------------------------------------------
# Dataclass
# ---------------------------------------------------------------------------


@dataclass
class SnapshotMeta:
    snapshot_id: str = ""                        # "20260210-043012"
    created_at_utc: str = ""                     # ISO 8601
    asof_dates: Dict[str, str] = field(default_factory=dict)  # {"us": "2026-02-09", …}
    grade: str = "C"
    grade_label: str = ""
    completeness: float = 0.0
    regime_label: str = ""
    trade_ideas_count: int = 0
    is_stale: bool = False
    pipeline_duration_s: float = 0.0
    source: str = "manual"                       # "cron", "manual", "auto"
    warning: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        # camelCase for the JS frontend
        return {
            "snapshotId": d["snapshot_id"],
            "createdAt": d["created_at_utc"],
            "asofDates": d["asof_dates"],
            "grade": d["grade"],
            "gradeLabel": d["grade_label"],
            "completeness": d["completeness"],
            "regimeLabel": d["regime_label"],
            "tradeIdeasCount": d["trade_ideas_count"],
            "isStale": d["is_stale"],
            "pipelineDurationS": d["pipeline_duration_s"],
            "source": d["source"],
            "warning": d["warning"],
        }


# ---------------------------------------------------------------------------
# Snapshot ID generation
# ---------------------------------------------------------------------------


def generate_snapshot_id(now_utc: Optional[dt.datetime] = None) -> str:
    """Return a human-readable, lexicographically sortable snapshot ID."""
    if now_utc is None:
        now_utc = dt.datetime.now(dt.timezone.utc)
    return now_utc.strftime("%Y%m%d-%H%M%S")


# ---------------------------------------------------------------------------
# As-of date extraction
# ---------------------------------------------------------------------------


def compute_asof_dates(
    bars: List[dict],
    universe: dict,
) -> Dict[str, str]:
    """Derive the latest bar date per region from the normalized bars.

    ``bars`` is the list of GlobalAssetBar dicts produced by the pipeline
    for the *latest* date.  We look for the sentinel ticker in each region
    and extract its ``date`` field.
    """
    # Build symbol → region mapping from universe equity_indices
    sym_region: Dict[str, str] = {}
    for entry in universe.get("equity_indices", []):
        sym = entry.get("symbol", "")
        region = entry.get("region", "")
        sym_region[sym] = region

    # Build bar lookup by symbol
    bar_by_sym: Dict[str, str] = {}
    for b in bars:
        sym = b.get("symbol", "")
        d = str(b.get("date", ""))[:10]
        if sym and d:
            bar_by_sym[sym] = d

    asof: Dict[str, str] = {}
    for region_key, sentinel_sym in REGION_SENTINELS.items():
        d = bar_by_sym.get(sentinel_sym, "")
        if d:
            asof[region_key] = d
        else:
            asof[region_key] = ""

    return asof


# ---------------------------------------------------------------------------
# Freshness grading
# ---------------------------------------------------------------------------


def compute_grade(asof_dates: Dict[str, str], is_stale: bool) -> str:
    """Assign A / B / C based on regional freshness.

    Grade A: asia, eu, and us all share the same date, not stale.
    Grade B: asia and eu fresh (non-empty and equal), us may be one day
             earlier (normal overnight planning scenario). Not stale.
    Grade C: missing any region, or data flagged stale.
    """
    if is_stale:
        return "C"

    us = asof_dates.get("us", "")
    eu = asof_dates.get("eu", "")
    asia = asof_dates.get("asia", "")

    # Any region missing → C
    if not us or not eu or not asia:
        return "C"

    # Grade A: all three match
    if us == eu == asia:
        return "A"

    # Grade B: EU and Asia match (and are non-empty), US may differ
    if eu == asia:
        return "B"

    return "C"


# ---------------------------------------------------------------------------
# Completeness scoring
# ---------------------------------------------------------------------------


def compute_completeness(snapshot_data: dict) -> float:
    """Score 0.0–1.0 based on how much of the expected output is present.

    Components (each worth equal weight):
    1. Has trade ideas (> 0)
    2. Has regime data
    3. Has sector biases (> 0)
    4. Has global signal summary / narrative
    5. Has vol lead-lag data
    """
    score = 0.0
    total = 5.0

    # 1. Trade ideas
    ideas = snapshot_data.get("tradeIdeas", [])
    if ideas:
        score += 1.0

    # 2. Regime
    regime = snapshot_data.get("regime")
    if regime and regime.get("label"):
        score += 1.0

    # 3. Sector biases
    biases = snapshot_data.get("sectorBiases", [])
    if biases:
        score += 1.0

    # 4. Narrative / global signal summary
    narrative = snapshot_data.get("globalSignalSummary")
    if narrative and narrative.get("narrative"):
        score += 1.0

    # 5. Vol lead-lag
    vll = snapshot_data.get("volLeadLag")
    if vll:
        score += 1.0

    return round(score / total, 2)


# ---------------------------------------------------------------------------
# Snapshot selection helpers
# ---------------------------------------------------------------------------


def select_best_snapshot(
    store: Any,
    max_age_days: int = 14,
    snapshot_ttl: int = 14 * 86400,
) -> Optional[dict]:
    """Return the best available snapshot (Grade A/B, highest completeness).

    Falls back to newest snapshot regardless of grade if no A/B is found.
    Returns ``None`` only when no snapshots exist at all.
    """
    index = _load_index(store)
    if not index:
        return None

    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=max_age_days)

    candidates: List[dict] = []
    fallback: Optional[dict] = None

    for sid in index:
        snap = store.get_json(f"engine5:snapshot:{sid}")
        if snap is None:
            continue

        meta = snap.get("meta", {})

        # Keep first valid snapshot as absolute fallback
        if fallback is None:
            fallback = snap

        created = meta.get("createdAt", "")
        if not created:
            continue

        # Age filter
        try:
            created_dt = dt.datetime.fromisoformat(created.replace("Z", "+00:00"))
            if created_dt < cutoff:
                continue
        except (ValueError, TypeError):
            continue

        grade = meta.get("grade", "C")
        if grade in ("A", "B"):
            candidates.append(snap)

    if candidates:
        # Sort by completeness DESC, then createdAt DESC
        def _sort_key(s: dict) -> tuple:
            m = s.get("meta", {})
            return (m.get("completeness", 0.0), m.get("createdAt", ""))
        candidates.sort(key=_sort_key, reverse=True)
        best = candidates[0]
        return best

    # No A/B → return newest with warning
    if fallback is not None:
        meta = fallback.get("meta", {})
        if meta.get("warning") is None:
            meta["warning"] = "No Grade A/B snapshot available. Showing most recent."
            fallback["meta"] = meta
        return fallback

    return None


def select_latest_snapshot(store: Any) -> Optional[dict]:
    """Return the newest snapshot regardless of quality."""
    sid = store.get_json("engine5:pointer:latest")
    if sid and isinstance(sid, str):
        snap = store.get_json(f"engine5:snapshot:{sid}")
        if snap is not None:
            return snap

    # Pointer missing/stale → fallback to index[0]
    index = _load_index(store)
    if index:
        snap = store.get_json(f"engine5:snapshot:{index[0]}")
        if snap is not None:
            return snap

    return None


def select_asof_snapshot(store: Any, target_date: str) -> Optional[dict]:
    """Return the snapshot whose US as-of date matches *target_date*.

    If multiple match, returns the most recently created one.
    """
    index = _load_index(store)
    if not index:
        return None

    for sid in index:  # index is newest-first
        snap = store.get_json(f"engine5:snapshot:{sid}")
        if snap is None:
            continue
        meta = snap.get("meta", {})
        us_date = meta.get("asofDates", {}).get("us", "")
        if us_date == target_date:
            return snap

    return None


# ---------------------------------------------------------------------------
# Index management
# ---------------------------------------------------------------------------


def _load_index(store: Any) -> List[str]:
    """Load the snapshot index (list of IDs, newest first)."""
    data = store.get_json("engine5:snapshots:index")
    if isinstance(data, list):
        return data
    return []


def persist_snapshot(
    store: Any,
    snapshot_id: str,
    meta: SnapshotMeta,
    data: dict,
    snapshot_ttl: int = 14 * 86400,
    index_ttl: int = 30 * 86400,
    max_index: int = 50,
) -> bool:
    """Persist an immutable snapshot and update index + pointers.

    Returns True on success.
    """
    payload = {
        "meta": meta.to_dict(),
        "data": data,
    }

    # 1. Write snapshot
    ok = store.set_json(f"engine5:snapshot:{snapshot_id}", payload, ttl_s=snapshot_ttl)
    if not ok:
        LOG.error("Failed to persist snapshot %s", snapshot_id)
        return False

    # 2. Update index (prepend, prune)
    index = _load_index(store)
    index = [snapshot_id] + [sid for sid in index if sid != snapshot_id]
    if len(index) > max_index:
        # Clean up old snapshots beyond the index limit
        for old_sid in index[max_index:]:
            store.delete_key(f"engine5:snapshot:{old_sid}")
        index = index[:max_index]
    store.set_json("engine5:snapshots:index", index, ttl_s=index_ttl)

    # 3. Update latest pointer (always)
    store.set_json("engine5:pointer:latest", snapshot_id, ttl_s=index_ttl)

    # 4. Update best pointer (only if grade A or B)
    if meta.grade in ("A", "B"):
        store.set_json("engine5:pointer:best", snapshot_id, ttl_s=index_ttl)

    LOG.info(
        "Persisted snapshot %s  grade=%s  completeness=%.2f  ideas=%d",
        snapshot_id, meta.grade, meta.completeness, meta.trade_ideas_count,
    )
    return True
