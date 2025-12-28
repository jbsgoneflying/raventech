from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.earnings_logic import classify_timing
from backend.fmp_client import FmpClient
from backend.redis_store import RedisStore


ET = ZoneInfo("America/New_York")

FMP_EARNINGS_SNAPSHOT_KEY = "calendar:earnings_snapshot_fmp:v1"
FMP_EARNINGS_LAST_REFRESH_ET_DATE_KEY = "calendar:lastRefreshETDate_fmp:v1"


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def _now_et() -> dt.datetime:
    return _now_utc().astimezone(ET)


def _snapshot_ttl_s() -> int:
    return int(float(os.getenv("CALENDAR_EARNINGS_SNAPSHOT_TTL_S") or (48 * 60 * 60)))


def should_refresh_today_et(*, now_et: dt.datetime, last_refresh_et_date: Optional[str]) -> bool:
    """
    True if:
    - current ET time is >= 04:00, and
    - last_refresh_et_date != today
    """
    if now_et.tzinfo is None:
        now_et = now_et.replace(tzinfo=ET)
    today = _fmt_date(now_et.date())
    if str(last_refresh_et_date or "")[:10] == today:
        return False
    gate = now_et.replace(hour=4, minute=0, second=0, microsecond=0)
    return now_et >= gate


def _coerce_ticker(row: dict) -> str:
    for k in ("symbol", "ticker", "Symbol", "Ticker"):
        v = row.get(k)
        if v:
            return str(v).strip().upper()
    return ""


def _coerce_date(row: dict) -> Optional[dt.date]:
    for k in ("date", "earningDate", "earningsDate", "reportedDate", "Date"):
        v = row.get(k)
        d = _parse_date(v)
        if d is not None:
            return d
    return None


def _coerce_timing(row: dict) -> str:
    """
    Map FMP timing/session-ish fields to AMC/BMO/UNK.

    FMP rows commonly include some combination of:
    - time: "afterClose" | "beforeOpen" | "amc" | "bmo" | "pm" | "am"
    - or a human string like "After Market Close"
    """
    for k in ("time", "session", "when", "timing", "timeOfDay"):
        v = row.get(k)
        if v is None:
            continue
        # Let our existing classifier handle strings like AMC/BMO or numeric HHMM.
        t = classify_timing(v)
        if t in ("AMC", "BMO"):
            return t
        s = str(v).strip().lower()
        if "after" in s or "close" in s or s in ("amc", "afterclose", "post", "pm", "postmarket", "afterhours"):
            return "AMC"
        if "before" in s or "open" in s or s in ("bmo", "beforeopen", "pre", "am", "premarket"):
            return "BMO"
    return "UNK"


def build_fmp_earnings_snapshot(
    client: FmpClient,
    *,
    now_et: dt.datetime,
    horizon_days: int = 180,
) -> Dict[str, Any]:
    today = now_et.date()
    end = today + dt.timedelta(days=int(horizon_days))

    rows_used = 0
    errors = 0
    by_date: Dict[str, Dict[str, List[str]]] = {}

    try:
        resp = client.earnings_calendar(date_from=_fmt_date(today), date_to=_fmt_date(end))
        rows = resp.rows or []
    except Exception:
        rows = []
        errors += 1

    for r in rows:
        if not isinstance(r, dict):
            continue
        sym = _coerce_ticker(r)
        if not sym:
            continue
        d = _coerce_date(r)
        if d is None or d < today or d > end:
            continue
        timing = _coerce_timing(r)
        k = _fmt_date(d)
        if k not in by_date:
            by_date[k] = {"BMO": [], "AMC": [], "UNK": []}
        by_date[k][timing].append(sym)
        rows_used += 1

    # stable + unique
    for d0 in list(by_date.keys()):
        for k in ("BMO", "AMC", "UNK"):
            by_date[d0][k] = sorted(list(dict.fromkeys(by_date[d0][k])))

    meta = {
        "refreshedAtUtc": _now_utc().isoformat(),
        "refreshedAtET": now_et.isoformat(),
        "etDate": _fmt_date(today),
        "horizonDays": int(horizon_days),
        "rowsUsed": int(rows_used),
        "errors": int(errors),
        "source": "fmp:earnings-calendar",
        "notes": [],
    }
    return {"meta": meta, "byDate": by_date}


@dataclass(frozen=True)
class RefreshResult:
    ok: bool
    etDate: str
    rowsUsed: int
    byDateSize: int
    errors: int
    notes: List[str]


def refresh_fmp_earnings_snapshot_if_needed(
    client: FmpClient,
    store: RedisStore,
    *,
    now_et: Optional[dt.datetime] = None,
    horizon_days: int = 180,
    force: bool = False,
) -> RefreshResult:
    now = now_et or _now_et()
    et_date = _fmt_date(now.date())
    last = store.get_json(FMP_EARNINGS_LAST_REFRESH_ET_DATE_KEY)
    last_s = str(last)[:10] if last is not None else None

    if (not force) and (not should_refresh_today_et(now_et=now, last_refresh_et_date=last_s)):
        return RefreshResult(ok=True, etDate=et_date, rowsUsed=0, byDateSize=0, errors=0, notes=["No refresh needed."])

    snap = build_fmp_earnings_snapshot(client, now_et=now, horizon_days=int(horizon_days))
    ttl = _snapshot_ttl_s()
    ok1 = store.set_json(FMP_EARNINGS_SNAPSHOT_KEY, snap, ttl_s=ttl)
    ok2 = store.set_json(FMP_EARNINGS_LAST_REFRESH_ET_DATE_KEY, et_date, ttl_s=ttl)

    meta = snap.get("meta") if isinstance(snap.get("meta"), dict) else {}
    by_date = snap.get("byDate") if isinstance(snap.get("byDate"), dict) else {}
    notes = ["Refreshed FMP earnings snapshot (forced)."] if (force and ok1 and ok2) else (
        ["Refreshed FMP earnings snapshot."] if (ok1 and ok2) else ["Failed to write FMP snapshot to Redis."]
    )

    return RefreshResult(
        ok=bool(ok1 and ok2),
        etDate=str(meta.get("etDate") or et_date)[:10],
        rowsUsed=int(meta.get("rowsUsed") or 0),
        byDateSize=int(len(by_date)),
        errors=int(meta.get("errors") or 0),
        notes=notes,
    )


def load_fmp_earnings_snapshot(store: RedisStore) -> Optional[Dict[str, Any]]:
    snap = store.get_json(FMP_EARNINGS_SNAPSHOT_KEY)
    return snap if isinstance(snap, dict) else None


