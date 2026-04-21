"""Calibration pipeline for Market Intelligence v2.

Builds a historical T × F factor matrix by replaying daily prices, then
fits the HMM and persists the model to Redis + disk.

Designed to be called either from:

- ``scripts/mi_calibrate.py`` (cron / manual)
- ``POST /api/market-intel/calibrate`` (admin-gated HTTP)
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.market_intel.factors import (
    FACTOR_KEYS,
    MIN_Z_OBSERVATIONS,
    _parse_bar_closes,
    _pct_returns,
    _pairwise_corr,
    _realized_vol_annualized,
    _rolling_z,
)
from backend.market_intel.regime_model import (
    CalibratedModel,
    fit_model,
    model_to_redis,
    save_model,
)
from backend.market_intel.regime_service import _model_path, _redis_key

LOG = logging.getLogger("market_intel.calibration")


@dataclass
class CalibrationReport:
    started_at:      str = ""
    finished_at:     str = ""
    ok:              bool = False
    model_version:   str = ""
    training_days:   int = 0
    log_likelihood:  float = 0.0
    persist_disk:    bool = False
    persist_redis:   bool = False
    error:           Optional[str] = None
    note:            str = ""
    factor_coverage: Dict[str, int] = field(default_factory=dict)  # factor → days_available

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Factor history builders
# ---------------------------------------------------------------------------


def _fetch_series(eodhd_client, symbol: str) -> Tuple[List[float], List[str]]:
    try:
        resp = eodhd_client.get_eod(symbol, period="d")
        return _parse_bar_closes(resp.rows if resp else [])
    except Exception as e:
        LOG.warning("mi_calibrate: fetch %s failed: %s", symbol, e)
        return [], []


def _align_by_date(
    series: Dict[str, Tuple[List[float], List[str]]],
) -> Tuple[List[str], Dict[str, List[float]]]:
    """Intersect date axes so every ticker has the same length."""
    date_sets = [set(dates) for _, dates in series.values() if dates]
    if not date_sets:
        return [], {}
    common = sorted(set.intersection(*date_sets))
    aligned: Dict[str, List[float]] = {}
    for sym, (closes, dates) in series.items():
        if not dates:
            continue
        date_to_close = dict(zip(dates, closes))
        aligned[sym] = [date_to_close[d] for d in common if d in date_to_close]
    return common, aligned


def build_historical_factor_matrix(
    eodhd_client: Any,
    *,
    lookback_days: int = 1260,
) -> Tuple[List[List[float]], Dict[str, int]]:
    """Build the T × F z-scored factor matrix for HMM training.

    We replay each factor's rolling construction day-by-day, producing a
    time series of per-factor z-scores. Missing factors on a given day
    are filled with 0.0 (the neutral z).

    Returns (matrix, coverage_report).
    """
    # ------------------------------------------------------------------
    # Pull all needed series once.
    # ------------------------------------------------------------------
    symbols = {
        "spy":   "SPY.US",
        "vix":   "VIX.INDX",
        "vix3m": "VIX3M.INDX",
        "hyg":   "HYG.US",
        "lqd":   "LQD.US",
        "dxy":   "UUP.US",
        "uso":   "USO.US",
        "gld":   "GLD.US",
        "btc":   "BTC-USD.CC",
        "xlk":   "XLK.US",
        "xlf":   "XLF.US",
        "xle":   "XLE.US",
        "xlv":   "XLV.US",
        "xlu":   "XLU.US",
        "xly":   "XLY.US",
    }
    raw: Dict[str, Tuple[List[float], List[str]]] = {}
    for name, sym in symbols.items():
        closes, dates = _fetch_series(eodhd_client, sym)
        if closes:
            # Keep only the lookback window (plus padding for z-windows).
            cap = min(len(closes), lookback_days + 400)
            raw[name] = (closes[-cap:], dates[-cap:])

    if len(raw) < 5:
        LOG.error("mi_calibrate: only %d series fetched; aborting", len(raw))
        return [], {k: len(v[0]) for k, v in raw.items()}

    # Align to a common date axis.
    common_dates, aligned = _align_by_date(raw)
    if len(common_dates) < MIN_Z_OBSERVATIONS + 50:
        LOG.error("mi_calibrate: only %d common days; need >= 110", len(common_dates))
        return [], {k: len(v) for k, v in aligned.items()}

    T = len(common_dates)
    coverage = {k: len(v) for k, v in aligned.items()}

    # ------------------------------------------------------------------
    # Pre-compute per-factor time series (raw, not z-scored yet).
    # ------------------------------------------------------------------
    spy = aligned.get("spy", [])
    vix = aligned.get("vix", [])
    vix3m = aligned.get("vix3m", [])
    hyg = aligned.get("hyg", [])
    lqd = aligned.get("lqd", [])
    dxy = aligned.get("dxy", [])
    uso = aligned.get("uso", [])
    gld = aligned.get("gld", [])
    btc = aligned.get("btc", [])
    sectors = [aligned.get(k, []) for k in ("xlk", "xlf", "xle", "xlv", "xlu", "xly")]
    sectors = [s for s in sectors if len(s) == T]

    # rv_spx_20d series
    rv_series: List[float] = []
    for t in range(T):
        if t < 20 or len(spy) < 21:
            rv_series.append(0.0)
            continue
        rv_series.append(_realized_vol_annualized(spy[:t + 1], window=20))

    # vix_term_slope (VIX - VIX3M)
    slope_series: List[float] = []
    if len(vix) == T and len(vix3m) == T:
        slope_series = [vix[t] - vix3m[t] for t in range(T)]
    else:
        slope_series = [0.0] * T

    # credit_hyg_lqd (ratio; we INVERT sign at z-score step)
    ratio_series: List[float] = []
    if len(hyg) == T and len(lqd) == T:
        ratio_series = [hyg[t] / lqd[t] if lqd[t] > 0 else 1.0 for t in range(T)]
    else:
        ratio_series = [1.0] * T

    # dxy_drift (20d cumulative return)
    dxy_drift: List[float] = []
    for t in range(T):
        if t < 20 or len(dxy) < t + 1 or dxy[t - 20] <= 0:
            dxy_drift.append(0.0)
        else:
            dxy_drift.append((dxy[t] / dxy[t - 20]) - 1.0)

    # commodity_stress
    commod: List[float] = []
    for t in range(T):
        if t < 20 or len(uso) != T or len(gld) != T or uso[t - 20] <= 0 or gld[t - 20] <= 0:
            commod.append(0.0)
        else:
            uso_20 = (uso[t] / uso[t - 20]) - 1.0
            gld_20 = (gld[t] / gld[t - 20]) - 1.0
            commod.append(abs(uso_20) + gld_20)

    # btc_decoupling (20d rolling corr distance from long-run mean)
    btc_series: List[float] = [0.0] * T
    if len(btc) == T and len(spy) == T:
        btc_rets = _pct_returns(btc)
        spy_rets = _pct_returns(spy)
        # Align — pct_returns drops one observation.
        if len(btc_rets) == len(spy_rets) == T - 1:
            corr_series: List[float] = [0.0] * T
            for t in range(21, T):
                c = _pairwise_corr(btc_rets[t - 21:t - 1], spy_rets[t - 21:t - 1])
                corr_series[t] = c
            # Long-run mean of corrs.
            valid = [c for c in corr_series if c != 0.0]
            long_mean = statistics.fmean(valid) if valid else 0.0
            btc_series = [abs(c - long_mean) for c in corr_series]

    # dealer_gamma — not historical; fill with 0 (neutral prior).
    dealer_series = [0.0] * T

    # breadth_proxy (sector dispersion, 20d returns)
    sect_series: List[float] = [0.0] * T
    if len(sectors) >= 3:
        # Per-sector 20d return series.
        per_sec: List[List[float]] = []
        for s in sectors:
            if len(s) != T:
                continue
            rets: List[float] = []
            for t in range(T):
                if t < 20 or s[t - 20] <= 0:
                    rets.append(0.0)
                else:
                    rets.append((s[t] / s[t - 20]) - 1.0)
            per_sec.append(rets)
        for t in range(T):
            vals = [ps[t] for ps in per_sec]
            if len(vals) >= 3:
                try:
                    sect_series[t] = statistics.pstdev(vals)
                except statistics.StatisticsError:
                    sect_series[t] = 0.0

    # ------------------------------------------------------------------
    # Build z-series for each factor (rolling 252d).
    # ------------------------------------------------------------------
    def _as_z_series(raw_series: List[float], *, invert: bool = False) -> List[float]:
        out = [0.0] * T
        for t in range(T):
            if t < MIN_Z_OBSERVATIONS:
                continue
            z = _rolling_z(raw_series[: t + 1])
            out[t] = -z if invert else z
        return out

    rv_z       = _as_z_series(rv_series)
    slope_z    = _as_z_series(slope_series)
    credit_z   = _as_z_series(ratio_series, invert=True)
    dxy_z      = _as_z_series(dxy_drift)
    commod_z   = _as_z_series(commod)
    btc_z      = _as_z_series(btc_series)
    dealer_z   = dealer_series  # already 0s
    breadth_z  = _as_z_series(sect_series)

    # ------------------------------------------------------------------
    # Assemble the T × 8 matrix in FACTOR_KEYS order.
    # ------------------------------------------------------------------
    matrix: List[List[float]] = []
    for t in range(T):
        matrix.append([
            rv_z[t], slope_z[t], credit_z[t], dxy_z[t],
            commod_z[t], btc_z[t], dealer_z[t], breadth_z[t],
        ])
    # Drop the first MIN_Z_OBSERVATIONS rows (all zeros — uninformative).
    matrix = matrix[MIN_Z_OBSERVATIONS:]
    return matrix, coverage


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def run_calibration(
    *,
    eodhd_client: Any = None,
    store: Any = None,
    lookback_days: int = 1260,
    persist: bool = True,
) -> CalibrationReport:
    """Full calibration pass: fetch → fit → persist."""
    report = CalibrationReport(
        started_at=dt.datetime.now(dt.timezone.utc).isoformat() + "Z",
    )
    if eodhd_client is None:
        try:
            from backend.eodhd_client import EodhdClient
            eodhd_client = EodhdClient.from_env()
        except Exception as e:
            report.error = f"no eodhd client: {e}"
            report.finished_at = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
            return report

    if store is None:
        try:
            from backend.redis_store import get_store_optional
            store = get_store_optional()
        except Exception:
            store = None

    try:
        matrix, coverage = build_historical_factor_matrix(
            eodhd_client, lookback_days=lookback_days,
        )
        report.factor_coverage = coverage
        if len(matrix) < 200:
            report.error = f"insufficient training data ({len(matrix)} rows)"
            report.note = "will fall back to sticky default at inference time"
            report.finished_at = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
            return report

        model = fit_model(matrix)
        report.model_version  = model.model_version
        report.training_days  = model.training_days
        report.log_likelihood = float(model.log_likelihood)

        if persist:
            report.persist_disk  = save_model(model, _model_path())
            report.persist_redis = model_to_redis(store, _redis_key(), model)

        # Invalidate the in-process memo so subsequent calls pick up the new model.
        try:
            from backend.market_intel.regime_service import clear_cache
            clear_cache()
        except Exception:
            pass

        report.ok = True
    except Exception as e:
        LOG.exception("mi_calibrate failed")
        report.error = f"{type(e).__name__}: {e}"
    finally:
        report.finished_at = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"

    return report
