"""Engine 14 — ORATS historical option-chain cache (SQLite-backed).

Why a dedicated cache?
----------------------
`OratsClient` already has an in-memory TTLCache, but:

  * The simulator needs to reprice ICs across *every trading day* in a
    replay window (5-7 days × N analogues = hundreds of per-day chain
    lookups per user request). In-memory TTL is too lossy for that.
  * A 2-year SPX backfill is ~500 calls — expensive to repeat. Persist to
    disk so the droplet keeps a warm chain after restarts.
  * Other engines occasionally need the same slice; a shared read-only
    table is friendlier than bespoke per-engine caches.

Schema
------
Single de-normalized table keyed by (trade_date, ticker, expiry, strike):

    CREATE TABLE IF NOT EXISTS spx_chain_cache (
        trade_date TEXT NOT NULL,     -- YYYY-MM-DD (tradeDate)
        ticker     TEXT NOT NULL,     -- e.g. SPX
        expiry     TEXT NOT NULL,     -- YYYY-MM-DD (expirDate)
        strike     REAL NOT NULL,
        spot       REAL,              -- underlying spot as of trade_date
        call_bid   REAL, call_ask   REAL, call_mid   REAL, call_iv REAL,
        put_bid    REAL, put_ask    REAL, put_mid    REAL, put_iv  REAL,
        call_oi    INTEGER, put_oi  INTEGER,
        PRIMARY KEY (trade_date, ticker, expiry, strike)
    );

Indices over (ticker, trade_date) and (ticker, expiry) let the simulator
pull a full chain slice in a single range query.
"""

from __future__ import annotations

import datetime as dt
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from backend.config import get_flags
from backend.orats_client import OratsClient, OratsError

LOG = logging.getLogger("engine14.chain_cache")

# Fields we request from ORATS /hist/strikes. Keep in sync with _row_to_rec.
_HIST_STRIKES_FIELDS = (
    "ticker,tradeDate,expirDate,strike,spotPrice,stockPrice,"
    "callBidPrice,callAskPrice,callMidPrice,callMidIv,"
    "putBidPrice,putAskPrice,putMidPrice,putMidIv,"
    "callOpenInterest,putOpenInterest"
)


@dataclass(frozen=True)
class ChainRow:
    trade_date: str
    ticker: str
    expiry: str
    strike: float
    spot: Optional[float]
    call_bid: Optional[float]
    call_ask: Optional[float]
    call_mid: Optional[float]
    call_iv: Optional[float]
    put_bid: Optional[float]
    put_ask: Optional[float]
    put_mid: Optional[float]
    put_iv: Optional[float]
    call_oi: Optional[int]
    put_oi: Optional[int]

    def call_mid_px(self) -> Optional[float]:
        if self.call_mid is not None and self.call_mid > 0:
            return float(self.call_mid)
        if self.call_bid is not None and self.call_ask is not None and self.call_ask > 0:
            return (float(self.call_bid) + float(self.call_ask)) / 2.0
        return None

    def put_mid_px(self) -> Optional[float]:
        if self.put_mid is not None and self.put_mid > 0:
            return float(self.put_mid)
        if self.put_bid is not None and self.put_ask is not None and self.put_ask > 0:
            return (float(self.put_bid) + float(self.put_ask)) / 2.0
        return None


_db_lock = threading.Lock()


def _resolve_db_path() -> str:
    flags = get_flags()
    raw = str(getattr(flags, "ENGINE14_SQLITE_PATH", None) or "data/engine14_chains.db")
    # Respect absolute or relative paths.
    p = Path(raw)
    if not p.is_absolute():
        # Resolve relative to the workspace root (two levels above this file).
        here = Path(__file__).resolve()
        root = here.parent.parent.parent
        p = (root / raw).resolve()
    return str(p)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _resolve_db_path()
    parent = Path(path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        # If the parent can't be created (e.g. read-only mount), fall back to :memory:
        # This mainly protects unit tests from spurious IO errors.
        LOG.warning("engine14 cache dir not writable (%s) — using in-memory DB.", parent)
        path = ":memory:"

    with _db_lock:
        conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute("PRAGMA temp_store=MEMORY;")
            _ensure_schema(conn)
            yield conn
        finally:
            conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS spx_chain_cache (
            trade_date TEXT NOT NULL,
            ticker     TEXT NOT NULL,
            expiry     TEXT NOT NULL,
            strike     REAL NOT NULL,
            spot       REAL,
            call_bid   REAL,
            call_ask   REAL,
            call_mid   REAL,
            call_iv    REAL,
            put_bid    REAL,
            put_ask    REAL,
            put_mid    REAL,
            put_iv     REAL,
            call_oi    INTEGER,
            put_oi     INTEGER,
            PRIMARY KEY (trade_date, ticker, expiry, strike)
        );

        CREATE INDEX IF NOT EXISTS idx_chain_tkr_td
            ON spx_chain_cache(ticker, trade_date);
        CREATE INDEX IF NOT EXISTS idx_chain_tkr_exp
            ON spx_chain_cache(ticker, expiry);

        CREATE TABLE IF NOT EXISTS spx_chain_manifest (
            ticker     TEXT NOT NULL,
            trade_date TEXT NOT NULL,
            rows       INTEGER NOT NULL,
            fetched_at TEXT NOT NULL,
            PRIMARY KEY (ticker, trade_date)
        );
        """
    )


# ---- row conversion ----

def _to_float(v: Any) -> Optional[float]:
    if v is None:
        return None
    try:
        x = float(v)
        if x != x:  # NaN
            return None
        return x
    except (TypeError, ValueError):
        return None


def _to_int(v: Any) -> Optional[int]:
    x = _to_float(v)
    return None if x is None else int(x)


def _row_to_rec(row: dict, *, ticker: str, trade_date: str) -> Optional[Tuple]:
    if not isinstance(row, dict):
        return None
    expiry = str(row.get("expirDate") or row.get("expiry") or row.get("expDate") or "")[:10]
    strike = _to_float(row.get("strike"))
    if not expiry or strike is None:
        return None
    return (
        trade_date,
        ticker,
        expiry,
        float(strike),
        _to_float(row.get("spotPrice") or row.get("stockPrice")),
        _to_float(row.get("callBidPrice")),
        _to_float(row.get("callAskPrice")),
        _to_float(row.get("callMidPrice")),
        _to_float(row.get("callMidIv")),
        _to_float(row.get("putBidPrice")),
        _to_float(row.get("putAskPrice")),
        _to_float(row.get("putMidPrice")),
        _to_float(row.get("putMidIv")),
        _to_int(row.get("callOpenInterest")),
        _to_int(row.get("putOpenInterest")),
    )


def _rec_to_chainrow(rec: Tuple) -> ChainRow:
    return ChainRow(
        trade_date=str(rec[0]),
        ticker=str(rec[1]),
        expiry=str(rec[2]),
        strike=float(rec[3]),
        spot=(None if rec[4] is None else float(rec[4])),
        call_bid=(None if rec[5] is None else float(rec[5])),
        call_ask=(None if rec[6] is None else float(rec[6])),
        call_mid=(None if rec[7] is None else float(rec[7])),
        call_iv=(None if rec[8] is None else float(rec[8])),
        put_bid=(None if rec[9] is None else float(rec[9])),
        put_ask=(None if rec[10] is None else float(rec[10])),
        put_mid=(None if rec[11] is None else float(rec[11])),
        put_iv=(None if rec[12] is None else float(rec[12])),
        call_oi=(None if rec[13] is None else int(rec[13])),
        put_oi=(None if rec[14] is None else int(rec[14])),
    )


# ---- public API ----

def fetch_chain_slice(
    *,
    ticker: str,
    trade_date: str,
    expiry: str,
) -> List[ChainRow]:
    """Return all strikes for (ticker, trade_date, expiry), sorted by strike."""
    ticker = str(ticker).upper()
    td = str(trade_date)[:10]
    exp = str(expiry)[:10]
    with _connect() as conn:
        cur = conn.execute(
            """SELECT trade_date,ticker,expiry,strike,spot,
                      call_bid,call_ask,call_mid,call_iv,
                      put_bid,put_ask,put_mid,put_iv,
                      call_oi,put_oi
               FROM spx_chain_cache
               WHERE ticker=? AND trade_date=? AND expiry=?
               ORDER BY strike ASC""",
            (ticker, td, exp),
        )
        return [_rec_to_chainrow(r) for r in cur.fetchall()]


def fetch_expiries_on(*, ticker: str, trade_date: str) -> List[str]:
    """Distinct expiries cached for (ticker, trade_date). Sorted ascending."""
    ticker = str(ticker).upper()
    td = str(trade_date)[:10]
    with _connect() as conn:
        cur = conn.execute(
            """SELECT DISTINCT expiry FROM spx_chain_cache
               WHERE ticker=? AND trade_date=? ORDER BY expiry ASC""",
            (ticker, td),
        )
        return [str(r[0]) for r in cur.fetchall()]


def fetch_cached_trade_dates(*, ticker: str) -> List[str]:
    """All trade_dates we have rows for. Sorted ascending."""
    ticker = str(ticker).upper()
    with _connect() as conn:
        cur = conn.execute(
            """SELECT trade_date FROM spx_chain_manifest
               WHERE ticker=? ORDER BY trade_date ASC""",
            (ticker,),
        )
        return [str(r[0]) for r in cur.fetchall()]


def cache_coverage(*, ticker: str) -> Dict[str, Any]:
    """Compact manifest summary for telemetry / health checks."""
    ticker = str(ticker).upper()
    with _connect() as conn:
        cur = conn.execute(
            """SELECT COUNT(*), MIN(trade_date), MAX(trade_date), SUM(rows)
               FROM spx_chain_manifest WHERE ticker=?""",
            (ticker,),
        )
        row = cur.fetchone() or (0, None, None, 0)
        return {
            "ticker": ticker,
            "daysCovered": int(row[0] or 0),
            "minDate": row[1],
            "maxDate": row[2],
            "totalRows": int(row[3] or 0),
            "dbPath": _resolve_db_path(),
        }


def has_trade_date(*, ticker: str, trade_date: str) -> bool:
    ticker = str(ticker).upper()
    td = str(trade_date)[:10]
    with _connect() as conn:
        cur = conn.execute(
            """SELECT 1 FROM spx_chain_manifest
               WHERE ticker=? AND trade_date=? LIMIT 1""",
            (ticker, td),
        )
        return cur.fetchone() is not None


def upsert_chain(
    *,
    ticker: str,
    trade_date: str,
    rows: Iterable[dict],
) -> int:
    """Replace any cached rows for (ticker, trade_date) with the provided set.

    We wipe-then-insert because ORATS publishes one canonical snapshot per
    EOD; a partial re-fetch should supersede the prior (possibly incomplete)
    one. Returns the number of rows inserted.
    """
    ticker = str(ticker).upper()
    td = str(trade_date)[:10]
    recs: List[Tuple] = []
    for r in rows or []:
        rec = _row_to_rec(r, ticker=ticker, trade_date=td)
        if rec is not None:
            recs.append(rec)

    with _connect() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            conn.execute(
                "DELETE FROM spx_chain_cache WHERE ticker=? AND trade_date=?",
                (ticker, td),
            )
            if recs:
                conn.executemany(
                    """INSERT INTO spx_chain_cache
                       (trade_date,ticker,expiry,strike,spot,
                        call_bid,call_ask,call_mid,call_iv,
                        put_bid,put_ask,put_mid,put_iv,
                        call_oi,put_oi)
                       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    recs,
                )
            conn.execute(
                """INSERT OR REPLACE INTO spx_chain_manifest
                   (ticker,trade_date,rows,fetched_at)
                   VALUES (?,?,?,?)""",
                (ticker, td, len(recs), dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"),
            )
            conn.execute("COMMIT;")
        except Exception:
            conn.execute("ROLLBACK;")
            raise
    return len(recs)


def fetch_and_cache_day(
    client: OratsClient,
    *,
    ticker: str,
    trade_date: str,
    max_dte: int = 45,
) -> int:
    """Pull one EOD chain from ORATS and write it to the cache.

    `max_dte` bounds the request to weekly-IC-relevant expirations so we
    don't balloon the cache with LEAP junk.
    Returns row count persisted. Raises OratsError on HTTP issues.
    """
    ticker = str(ticker).upper()
    td = str(trade_date)[:10]
    try:
        resp = client.hist_strikes(
            ticker=ticker,
            trade_date=td,
            fields=_HIST_STRIKES_FIELDS,
            dte=f"0,{int(max_dte)}",
        )
    except OratsError:
        raise
    except Exception as e:
        raise OratsError(f"hist_strikes fetch failed for {ticker} {td}: {type(e).__name__}: {e}") from e
    rows = list(resp.rows or [])
    if not rows:
        LOG.info("engine14 cache: no rows for %s %s (td may be non-trading).", ticker, td)
    return upsert_chain(ticker=ticker, trade_date=td, rows=rows)


def purge(*, ticker: Optional[str] = None) -> int:
    """Clear cached rows. Returns rows deleted. Guarded — callers must be explicit."""
    with _connect() as conn:
        if ticker is None:
            n1 = conn.execute("DELETE FROM spx_chain_cache").rowcount
            conn.execute("DELETE FROM spx_chain_manifest")
        else:
            t = str(ticker).upper()
            n1 = conn.execute("DELETE FROM spx_chain_cache WHERE ticker=?", (t,)).rowcount
            conn.execute("DELETE FROM spx_chain_manifest WHERE ticker=?", (t,))
    return int(n1 or 0)
