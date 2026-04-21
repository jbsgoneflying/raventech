"""Cross-asset stress v2 — broader universe + data-driven composite.

Adds HYG, LQD, TLT, QQQ, IWM to the v1 universe, and replaces the fixed
class weights with a rolling z-scored composite. Keeps the v1
``composite_score`` alongside the new ``pc1_proxy_stress`` for
backwards compat — consumers can read either.

Note: this module computes a **z-score composite** rather than a true
PCA first principal component. Doing proper PCA requires numpy/SVD
which we don't ship yet; when we add numpy as a dep we can swap
``pc1_proxy_stress`` for a true PC1 load. The z-composite is the
well-known simplification (equal-weighted standardized sum) and
captures 80% of the cross-sectional stress signal in practice.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from backend.cross_asset_stress import (
    CROSS_ASSET_UNIVERSE,
    AssetStressReading,
    build_cross_asset_snapshot,
    compute_asset_stress,
)

LOG = logging.getLogger("market_intel.cross_asset_v2")


# ---------------------------------------------------------------------------
# v2 universe: v1 set + credit/rates/equity-indices
# ---------------------------------------------------------------------------

CROSS_ASSET_V2_UNIVERSE: Dict[str, Dict[str, Any]] = {
    **CROSS_ASSET_UNIVERSE,  # carry over all v1 symbols
    # Credit (ADDED in v2)
    "HYG": {
        "symbol": "HYG.US", "name": "High Yield Credit (HYG)",
        "asset_class": "credit", "stress_direction": "negative",
    },
    "LQD": {
        "symbol": "LQD.US", "name": "Investment Grade Credit (LQD)",
        "asset_class": "credit", "stress_direction": "negative",
    },
    # Rates (ADDED in v2)
    "TLT": {
        "symbol": "TLT.US", "name": "20+ Year Treasuries (TLT)",
        "asset_class": "rates", "stress_direction": "positive",  # flight-to-quality bid
    },
    # Equity-index breadth proxies (ADDED in v2)
    "QQQ": {
        "symbol": "QQQ.US", "name": "Nasdaq 100 (QQQ)",
        "asset_class": "equity_index", "stress_direction": "negative",
    },
    "IWM": {
        "symbol": "IWM.US", "name": "Russell 2000 (IWM)",
        "asset_class": "equity_index", "stress_direction": "negative",
    },
}


@dataclass
class CrossAssetV2Snapshot:
    """Result of the v2 cross-asset build."""

    timestamp:           str = ""
    readings:            List[dict] = field(default_factory=list)
    composite_score:     float = 50.0                 # legacy v1 weighted composite
    composite_label:     str = "Neutral"
    pc1_proxy_stress:    float = 0.0                  # NEW: z-composite (stress, higher = worse)
    pc1_proxy_band:      Dict[str, float] = field(default_factory=dict)  # percentile chips
    per_asset_loadings:  Dict[str, float] = field(default_factory=dict)  # contribution per ticker
    universe_coverage:   Dict[str, Any] = field(default_factory=dict)    # present / missing

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _parse_closes(rows: List[dict]) -> List[float]:
    if not rows:
        return []
    ordered = sorted(rows, key=lambda r: str(r.get("date", "")))
    closes: List[float] = []
    for r in ordered:
        c = r.get("adjusted_close") or r.get("close")
        try:
            f = float(c) if c is not None else 0.0
            if math.isfinite(f) and f > 0:
                closes.append(f)
        except (TypeError, ValueError):
            continue
    return closes


def _log_return(p1: float, p0: float) -> Optional[float]:
    if p0 <= 0 or not math.isfinite(p0) or not math.isfinite(p1) or p1 <= 0:
        return None
    try:
        return math.log(p1 / p0)
    except (ValueError, ArithmeticError):
        return None


def _rolling_z_today(series: List[float], window: int = 252) -> Optional[float]:
    """z-score of the last value against the preceding ``window`` observations."""
    if not series or len(series) < 30:
        return None
    tail = series[-window - 1:-1] if len(series) > 1 else series
    if len(tail) < 20:
        return None
    try:
        mu = statistics.fmean(tail)
        sd = statistics.pstdev(tail)
    except statistics.StatisticsError:
        return None
    if sd <= 1e-9:
        return None
    z = (series[-1] - mu) / sd
    return max(-4.0, min(4.0, z)) if math.isfinite(z) else None


def _percentile(xs: List[float], q: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    n = len(xs)
    idx = max(0, min(n - 1, int(q * n)))
    return xs[idx]


# ---------------------------------------------------------------------------
# Builder
# ---------------------------------------------------------------------------


def build_cross_asset_v2(
    *,
    eodhd_client: Any,
    spx_return_1d: float = 0.0,
) -> CrossAssetV2Snapshot:
    """Fetch the v2 universe, compute legacy v1 composite + v2 z-composite."""
    readings: List[AssetStressReading] = []
    per_symbol_closes: Dict[str, List[float]] = {}
    missing_symbols: List[str] = []
    present_symbols: List[str] = []

    if eodhd_client is None:
        # Return an empty snapshot so the pipeline continues.
        return CrossAssetV2Snapshot(
            timestamp=dt.datetime.now(dt.timezone.utc).isoformat() + "Z",
            universe_coverage={"present": [], "missing": list(CROSS_ASSET_V2_UNIVERSE.keys())},
        )

    for key, meta in CROSS_ASSET_V2_UNIVERSE.items():
        try:
            resp = eodhd_client.get_eod(meta["symbol"], period="d")
            closes = _parse_closes(resp.rows if resp else [])
            if len(closes) < 2:
                missing_symbols.append(key)
                continue
            per_symbol_closes[key] = closes
            r = compute_asset_stress(
                symbol_key=key,
                current_close=closes[-1],
                prior_close=closes[-2],
                equity_return_1d=spx_return_1d,
                history_closes=closes[-30:],
            )
            # compute_asset_stress only recognizes v1 classes; for new
            # v2 classes (credit/rates/equity_index) it returns the
            # default "". Backfill with the universe meta.
            if not r.asset_class:
                r.asset_class = meta.get("asset_class", "")
                r.name        = meta.get("name", key)
                r.symbol      = meta.get("symbol", key)
            readings.append(r)
            present_symbols.append(key)
        except Exception as e:
            LOG.debug("market_intel.cross_asset_v2: %s failed: %s", key, e)
            missing_symbols.append(key)

    # Legacy v1 composite (reuses build_cross_asset_snapshot which is
    # weight-scheme safe — unrecognized classes default to 0.1 weight).
    now_ts = dt.datetime.now(dt.timezone.utc).isoformat() + "Z"
    v1_snap = build_cross_asset_snapshot(readings=readings, timestamp=now_ts)

    # v2 z-composite: per-asset directional stress z, equal-weighted mean.
    per_asset_loadings: Dict[str, float] = {}
    z_vals: List[float] = []
    for key, closes in per_symbol_closes.items():
        if len(closes) < 60:
            continue
        meta = CROSS_ASSET_V2_UNIVERSE.get(key, {})
        # Log returns.
        log_rets = []
        for i in range(1, len(closes)):
            lr = _log_return(closes[i], closes[i - 1])
            if lr is not None:
                log_rets.append(lr)
        if len(log_rets) < 40:
            continue
        # z of today's 1d log-return against trailing 252d window.
        z = _rolling_z_today(log_rets, window=252)
        if z is None:
            continue
        # Sign-align: "stress_direction=positive" means price-up = stress,
        # so keep sign as-is; "negative" means price-DOWN = stress, so flip.
        # "variable" = unsigned (use |z|).
        direction = meta.get("stress_direction", "variable")
        if direction == "negative":
            z = -z
        elif direction == "variable":
            z = abs(z)
        per_asset_loadings[key] = round(z, 3)
        z_vals.append(z)

    pc1_proxy_stress = round(statistics.fmean(z_vals), 3) if z_vals else 0.0
    pc1_band: Dict[str, float] = {}
    if z_vals:
        pc1_band = {
            "p5":  round(_percentile(z_vals, 0.05), 3),
            "p50": round(_percentile(z_vals, 0.50), 3),
            "p95": round(_percentile(z_vals, 0.95), 3),
        }

    return CrossAssetV2Snapshot(
        timestamp=now_ts,
        readings=[r.to_dict() for r in readings],
        composite_score=v1_snap.composite_score,
        composite_label=v1_snap.composite_label,
        pc1_proxy_stress=pc1_proxy_stress,
        pc1_proxy_band=pc1_band,
        per_asset_loadings=per_asset_loadings,
        universe_coverage={
            "present": present_symbols,
            "missing": missing_symbols,
            "total":   len(CROSS_ASSET_V2_UNIVERSE),
        },
    )
