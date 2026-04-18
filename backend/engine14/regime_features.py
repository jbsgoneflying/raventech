"""Engine 14 — multi-factor regime features (Phase C1).

Persists a per-trade-day snapshot of the macro / vol / positioning state so
the analogue-matching step can eventually do a KNN nearest-neighbor match
instead of the single RV20 percentile bucket we ship today.

Fields
------
VIX, VIX9D, VVIX are the standard vol-surface inputs. `term_slope` is
`VIX9D - VIX` (positive = front-loaded fear). Dealer-gamma and credit-stress
fields are *best-effort*: we store whatever is in the DMS snapshot for the
given date, and leave them NULL when not available (the backfill script
doesn't historically reconstruct those signals).

Schema
------
    CREATE TABLE IF NOT EXISTS regime_features (
        trade_date            TEXT PRIMARY KEY,
        spx_close             REAL,
        vix                   REAL,
        vix9d                 REAL,
        vvix                  REAL,
        term_slope            REAL,
        rv20                  REAL,
        dealer_gamma_sign     TEXT,   -- "POSITIVE" | "NEUTRAL" | "NEGATIVE"
        dealer_gamma_mag      TEXT,   -- "low" | "medium" | "high"
        dealer_gamma_net_gex  REAL,
        credit_stress_label   TEXT,   -- "Risk-On" | "Neutral" | "Risk-Off" | "Stressed"
        credit_stress_score   REAL,
        updated_at            TEXT
    );

Phase C2 will consume this table in `analogue_matcher.filter_analogues`.
Until then, the table is read-only documentation of market state and the
legacy RV20 bucket remains the authority for analogue selection.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import os
import sqlite3
import statistics
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Optional, Tuple

from backend.config import get_flags

LOG = logging.getLogger("engine14.regime_features")


@dataclass(frozen=True)
class RegimeFeatures:
    trade_date: str
    spx_close: Optional[float] = None
    vix: Optional[float] = None
    vix9d: Optional[float] = None
    vvix: Optional[float] = None
    term_slope: Optional[float] = None
    rv20: Optional[float] = None
    dealer_gamma_sign: Optional[str] = None
    dealer_gamma_mag: Optional[str] = None
    dealer_gamma_net_gex: Optional[float] = None
    credit_stress_label: Optional[str] = None
    credit_stress_score: Optional[float] = None
    updated_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "tradeDate": self.trade_date,
            "spxClose": self.spx_close,
            "vix": self.vix, "vix9d": self.vix9d, "vvix": self.vvix,
            "termSlope": self.term_slope, "rv20": self.rv20,
            "dealerGammaSign": self.dealer_gamma_sign,
            "dealerGammaMagnitude": self.dealer_gamma_mag,
            "dealerGammaNetGex": self.dealer_gamma_net_gex,
            "creditStressLabel": self.credit_stress_label,
            "creditStressScore": self.credit_stress_score,
            "updatedAt": self.updated_at,
        }

    def feature_vector(self) -> List[Optional[float]]:
        """Numeric view used by Phase C2 KNN.

        Order: [vix, vix9d, vvix, term_slope, rv20, net_gex, credit_score].
        NULLs are preserved for the caller to handle (e.g. impute with
        training-set medians).
        """
        return [
            self.vix, self.vix9d, self.vvix,
            self.term_slope, self.rv20,
            self.dealer_gamma_net_gex,
            self.credit_stress_score,
        ]


# ---------------------------------------------------------------------------
# SQLite
# ---------------------------------------------------------------------------

_db_lock = threading.Lock()


def _resolve_db_path() -> str:
    flags = get_flags()
    raw = str(getattr(flags, "ENGINE14_REGIME_FEATURES_PATH", None)
              or "data/engine14_regime_features.db")
    p = Path(raw)
    if not p.is_absolute():
        root = Path(__file__).resolve().parent.parent.parent
        p = (root / raw).resolve()
    return str(p)


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _resolve_db_path()
    parent = Path(path).parent
    try:
        parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        LOG.warning("regime_features dir not writable (%s) — using in-memory DB.", parent)
        path = ":memory:"
    with _db_lock:
        conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            _ensure_schema(conn)
            yield conn
        finally:
            conn.close()


def _ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS regime_features (
            trade_date            TEXT PRIMARY KEY,
            spx_close             REAL,
            vix                   REAL,
            vix9d                 REAL,
            vvix                  REAL,
            term_slope            REAL,
            rv20                  REAL,
            dealer_gamma_sign     TEXT,
            dealer_gamma_mag      TEXT,
            dealer_gamma_net_gex  REAL,
            credit_stress_label   TEXT,
            credit_stress_score   REAL,
            updated_at            TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_rf_td ON regime_features(trade_date);
        """
    )


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------

def _row_to_features(row: sqlite3.Row) -> RegimeFeatures:
    return RegimeFeatures(
        trade_date=str(row["trade_date"]),
        spx_close=row["spx_close"],
        vix=row["vix"], vix9d=row["vix9d"], vvix=row["vvix"],
        term_slope=row["term_slope"], rv20=row["rv20"],
        dealer_gamma_sign=row["dealer_gamma_sign"],
        dealer_gamma_mag=row["dealer_gamma_mag"],
        dealer_gamma_net_gex=row["dealer_gamma_net_gex"],
        credit_stress_label=row["credit_stress_label"],
        credit_stress_score=row["credit_stress_score"],
        updated_at=row["updated_at"],
    )


def upsert_features(feats: RegimeFeatures) -> None:
    """Insert-or-replace a per-date features row."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO regime_features(
                trade_date, spx_close, vix, vix9d, vvix, term_slope, rv20,
                dealer_gamma_sign, dealer_gamma_mag, dealer_gamma_net_gex,
                credit_stress_label, credit_stress_score, updated_at
            ) VALUES (?,?,?,?,?,?,?, ?,?,?, ?,?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                spx_close=excluded.spx_close,
                vix=excluded.vix, vix9d=excluded.vix9d, vvix=excluded.vvix,
                term_slope=excluded.term_slope, rv20=excluded.rv20,
                dealer_gamma_sign=excluded.dealer_gamma_sign,
                dealer_gamma_mag=excluded.dealer_gamma_mag,
                dealer_gamma_net_gex=excluded.dealer_gamma_net_gex,
                credit_stress_label=excluded.credit_stress_label,
                credit_stress_score=excluded.credit_stress_score,
                updated_at=excluded.updated_at
            """,
            (
                feats.trade_date, feats.spx_close, feats.vix, feats.vix9d, feats.vvix,
                feats.term_slope, feats.rv20,
                feats.dealer_gamma_sign, feats.dealer_gamma_mag, feats.dealer_gamma_net_gex,
                feats.credit_stress_label, feats.credit_stress_score, now,
            ),
        )


def upsert_features_many(rows: Iterable[RegimeFeatures]) -> int:
    """Bulk upsert. Returns number of rows written."""
    n = 0
    for r in rows:
        upsert_features(r)
        n += 1
    return n


def fetch_features(trade_date: str) -> Optional[RegimeFeatures]:
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT * FROM regime_features WHERE trade_date = ?", (trade_date,)
        ).fetchone()
    return _row_to_features(row) if row else None


def fetch_features_range(*, start: str, end: str) -> List[RegimeFeatures]:
    """Return rows in [start, end] ascending by date."""
    with _connect() as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT * FROM regime_features WHERE trade_date BETWEEN ? AND ? "
            "ORDER BY trade_date ASC",
            (start, end),
        ).fetchall()
    return [_row_to_features(r) for r in rows]


def cached_trade_dates() -> List[str]:
    with _connect() as conn:
        rows = conn.execute(
            "SELECT trade_date FROM regime_features ORDER BY trade_date ASC"
        ).fetchall()
    return [str(r[0]) for r in rows]


def coverage() -> Dict[str, Any]:
    """Shape mirrors `chain_cache.cache_coverage` for consistency."""
    with _connect() as conn:
        rows = conn.execute(
            "SELECT trade_date, vix, vix9d, vvix, rv20, dealer_gamma_sign, credit_stress_label "
            "FROM regime_features ORDER BY trade_date ASC"
        ).fetchall()
    if not rows:
        return {"daysCovered": 0, "firstDate": None, "lastDate": None, "fieldCoverage": {}}
    tds = [str(r[0]) for r in rows]

    def _pct(field_idx: int) -> float:
        return round(100.0 * sum(1 for r in rows if r[field_idx] is not None) / len(rows), 1)

    return {
        "daysCovered": len(rows),
        "firstDate": tds[0],
        "lastDate": tds[-1],
        "fieldCoverage": {
            "vix":        _pct(1),
            "vix9d":      _pct(2),
            "vvix":       _pct(3),
            "rv20":       _pct(4),
            "dealerGamma": _pct(5),
            "creditStress": _pct(6),
        },
    }


# ---------------------------------------------------------------------------
# Feature computation from live sources
# ---------------------------------------------------------------------------

def _safe_close(bars: List[Any], trade_date: str) -> Optional[float]:
    """Look up the close for `trade_date` in a sorted bar list."""
    for b in bars or []:
        td = getattr(b, "trade_date", None) or (b.get("trade_date") if isinstance(b, dict) else None)
        if td == trade_date:
            cl = getattr(b, "close", None) or (b.get("close") if isinstance(b, dict) else None)
            try:
                return float(cl) if cl is not None else None
            except (TypeError, ValueError):
                return None
    return None


def _compute_rv20(spx_closes: List[float]) -> Optional[float]:
    """20-session annualized realized vol (log returns)."""
    if len(spx_closes) < 21:
        return None
    logs: List[float] = []
    for i in range(1, len(spx_closes)):
        a, b = spx_closes[i - 1], spx_closes[i]
        if a and a > 0 and b and b > 0:
            logs.append(math.log(b / a))
    if len(logs) < 20:
        return None
    last = logs[-20:]
    return statistics.stdev(last) * math.sqrt(252.0)


def compute_features_for_range(
    *,
    price_service: Any,
    start: dt.date,
    end: dt.date,
    store: Any = None,
) -> List[RegimeFeatures]:
    """Build a batch of RegimeFeatures across [start, end].

    Required dependencies:
      - `price_service`: an instance of `backend.price_service.PriceService`.
        Fetches SPX / VIX / VIX9D / VVIX daily bars via EODHD.
      - `store` (optional): Redis store for DMS cross-asset-stress lookup.
        When omitted, credit-stress fields are NULL.

    Dealer-gamma fields are not historically reconstructible and are always
    NULL from this backfill. They will be populated by a live-capture job
    (Phase C1 follow-up) or remain NULL until live snapshots accumulate.
    """
    if end < start:
        return []

    # Fetch daily bars for SPX and the VIX complex in one round trip each.
    # SPX pulled with a 25-session prefix so we can compute RV20 from day 1.
    pre = start - dt.timedelta(days=60)
    try:
        spx_bars = price_service.fetch_daily_bars("SPX", pre, end)
    except Exception as e:
        LOG.warning("SPX bars fetch failed: %s", e)
        spx_bars = []
    try:
        vix_bars = price_service.fetch_daily_bars("VIX", start, end)
    except Exception as e:
        LOG.warning("VIX bars fetch failed: %s", e)
        vix_bars = []
    try:
        # VIX9D and VVIX use qualified EODHD symbols (pass-through).
        vix9d_bars = price_service.fetch_daily_bars("VIX9D.INDX", start, end)
    except Exception as e:
        LOG.warning("VIX9D bars fetch failed: %s", e)
        vix9d_bars = []
    try:
        vvix_bars = price_service.fetch_daily_bars("VVIX.INDX", start, end)
    except Exception as e:
        LOG.warning("VVIX bars fetch failed: %s", e)
        vvix_bars = []

    # SPX close series for rolling RV20.
    spx_series: List[Tuple[str, float]] = []
    for b in spx_bars:
        td = getattr(b, "trade_date", None)
        cl = getattr(b, "close", None)
        if td and cl is not None and float(cl) > 0:
            spx_series.append((str(td), float(cl)))
    spx_series.sort(key=lambda r: r[0])

    # Rolling RV20: for each date, use the last 21 closes ending on that date.
    rv20_by_date: Dict[str, float] = {}
    closes_series: List[float] = []
    date_series: List[str] = []
    for td, cl in spx_series:
        closes_series.append(cl)
        date_series.append(td)
        if len(closes_series) >= 21:
            rv = _compute_rv20(closes_series[-21:])
            if rv is not None:
                rv20_by_date[td] = rv

    # DMS cross-asset stress per date (best-effort).
    dms_by_date: Dict[str, Tuple[Optional[str], Optional[float]]] = {}
    if store is not None:
        try:
            from backend.daily_market_state import load_dms
        except Exception:
            load_dms = None
        if load_dms is not None:
            cur = start
            while cur <= end:
                iso = cur.isoformat()
                try:
                    dms = load_dms(iso, store)
                except Exception:
                    dms = None
                if dms is not None:
                    cas = getattr(dms, "cross_asset_stress", {}) or {}
                    dms_by_date[iso] = (
                        str(cas.get("composite_label") or "").strip() or None,
                        float(cas.get("composite_score")) if cas.get("composite_score") is not None else None,
                    )
                cur += dt.timedelta(days=1)

    # Iterate trading days (use VIX dates as canonical — holidays excluded).
    target_dates = {getattr(b, "trade_date", None) for b in vix_bars if getattr(b, "close", None) is not None}
    target_dates |= {td for td, _ in spx_series if start.isoformat() <= td <= end.isoformat()}
    target_dates.discard(None)

    out: List[RegimeFeatures] = []
    for td in sorted(t for t in target_dates if t and start.isoformat() <= t <= end.isoformat()):
        spx_close = _safe_close(spx_bars, td)
        vix = _safe_close(vix_bars, td)
        v9 = _safe_close(vix9d_bars, td)
        vv = _safe_close(vvix_bars, td)
        term_slope: Optional[float] = None
        if vix is not None and v9 is not None:
            term_slope = float(v9) - float(vix)
        rv20 = rv20_by_date.get(td)
        stress_label, stress_score = dms_by_date.get(td, (None, None))
        out.append(RegimeFeatures(
            trade_date=td, spx_close=spx_close,
            vix=vix, vix9d=v9, vvix=vv,
            term_slope=term_slope, rv20=rv20,
            dealer_gamma_sign=None, dealer_gamma_mag=None, dealer_gamma_net_gex=None,
            credit_stress_label=stress_label, credit_stress_score=stress_score,
        ))
    return out


def purge_range(*, start: str, end: str) -> int:
    """Delete features rows in [start, end]. Returns rows deleted."""
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM regime_features WHERE trade_date BETWEEN ? AND ?",
            (start, end),
        )
        return int(cur.rowcount or 0)
