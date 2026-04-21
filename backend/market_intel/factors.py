"""Canonical factor construction for Market Intelligence v2.

Each factor returns a :class:`FactorReading` with:

- ``value``:   raw factor value (units vary — pct / ratio / signed score)
- ``z``:       trailing 252-day z-score (comparable across factors)
- ``quality``: ``OK`` | ``STALE`` | ``MISSING``
- ``as_of``:   ISO date of the most recent observation
- ``source``:  provenance string (for debugging + data-quality chips)

Factor keys in :data:`FACTOR_KEYS` are the stable ordering used by the HMM
and every downstream consumer. **Do not reorder** — calibrated models
encode feature indexes positionally.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

LOG = logging.getLogger("market_intel.factors")


# ---------------------------------------------------------------------------
# Stable factor ordering — DO NOT REORDER
# ---------------------------------------------------------------------------

#: The canonical factor order consumed by the HMM and every downstream card.
#: Values on the factor vector are z-scored (mean 0, std 1 over trailing 252d).
FACTOR_KEYS: Tuple[str, ...] = (
    "rv_spx_20d",
    "vix_term_slope",
    "credit_hyg_lqd",
    "dxy_drift",
    "commodity_stress",
    "btc_decoupling",
    "dealer_gamma",
    "breadth_proxy",
)

#: Per-factor human-friendly labels.
FACTOR_LABELS: Dict[str, str] = {
    "rv_spx_20d":       "SPX 20d Realized Vol",
    "vix_term_slope":   "VIX Term Slope (VX1-VX2)",
    "credit_hyg_lqd":   "Credit HYG/LQD z",
    "dxy_drift":        "DXY 20d z",
    "commodity_stress": "Commodity Stress (WTI+GLD)",
    "btc_decoupling":   "BTC Decoupling vs SPY",
    "dealer_gamma":     "Dealer Gamma z",
    "breadth_proxy":    "Breadth Proxy (sector ETFs)",
}

# Quality flag constants.
OK = "OK"
STALE = "STALE"
MISSING = "MISSING"

#: Rolling window for z-score normalization (1y of biz days).
DEFAULT_Z_WINDOW = 252

#: Minimum observations needed to produce a z-score.
MIN_Z_OBSERVATIONS = 60


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class FactorReading:
    key:     str = ""
    label:   str = ""
    value:   float = 0.0        # raw value (units vary)
    z:       float = 0.0        # z-score vs trailing window
    quality: str = MISSING      # OK | STALE | MISSING
    as_of:   str = ""           # YYYY-MM-DD of last observation
    source:  str = ""           # provenance
    note:    str = ""           # optional human-readable context

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FactorSnapshot:
    """Full factor vector for a single day. Feeds the HMM + Cross-Asset v2."""

    as_of:       str = ""                # YYYY-MM-DD
    readings:    Dict[str, FactorReading] = field(default_factory=dict)
    missing:     List[str] = field(default_factory=list)
    stale:       List[str] = field(default_factory=list)
    ok:          List[str] = field(default_factory=list)

    @property
    def vector(self) -> List[float]:
        """Return the z-score vector in canonical FACTOR_KEYS order.

        Missing/stale factors contribute 0 (their trailing-z mean), which
        is the least-informative prior — keeps the HMM inference stable
        when a data source is down.
        """
        return [float(self.readings.get(k, FactorReading()).z) for k in FACTOR_KEYS]

    def to_dict(self) -> dict:
        return {
            "as_of":    self.as_of,
            "readings": {k: v.to_dict() for k, v in self.readings.items()},
            "missing":  list(self.missing),
            "stale":    list(self.stale),
            "ok":       list(self.ok),
        }


# ---------------------------------------------------------------------------
# Math helpers (stdlib only)
# ---------------------------------------------------------------------------


def _rolling_z(values: List[float], window: int = DEFAULT_Z_WINDOW) -> float:
    """Last-value z-score against a trailing window. 0.0 if insufficient data."""
    if not values or len(values) < MIN_Z_OBSERVATIONS:
        return 0.0
    tail = values[-window:]
    # Exclude the last point from the baseline to avoid self-inclusion bias,
    # then compare it to the tail distribution.
    baseline = tail[:-1] if len(tail) > 1 else tail
    if len(baseline) < 2:
        return 0.0
    try:
        mu = statistics.fmean(baseline)
        sigma = statistics.pstdev(baseline)
    except statistics.StatisticsError:
        return 0.0
    if sigma <= 1e-9:
        return 0.0
    z = (values[-1] - mu) / sigma
    if not math.isfinite(z):
        return 0.0
    # Cap to [-4, 4] so an outlier day doesn't dominate HMM emissions.
    return max(-4.0, min(4.0, z))


def _pct_returns(closes: List[float]) -> List[float]:
    """Simple percent returns from a close series."""
    out: List[float] = []
    for i in range(1, len(closes)):
        p, c = closes[i - 1], closes[i]
        if p and math.isfinite(p) and math.isfinite(c) and p > 0:
            out.append((c - p) / p)
    return out


def _realized_vol_annualized(closes: List[float], window: int = 20) -> float:
    """20d rolling realized vol, annualized (×√252)."""
    if len(closes) < window + 1:
        return 0.0
    rets = _pct_returns(closes[-(window + 1):])
    if len(rets) < 2:
        return 0.0
    try:
        sd = statistics.pstdev(rets)
    except statistics.StatisticsError:
        return 0.0
    return float(sd * math.sqrt(252) * 100.0)  # as %


def _pairwise_corr(a: List[float], b: List[float]) -> float:
    """Pearson correlation, returning 0.0 if undefined."""
    n = min(len(a), len(b))
    if n < 3:
        return 0.0
    a = a[-n:]
    b = b[-n:]
    mean_a = statistics.fmean(a)
    mean_b = statistics.fmean(b)
    num = sum((a[i] - mean_a) * (b[i] - mean_b) for i in range(n))
    den_a = math.sqrt(sum((x - mean_a) ** 2 for x in a))
    den_b = math.sqrt(sum((x - mean_b) ** 2 for x in b))
    if den_a <= 1e-9 or den_b <= 1e-9:
        return 0.0
    return num / (den_a * den_b)


def _parse_bar_closes(rows: List[dict]) -> Tuple[List[float], List[str]]:
    """Extract ordered (close, date) from EODHD EOD rows."""
    if not rows:
        return [], []
    ordered = sorted(rows, key=lambda r: str(r.get("date", "")))
    closes: List[float] = []
    dates:  List[str]   = []
    for r in ordered:
        c = r.get("adjusted_close")
        if c is None:
            c = r.get("close")
        try:
            f = float(c)
            if math.isfinite(f) and f > 0:
                closes.append(f)
                dates.append(str(r.get("date", "")))
        except (TypeError, ValueError):
            continue
    return closes, dates


def _quality_for(closes: List[float], dates: List[str], *, stale_days: int) -> Tuple[str, str]:
    """Classify a series: (quality, as_of)."""
    if not closes or not dates:
        return MISSING, ""
    as_of = dates[-1]
    try:
        age_days = (dt.date.today() - dt.date.fromisoformat(as_of[:10])).days
    except Exception:
        return OK, as_of
    # Use calendar-day age with a generous cap (markets have weekends).
    if age_days > max(1, stale_days) + 3:
        return STALE, as_of
    return OK, as_of


# ---------------------------------------------------------------------------
# Factor builders
# ---------------------------------------------------------------------------
# Each builder signature:
#    fn(eodhd_client, orats_client, *, stale_days, ...) -> FactorReading
#
# Clients may be None — factor falls to MISSING gracefully.
# ---------------------------------------------------------------------------


def _rv_spx_20d(eodhd, *, stale_days: int) -> FactorReading:
    reading = FactorReading(
        key="rv_spx_20d", label=FACTOR_LABELS["rv_spx_20d"], source="eodhd:SPY.US",
    )
    if eodhd is None:
        return reading
    try:
        resp = eodhd.get_eod("SPY.US", period="d")
        closes, dates = _parse_bar_closes(resp.rows)
    except Exception as e:
        reading.note = f"fetch_err: {type(e).__name__}"
        return reading
    if len(closes) < 40:
        reading.note = f"insufficient: {len(closes)}"
        return reading
    # Build rolling RV series over trailing window for z-scoring.
    rv_series: List[float] = []
    for i in range(20, len(closes)):
        rv_series.append(_realized_vol_annualized(closes[: i + 1], window=20))
    reading.value = float(rv_series[-1]) if rv_series else 0.0
    reading.z     = _rolling_z(rv_series)
    reading.quality, reading.as_of = _quality_for(closes, dates, stale_days=stale_days)
    return reading


def _vix_term_slope(eodhd, *, stale_days: int) -> FactorReading:
    """VIX vs VIX3M (3-month) proxy — positive = backwardation (stress)."""
    reading = FactorReading(
        key="vix_term_slope",
        label=FACTOR_LABELS["vix_term_slope"],
        source="eodhd:VIX.INDX-VIX3M.INDX",
    )
    if eodhd is None:
        return reading
    try:
        v_resp  = eodhd.get_eod("VIX.INDX",  period="d")
        v3_resp = eodhd.get_eod("VIX3M.INDX", period="d")
    except Exception as e:
        reading.note = f"fetch_err: {type(e).__name__}"
        return reading
    v_closes,  v_dates  = _parse_bar_closes(v_resp.rows  if v_resp  else [])
    v3_closes, v3_dates = _parse_bar_closes(v3_resp.rows if v3_resp else [])
    n = min(len(v_closes), len(v3_closes))
    if n < MIN_Z_OBSERVATIONS:
        reading.note = f"insufficient: n={n}"
        return reading
    slope_series = [
        v_closes[-n:][i] - v3_closes[-n:][i] for i in range(n)
    ]
    reading.value = float(slope_series[-1]) if slope_series else 0.0
    reading.z     = _rolling_z(slope_series)
    reading.quality, reading.as_of = _quality_for(v_closes, v_dates, stale_days=stale_days)
    return reading


def _credit_hyg_lqd(eodhd, *, stale_days: int) -> FactorReading:
    """HYG/LQD ratio z-score — lower ratio = credit stress."""
    reading = FactorReading(
        key="credit_hyg_lqd",
        label=FACTOR_LABELS["credit_hyg_lqd"],
        source="eodhd:HYG.US-LQD.US",
    )
    if eodhd is None:
        return reading
    try:
        hyg = eodhd.get_eod("HYG.US", period="d")
        lqd = eodhd.get_eod("LQD.US", period="d")
    except Exception as e:
        reading.note = f"fetch_err: {type(e).__name__}"
        return reading
    h_closes, h_dates = _parse_bar_closes(hyg.rows if hyg else [])
    l_closes, _       = _parse_bar_closes(lqd.rows if lqd else [])
    n = min(len(h_closes), len(l_closes))
    if n < MIN_Z_OBSERVATIONS:
        reading.note = f"insufficient: n={n}"
        return reading
    ratio = [
        h_closes[-n:][i] / l_closes[-n:][i]
        for i in range(n)
        if l_closes[-n:][i] > 0
    ]
    # Z-score of the ratio; we INVERT sign so higher z = more stress.
    z = _rolling_z(ratio)
    reading.value = float(ratio[-1]) if ratio else 0.0
    reading.z     = -z  # higher = more stress
    reading.quality, reading.as_of = _quality_for(h_closes, h_dates, stale_days=stale_days)
    return reading


def _dxy_drift(eodhd, *, stale_days: int) -> FactorReading:
    """DXY 20d cumulative return, z-scored. Positive = dollar strength / stress."""
    reading = FactorReading(
        key="dxy_drift", label=FACTOR_LABELS["dxy_drift"], source="eodhd:UUP.US",
    )
    if eodhd is None:
        return reading
    try:
        resp = eodhd.get_eod("UUP.US", period="d")
    except Exception as e:
        reading.note = f"fetch_err: {type(e).__name__}"
        return reading
    closes, dates = _parse_bar_closes(resp.rows if resp else [])
    if len(closes) < 40:
        reading.note = f"insufficient: {len(closes)}"
        return reading
    drifts: List[float] = []
    for i in range(20, len(closes)):
        if closes[i - 20] > 0:
            drifts.append((closes[i] / closes[i - 20]) - 1.0)
    reading.value = float(drifts[-1]) if drifts else 0.0
    reading.z     = _rolling_z(drifts)
    reading.quality, reading.as_of = _quality_for(closes, dates, stale_days=stale_days)
    return reading


def _commodity_stress(eodhd, *, stale_days: int) -> FactorReading:
    """Composite of WTI (USO) + gold (GLD) 20d returns, combined z-score."""
    reading = FactorReading(
        key="commodity_stress",
        label=FACTOR_LABELS["commodity_stress"],
        source="eodhd:USO.US+GLD.US",
    )
    if eodhd is None:
        return reading
    try:
        uso = eodhd.get_eod("USO.US", period="d")
        gld = eodhd.get_eod("GLD.US", period="d")
    except Exception as e:
        reading.note = f"fetch_err: {type(e).__name__}"
        return reading
    u_closes, u_dates = _parse_bar_closes(uso.rows if uso else [])
    g_closes, _       = _parse_bar_closes(gld.rows if gld else [])
    if len(u_closes) < 40 or len(g_closes) < 40:
        reading.note = "insufficient"
        return reading
    n = min(len(u_closes), len(g_closes))
    u_closes = u_closes[-n:]
    g_closes = g_closes[-n:]
    uso_20: List[float] = []
    gld_20: List[float] = []
    for i in range(20, n):
        if u_closes[i - 20] > 0:
            uso_20.append((u_closes[i] / u_closes[i - 20]) - 1.0)
        if g_closes[i - 20] > 0:
            gld_20.append((g_closes[i] / g_closes[i - 20]) - 1.0)
    # Composite = abs magnitude (both up OR both down from normal = stress).
    # We use the unsigned z of (|uso_20| + gld_20), since gold-up AND oil-up
    # are both risk signals in different regimes.
    composite = [abs(uso_20[i]) + g for i, g in enumerate(gld_20[-len(uso_20):])]
    reading.value = float(composite[-1]) if composite else 0.0
    reading.z     = _rolling_z(composite)
    reading.quality, reading.as_of = _quality_for(u_closes, u_dates, stale_days=stale_days)
    return reading


def _btc_decoupling(eodhd, *, stale_days: int) -> FactorReading:
    """|20d corr(BTC, SPY) - trailing 252d mean of that corr|."""
    reading = FactorReading(
        key="btc_decoupling",
        label=FACTOR_LABELS["btc_decoupling"],
        source="eodhd:BTC-USD.CC+SPY.US",
    )
    if eodhd is None:
        return reading
    try:
        btc = eodhd.get_eod("BTC-USD.CC", period="d")
        spy = eodhd.get_eod("SPY.US",     period="d")
    except Exception as e:
        reading.note = f"fetch_err: {type(e).__name__}"
        return reading
    b_closes, b_dates = _parse_bar_closes(btc.rows if btc else [])
    s_closes, _       = _parse_bar_closes(spy.rows if spy else [])
    n = min(len(b_closes), len(s_closes))
    if n < 80:
        reading.note = f"insufficient: {n}"
        return reading
    b_closes = b_closes[-n:]
    s_closes = s_closes[-n:]
    b_rets = _pct_returns(b_closes)
    s_rets = _pct_returns(s_closes)
    # Align.
    m = min(len(b_rets), len(s_rets))
    if m < 40:
        reading.note = f"insufficient: {m}"
        return reading
    b_rets = b_rets[-m:]
    s_rets = s_rets[-m:]
    # Rolling 20d corr series.
    corrs: List[float] = []
    for i in range(20, m):
        corrs.append(_pairwise_corr(b_rets[i - 20:i], s_rets[i - 20:i]))
    if len(corrs) < MIN_Z_OBSERVATIONS:
        reading.note = f"insufficient: corr={len(corrs)}"
        return reading
    # Decoupling magnitude: distance from long-run mean (unsigned).
    long_mean = statistics.fmean(corrs)
    decouple  = [abs(c - long_mean) for c in corrs]
    reading.value = float(decouple[-1]) if decouple else 0.0
    reading.z     = _rolling_z(decouple)
    reading.quality, reading.as_of = _quality_for(b_closes, b_dates, stale_days=stale_days)
    return reading


def _dealer_gamma(gamma_context: Optional[dict]) -> FactorReading:
    """Reads the existing dealer_gamma_context output. Optional input."""
    reading = FactorReading(
        key="dealer_gamma",
        label=FACTOR_LABELS["dealer_gamma"],
        source="backend.dealer_gamma_context",
    )
    if not gamma_context or not isinstance(gamma_context, dict):
        reading.note = "no_context"
        return reading
    try:
        # Invert: negative gamma (dealers short gamma) = stress → positive z.
        gamma_sign   = str(gamma_context.get("sign") or gamma_context.get("regime") or "").lower()
        gamma_score  = float(gamma_context.get("magnitude_z") or gamma_context.get("score") or 0.0)
        if gamma_sign in ("negative", "short_gamma", "amplifying"):
            reading.z = abs(gamma_score)
        elif gamma_sign in ("positive", "long_gamma", "damping"):
            reading.z = -abs(gamma_score)
        else:
            reading.z = float(gamma_score) if math.isfinite(gamma_score) else 0.0
        reading.value = gamma_score
        reading.quality = OK
        reading.as_of = str(gamma_context.get("as_of") or "")
        reading.note = f"sign={gamma_sign}"
    except Exception as e:
        reading.note = f"parse_err: {type(e).__name__}"
    return reading


def _breadth_proxy(eodhd, *, stale_days: int) -> FactorReading:
    """Sector-ETF equal-weighted 20d return dispersion.

    Inverts the magnitude so "all sectors moving together (positive corr)" is
    neutral, "sectors fanning out (high dispersion, e.g. defensives up while
    cyclicals down)" reads as stress.
    """
    reading = FactorReading(
        key="breadth_proxy",
        label=FACTOR_LABELS["breadth_proxy"],
        source="eodhd:XLK+XLF+XLE+XLV+XLU+XLY",
    )
    if eodhd is None:
        return reading
    sectors = ["XLK.US", "XLF.US", "XLE.US", "XLV.US", "XLU.US", "XLY.US"]
    per_sector_ret20: Dict[str, List[float]] = {}
    last_dates: List[str] = []
    for sym in sectors:
        try:
            r = eodhd.get_eod(sym, period="d")
            c, d = _parse_bar_closes(r.rows if r else [])
            if len(c) >= 40:
                series: List[float] = []
                for i in range(20, len(c)):
                    if c[i - 20] > 0:
                        series.append((c[i] / c[i - 20]) - 1.0)
                per_sector_ret20[sym] = series
                if d:
                    last_dates.append(d[-1])
        except Exception:
            continue
    if len(per_sector_ret20) < 3:
        reading.note = f"insufficient_sectors: {len(per_sector_ret20)}"
        return reading
    # Align lengths.
    n = min(len(v) for v in per_sector_ret20.values())
    if n < MIN_Z_OBSERVATIONS:
        reading.note = f"insufficient_hist: {n}"
        return reading
    aligned = {k: v[-n:] for k, v in per_sector_ret20.items()}
    # Per-day dispersion = stdev across sectors that day.
    dispersion: List[float] = []
    for i in range(n):
        vals = [v[i] for v in aligned.values()]
        try:
            dispersion.append(statistics.pstdev(vals))
        except statistics.StatisticsError:
            dispersion.append(0.0)
    reading.value = float(dispersion[-1]) if dispersion else 0.0
    reading.z     = _rolling_z(dispersion)
    if last_dates:
        reading.as_of = sorted(last_dates)[-1]
        try:
            age = (dt.date.today() - dt.date.fromisoformat(reading.as_of[:10])).days
            reading.quality = STALE if age > stale_days + 3 else OK
        except Exception:
            reading.quality = OK
    return reading


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def build_factor_snapshot(
    *,
    eodhd_client: Any = None,
    gamma_context: Optional[dict] = None,
    stale_days: int = 1,
    today: Optional[dt.date] = None,
) -> FactorSnapshot:
    """Build today's factor snapshot.

    Uses whatever clients are provided; missing clients → MISSING factors.
    """
    today = today or dt.date.today()
    readings: Dict[str, FactorReading] = {}

    builders: List[Tuple[str, Callable[[], FactorReading]]] = [
        ("rv_spx_20d",       lambda: _rv_spx_20d(eodhd_client, stale_days=stale_days)),
        ("vix_term_slope",   lambda: _vix_term_slope(eodhd_client, stale_days=stale_days)),
        ("credit_hyg_lqd",   lambda: _credit_hyg_lqd(eodhd_client, stale_days=stale_days)),
        ("dxy_drift",        lambda: _dxy_drift(eodhd_client, stale_days=stale_days)),
        ("commodity_stress", lambda: _commodity_stress(eodhd_client, stale_days=stale_days)),
        ("btc_decoupling",   lambda: _btc_decoupling(eodhd_client, stale_days=stale_days)),
        ("dealer_gamma",     lambda: _dealer_gamma(gamma_context)),
        ("breadth_proxy",    lambda: _breadth_proxy(eodhd_client, stale_days=stale_days)),
    ]

    for key, fn in builders:
        try:
            r = fn()
        except Exception as e:
            LOG.warning("market_intel: factor %s failed: %s", key, e)
            r = FactorReading(key=key, label=FACTOR_LABELS.get(key, key),
                              quality=MISSING, note=f"exc: {type(e).__name__}")
        readings[key] = r

    missing = [k for k, r in readings.items() if r.quality == MISSING]
    stale   = [k for k, r in readings.items() if r.quality == STALE]
    ok      = [k for k, r in readings.items() if r.quality == OK]

    return FactorSnapshot(
        as_of=today.isoformat(),
        readings=readings,
        missing=missing,
        stale=stale,
        ok=ok,
    )


def build_factor_matrix(
    snapshots: List[FactorSnapshot],
) -> List[List[float]]:
    """Stack a list of snapshots into a T × F matrix for HMM fitting."""
    return [s.vector for s in snapshots]
