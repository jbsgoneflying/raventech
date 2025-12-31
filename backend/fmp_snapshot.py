from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

from backend.earnings_logic import classify_timing
from backend.fmp_client import FmpClient
from backend.redis_store import RedisStore
from backend.orats_client import OratsClient
from backend.universe import load_universe_sp500_and_nasdaq100


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


def _fmp_limit() -> int:
    # FMP stable endpoints often apply a default limit. Use a high cap to reduce truncation.
    return int(float(os.getenv("FMP_EARNINGS_CALENDAR_LIMIT") or 10000))


def _fmp_window_days() -> int:
    # Safety: fetch in smaller windows to reduce truncation risk even if FMP caps limit.
    return int(float(os.getenv("FMP_EARNINGS_CALENDAR_WINDOW_DAYS") or 14))


def _truthy(name: str, default: bool = False) -> bool:
    v = os.getenv(name)
    if v is None:
        return bool(default)
    s = str(v).strip().lower()
    return s in ("1", "true", "t", "yes", "y", "on")


def _universe_mode() -> str:
    """
    Universe mode for the FMP snapshot.

    - sp500_nasdaq100: filter to `data/universe/sp500.txt` + `data/universe/nasdaq100.txt` (default)
    - all: keep all FMP rows (can be huge)
    """
    v = str(os.getenv("FMP_EARNINGS_UNIVERSE") or "sp500_nasdaq100").strip().lower()
    return v or "sp500_nasdaq100"


def _normalize_symbol_to_universe(sym: str, universe: set[str]) -> str:
    """
    Normalize common share-class punctuation so universe membership checks are robust.

    Examples:
    - FMP may return BRK-B while our files contain BRK.B
    - Some feeds invert that convention
    """
    s = str(sym or "").strip().upper()
    if not s or not universe:
        return s
    if s in universe:
        return s
    if "-" in s:
        s2 = s.replace("-", ".")
        if s2 in universe:
            return s2
    if "." in s:
        s2 = s.replace(".", "-")
        if s2 in universe:
            return s2
    return s


def _overlay_timing_from_orats(tickers: List[str]) -> Dict[str, str]:
    """
    Optional overlay for timing (BMO/AMC) using ORATS /cores nextErnTod.
    This avoids relying on FMP providing time-of-day, and costs only during the daily snapshot refresh.
    """
    if not _truthy("FMP_EARNINGS_USE_ORATS_TOD", default=True):
        return {}
    if not os.getenv("ORATS_TOKEN"):
        return {}
    client = OratsClient.from_env()
    out: Dict[str, str] = {}
    for t in tickers:
        sym = str(t or "").strip().upper()
        if not sym:
            continue
        try:
            rows = client.cores(ticker=sym, fields="ticker,nextErnTod").rows or []
            row = rows[0] if rows else {}
            if not isinstance(row, dict):
                continue
            tp = classify_timing(row.get("nextErnTod"))
            if tp in ("AMC", "BMO"):
                out[sym] = tp
        except Exception:
            continue
    return out


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
    rows_fetched = 0
    windows = 0

    mode = _universe_mode()
    universe: set[str] = set()
    if mode != "all":
        universe = set(load_universe_sp500_and_nasdaq100())

    # Fetch in smaller windows to reduce the risk of truncation.
    rows: List[dict] = []
    step = max(1, int(_fmp_window_days()))
    cur = today
    limit = _fmp_limit()
    while cur <= end:
        w_end = min(end, cur + dt.timedelta(days=step - 1))
        windows += 1
        try:
            resp = client.earnings_calendar(date_from=_fmt_date(cur), date_to=_fmt_date(w_end), limit=limit)
            batch = resp.rows or []
            rows.extend([r for r in batch if isinstance(r, dict)])
            rows_fetched += int(len(batch))
        except Exception:
            errors += 1
        cur = w_end + dt.timedelta(days=1)

    # De-dupe rows defensively (ticker+date+timing).
    rows_dropped_universe = 0
    uniq: Dict[Tuple[str, str, str], dict] = {}
    for r in rows:
        sym0 = _coerce_ticker(r)
        sym = _normalize_symbol_to_universe(sym0, universe) if universe else sym0
        d = _coerce_date(r)
        if not sym or d is None:
            continue
        if universe and sym not in universe:
            rows_dropped_universe += 1
            continue
        tm = _coerce_timing(r)
        uniq[(sym, _fmt_date(d), tm)] = r
    rows = list(uniq.values())

    # Optional overlay of timing using ORATS nextErnTod (BMO/AMC).
    timing_overlay = _overlay_timing_from_orats([_normalize_symbol_to_universe(_coerce_ticker(r), universe) for r in rows])

    for r in rows:
        if not isinstance(r, dict):
            continue
        sym0 = _coerce_ticker(r)
        sym = _normalize_symbol_to_universe(sym0, universe) if universe else sym0
        if not sym:
            continue
        if universe and sym not in universe:
            continue
        d = _coerce_date(r)
        if d is None or d < today or d > end:
            continue
        timing = timing_overlay.get(sym) or _coerce_timing(r)
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
        "rowsFetched": int(rows_fetched),
        "windows": int(windows),
        "limit": int(limit),
        "universeMode": mode,
        "universeSize": int(len(universe)) if universe else None,
        "rowsDroppedUniverse": int(rows_dropped_universe),
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


