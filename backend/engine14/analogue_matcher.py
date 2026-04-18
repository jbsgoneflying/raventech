"""Engine 14 — historical analogue enumeration & regime bucketing.

Given a ticker + lookback, this module enumerates every prior weekly IC
window that *could* serve as a historical analogue for the user's trade.
Each window is annotated with:

  * entry_date, expiry_date, dte_sessions, dte_calendar_days
  * entry_close                   (SPX spot at entry)
  * entry_em_pct                  (1-sigma move % over calendar DTE)
  * regime_score, regime_bucket   (realized-vol-percentile proxy; Phase 1)
  * season_bucket                 (month + quarter + OPEX/summer flags)

Phase 1 intentionally ships a lean regime proxy (RV20 percentile over a
trailing 252-trading-day lookback). Full Engine 2-style regime scoring
can be layered in during Phase 2 without touching the simulator contract.

Why RV20 percentile?
--------------------
- It's computable from just SPX closes (no extra ORATS calls, robust to
  options-data gaps in the backfill window).
- It's highly correlated with the Engine 2 volatility sub-score, which
  dominates IC outcomes on a 5-7 DTE horizon.
- Easy to audit: "this window was in the 60th percentile of 1-month RV
  over the prior year" is a legible gate for the LLM advisor downstream.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.config import FeatureFlags, get_flags
from backend.engine14 import chain_cache
from backend.spx_ic.ohlc import DailyOHLC, iv_to_em1sigma_pct

LOG = logging.getLogger("engine14.analogue_matcher")


# ---- Bucket definitions ----

REGIME_BUCKETS = ("LOW", "MODERATE", "ELEVATED", "NO_TRADE")


def _regime_from_rv_pct(rv_pct: float) -> str:
    """Map RV20 percentile [0,1] to an Engine 2-compatible bucket label."""
    p = float(rv_pct)
    if p <= 0.25:
        return "LOW"
    if p <= 0.45:
        return "MODERATE"
    if p <= 0.65:
        return "ELEVATED"
    return "NO_TRADE"


def _is_opex_week(d: dt.date) -> bool:
    """Week containing the 3rd Friday (Engine 2 convention)."""
    first = dt.date(d.year, d.month, 1)
    ff = first
    while ff.weekday() != 4:
        ff += dt.timedelta(days=1)
    third_friday = ff + dt.timedelta(days=14)
    mon = third_friday - dt.timedelta(days=4)
    return mon <= d <= third_friday


def _season_bucket(d: dt.date) -> Dict[str, str]:
    q = "Q1" if d.month <= 3 else "Q2" if d.month <= 6 else "Q3" if d.month <= 9 else "Q4"
    return {
        "quarter": q,
        "month": f"{d.month:02d}",
        "isSummer": "YES" if d.month in (6, 7, 8) else "NO",
        "isOpex": "YES" if _is_opex_week(d) else "NO",
    }


# ---- Window enumeration ----

@dataclass(frozen=True)
class AnalogueWindow:
    entry_date: str
    expiry_date: str
    dte_sessions: int
    dte_calendar_days: int
    entry_close: float
    entry_em_pct: float           # 1-sigma move % over calendar DTE
    entry_iv_pct: Optional[float]  # annualized IV from cached ATM callMidIv, if any
    rv20: Optional[float]          # annualized realized vol (1.0 = 100%)
    rv20_pct: Optional[float]      # percentile in rolling 252d lookback [0,1]
    regime_bucket: str
    season: Dict[str, str] = field(default_factory=dict)
    # Phase A3 — coverage of the analogue's chain at likely 1σ / 1.5σ / 2σ short-strike
    # placements. Used by the EM-multiple filter to reject windows where
    # the caller's short strike (in sigma space) falls outside what the
    # listed chain actually quotes. None = unknown / not pre-computed.
    short_strike_em_coverage: Optional[Tuple[float, float]] = None  # (min_em_mult, max_em_mult)


def _log_returns(closes: List[float]) -> List[float]:
    out: List[float] = []
    for i in range(1, len(closes)):
        a, b = closes[i - 1], closes[i]
        if a and a > 0 and b and b > 0:
            out.append(math.log(b / a))
    return out


def _rv_annualized(logrets: List[float], window: int = 20) -> Optional[float]:
    if len(logrets) < window or window < 2:
        return None
    w = logrets[-window:]
    if len(w) < 2:
        return None
    return statistics.stdev(w) * math.sqrt(252.0)


def _percentile(x: float, xs: List[float]) -> Optional[float]:
    vals = [float(v) for v in xs if v is not None and math.isfinite(float(v))]
    if not vals:
        return None
    c = sum(1 for v in vals if v <= x)
    return c / len(vals)


def _atm_iv_from_cache(ticker: str, trade_date: str, expiry: str, spot: float) -> Optional[float]:
    """Pull ATM callMidIv from our chain cache for (trade_date, expiry).
    Returns annualized IV as a decimal (e.g., 0.18 for 18%). None if missing.
    """
    rows = chain_cache.fetch_chain_slice(ticker=ticker, trade_date=trade_date, expiry=expiry)
    if not rows:
        return None
    best = min(rows, key=lambda r: abs(float(r.strike) - float(spot)))
    iv = best.call_iv if best.call_iv is not None else best.put_iv
    if iv is None or iv <= 0:
        return None
    return float(iv)


def _build_matching_windows(
    closes_sorted: List[Tuple[str, float]],
    *,
    entry_dow: int = 0,
    target_dte_calendar: int = 4,
) -> List[Tuple[str, str, int, int]]:
    """Enumerate (entry_date, expiry_date, dte_sessions, dte_calendar) tuples
    that match the user's calendar shape.

    For each historical trading day whose weekday matches `entry_dow`, pair
    it with the trading day `target_dte_calendar` calendar days later
    (snapping forward to the next trading day if the raw target is a
    weekend/holiday). This naturally supports:

      * Weekly Mon→Fri ICs (entry_dow=0, target_dte_calendar=4)
      * Weekly Wed→Fri trades (entry_dow=2, target_dte_calendar=2)
      * Fri→Mon overnights (entry_dow=4, target_dte_calendar=3)
      * Any custom SPXW expiry the user types in.
    """
    if not closes_sorted:
        return []
    date_to_idx = {d: i for i, (d, _) in enumerate(closes_sorted)}
    sorted_dates = [d for d, _ in closes_sorted]

    def _snap_fwd(target: dt.date, max_steps: int = 5) -> Optional[str]:
        for _ in range(max_steps):
            s = target.isoformat()
            if s in date_to_idx:
                return s
            target += dt.timedelta(days=1)
        return None

    target_dte_calendar = max(1, int(target_dte_calendar))
    out: List[Tuple[str, str, int, int]] = []
    for entry_date in sorted_dates:
        try:
            ed = dt.date.fromisoformat(entry_date)
        except Exception:
            continue
        if ed.weekday() != int(entry_dow):
            continue
        x = _snap_fwd(ed + dt.timedelta(days=target_dte_calendar))
        if not x or x <= entry_date:
            continue
        ei = date_to_idx[entry_date]
        xi = date_to_idx[x]
        dte_sessions = xi - ei + 1
        dte_calendar = (dt.date.fromisoformat(x) - ed).days
        if dte_sessions > 0:
            out.append((entry_date, x, dte_sessions, dte_calendar))
    return out


# Backward-compat shim: default Mon→Fri weekly shape.
def _build_weekly_windows(
    closes_sorted: List[Tuple[str, float]], entry_dow: int = 0
) -> List[Tuple[str, str, int, int]]:
    """Enumerate Mon→Fri weekly windows (legacy entrypoint)."""
    return _build_matching_windows(
        closes_sorted, entry_dow=entry_dow, target_dte_calendar=4
    )


def build_analogue_universe(
    *,
    ticker: str,
    closes_sorted: List[Tuple[str, float]],
    entry_dow: int = 0,
    target_dte_calendar_days: int = 4,
    max_windows: int = 260,
) -> List[AnalogueWindow]:
    """Enumerate analogue windows and annotate with regime/season metadata.

    Requires the caller to pass a pre-fetched SPX close series (trading
    days only, ascending). `target_dte_calendar_days` is the user's
    (expiry - entry) calendar-day delta; windows are shaped to match so
    overnight/3-day/weekly trades all get apples-to-apples analogues.
    We deliberately avoid hitting ORATS here — IV enrichment comes from
    the cached option chain (if present).
    """
    ticker = str(ticker).upper()
    if not closes_sorted:
        return []

    windows = _build_matching_windows(
        closes_sorted,
        entry_dow=entry_dow,
        target_dte_calendar=target_dte_calendar_days,
    )
    if not windows:
        return []
    windows = windows[-max_windows:]

    # Precompute RV20 time series indexed by entry_date.
    closes = [c for _, c in closes_sorted]
    dates = [d for d, _ in closes_sorted]
    logrets = _log_returns(closes)
    # log-returns are aligned 1:1 with closes[1:], so logrets[i] corresponds to dates[i+1]
    # Rolling window RV20 at date index i+1 uses logrets[i-19:i+1].
    rv_by_date: Dict[str, float] = {}
    for i in range(19, len(logrets)):
        rv = _rv_annualized(logrets[i - 19 : i + 1], window=20)
        if rv is not None:
            rv_by_date[dates[i + 1]] = rv

    close_by_date = {d: c for d, c in closes_sorted}
    sorted_rv_dates = sorted(rv_by_date.keys())
    out: List[AnalogueWindow] = []
    for entry_date, expiry_date, dte_s, dte_c in windows:
        entry_close = close_by_date.get(entry_date)
        if entry_close is None:
            continue

        rv20 = rv_by_date.get(entry_date)
        rv20_pct: Optional[float] = None
        if rv20 is not None:
            prior = [rv_by_date[d] for d in sorted_rv_dates if d < entry_date]
            prior = prior[-252:] if len(prior) > 252 else prior
            if len(prior) >= 60:
                rv20_pct = _percentile(rv20, prior)

        iv_dec = _atm_iv_from_cache(ticker, entry_date, expiry_date, entry_close)
        iv_pct_ann: Optional[float] = (iv_dec * 100.0) if iv_dec is not None else None

        # Prefer option-market EM when available; otherwise fall back to RV-derived EM.
        if iv_pct_ann is not None:
            em_pct = iv_to_em1sigma_pct(iv_pct=iv_pct_ann, dte_calendar_days=dte_c)
        elif rv20 is not None:
            em_pct = iv_to_em1sigma_pct(iv_pct=float(rv20) * 100.0, dte_calendar_days=dte_c)
        else:
            continue  # can't anchor strikes without an EM; drop window

        regime = _regime_from_rv_pct(rv20_pct if rv20_pct is not None else 0.5)

        try:
            ed = dt.date.fromisoformat(entry_date)
        except Exception:
            continue
        season = _season_bucket(ed)

        # Pre-compute the analogue's EM-multiple coverage so the downstream
        # placement filter runs in O(1). Only do this for windows whose
        # cache was populated (fetch_chain_slice returns [] otherwise).
        coverage = compute_short_strike_em_coverage(
            ticker=ticker, trade_date=entry_date, expiry=expiry_date,
            entry_close=float(entry_close), entry_em_pct=float(em_pct),
        )

        out.append(
            AnalogueWindow(
                entry_date=entry_date,
                expiry_date=expiry_date,
                dte_sessions=int(dte_s),
                dte_calendar_days=int(dte_c),
                entry_close=float(entry_close),
                entry_em_pct=float(em_pct),
                entry_iv_pct=(None if iv_pct_ann is None else float(iv_pct_ann)),
                rv20=(None if rv20 is None else float(rv20)),
                rv20_pct=(None if rv20_pct is None else float(rv20_pct)),
                regime_bucket=regime,
                season=season,
                short_strike_em_coverage=coverage,
            )
        )

    return out


def date_to_idx(closes_sorted: List[Tuple[str, float]]) -> Dict[str, int]:
    """Public helper — exposed for tests and the simulator."""
    return {d: i for i, (d, _) in enumerate(closes_sorted)}


# ---- Matching ----

@dataclass(frozen=True)
class MatchCriteria:
    target_regime: str
    target_dte_sessions: int
    regime_bucket_tol: float   # in regime "score points" (0..100) — used via bucket-index distance
    season_mode: str           # "none" | "quarter" | "month" | "summer" | "opex"
    season_value: Optional[str] = None  # for quarter/month etc.
    # Phase A3 — placement filter: only admit analogues whose listed chain
    # covers strike placements comparable to the user's |z|. `None` disables.
    target_em_multiple: Optional[float] = None       # |z| of user's short strikes (avg)
    em_multiple_tol: float = 0.25                    # sigma tolerance (+/-)
    enable_em_multiple_filter: bool = False
    # Phase C2 — KNN multi-factor regime match. When enabled we drop the
    # regime-bucket gate and instead rank analogues by weighted L2 distance
    # in (VIX, VIX9D, VVIX, term_slope, RV20, net_gex, credit_score) space.
    # The RV20 bucket label is preserved on each `AnalogueWindow` for the
    # UI; `regime_bucket_tol` is ignored in KNN mode.
    enable_knn_regime: bool = False
    knn_top_n: int = 80


def _bucket_distance(a: str, b: str) -> int:
    try:
        return abs(REGIME_BUCKETS.index(a) - REGIME_BUCKETS.index(b))
    except ValueError:
        return 99


def compute_short_strike_em_coverage(
    *,
    ticker: str,
    trade_date: str,
    expiry: str,
    entry_close: float,
    entry_em_pct: float,
) -> Optional[Tuple[float, float]]:
    """Return (min_short_em_mult, max_short_em_mult) covered by the listed
    chain on the analogue's entry day.

    We pull the cached chain and compute each listed strike's |z| (distance
    from spot in sigma units using the window's EM). The MIN is the closest
    strike to spot, the MAX is the farthest. Any user-requested short-strike
    placement whose |z| falls inside [min, max] can be mapped without
    pathological snap failures.
    """
    if not entry_close or entry_em_pct <= 0:
        return None
    rows = chain_cache.fetch_chain_slice(ticker=ticker, trade_date=trade_date, expiry=expiry)
    if not rows:
        return None
    zs: List[float] = []
    for r in rows:
        try:
            k = float(r.strike)
        except (TypeError, ValueError):
            continue
        if k <= 0:
            continue
        z = abs((k / float(entry_close) - 1.0) * 100.0 / float(entry_em_pct))
        zs.append(z)
    if not zs:
        return None
    zs_sorted = sorted(zs)
    return (float(zs_sorted[0]), float(zs_sorted[-1]))


def user_em_multiple(
    *,
    user_spot: float,
    user_em_pct: float,
    short_put: float,
    short_call: float,
) -> Optional[float]:
    """Average |z| distance (sigma units) of the user's short strikes.

    z_put  = (short_put  / spot - 1) * 100 / em_pct    (negative)
    z_call = (short_call / spot - 1) * 100 / em_pct    (positive)
    Returns (|z_put| + |z_call|) / 2, which is the canonical EM multiple
    for a symmetric IC (and a good average for slightly skewed ones).
    """
    if user_spot <= 0 or user_em_pct <= 0:
        return None
    zp = abs((float(short_put) / float(user_spot) - 1.0) * 100.0 / float(user_em_pct))
    zc = abs((float(short_call) / float(user_spot) - 1.0) * 100.0 / float(user_em_pct))
    return float((zp + zc) / 2.0)


def filter_analogues(
    universe: List[AnalogueWindow],
    *,
    criteria: MatchCriteria,
    flags: Optional[FeatureFlags] = None,
    user_features: Optional[Any] = None,
    candidate_features: Optional[Dict[str, Any]] = None,
    return_match_quality: bool = False,
) -> Any:
    """Filter + rank analogues for the user's trade.

    Stage 1 — hard gates (DTE ±2, season, EM-multiple coverage).
    Stage 2 — regime selection:
        * Legacy: RV20 bucket distance (tol derived from
          `regime_bucket_tol`).
        * Phase C2 KNN (when `criteria.enable_knn_regime=True`):
          weighted L2 distance over `(VIX, VIX9D, VVIX, term_slope, RV20,
          net_gex, credit_score)`, truncated to `criteria.knn_top_n`.
          Candidates without a features row gracefully fall back to the
          bucket test so we don't silently drop half the pool.

    Parameters
    ----------
    user_features / candidate_features:
        Required when `enable_knn_regime=True`. `candidate_features` is a
        dict keyed by analogue `entry_date`.
    return_match_quality:
        When True the return value is a tuple
        `(list[AnalogueWindow], match_quality_dict)`. `match_quality_dict`
        is `None` outside KNN mode.
    """
    flags = flags or get_flags()
    tol_buckets = max(0, int(round(float(criteria.regime_bucket_tol) / 25.0)))  # 25 pts per bucket
    if tol_buckets == 0:
        tol_buckets = 1

    em_filter_on = bool(criteria.enable_em_multiple_filter and criteria.target_em_multiple is not None)
    target_z = float(criteria.target_em_multiple or 0.0)
    em_tol = float(criteria.em_multiple_tol or 0.25)

    knn_on = bool(
        criteria.enable_knn_regime
        and user_features is not None
        and candidate_features is not None
    )

    # Stage 1: hard gates (DTE / season / EM-coverage) + bucket gate when not in KNN mode.
    gated: List[AnalogueWindow] = []
    for w in universe:
        if not knn_on:
            if _bucket_distance(w.regime_bucket, criteria.target_regime) > tol_buckets:
                continue
        if abs(int(w.dte_sessions) - int(criteria.target_dte_sessions)) > 2:
            continue
        if criteria.season_mode == "quarter" and criteria.season_value:
            if w.season.get("quarter") != criteria.season_value:
                continue
        elif criteria.season_mode == "month" and criteria.season_value:
            if w.season.get("month") != criteria.season_value:
                continue
        elif criteria.season_mode == "summer":
            if w.season.get("isSummer") != "YES":
                continue
        elif criteria.season_mode == "opex":
            if w.season.get("isOpex") != "YES":
                continue
        if em_filter_on and w.short_strike_em_coverage is not None:
            zmin, zmax = w.short_strike_em_coverage
            if target_z + em_tol < zmin or target_z - em_tol > zmax:
                continue
        gated.append(w)

    match_quality: Optional[Dict[str, Any]] = None

    # Stage 2: KNN rank (if enabled).
    if knn_on:
        from backend.engine14.regime_knn import (
            knn_top_n,
            summarize_match_quality,
        )
        # Candidate RegimeFeatures by entry_date — only include windows with a features row.
        cand_map = {
            w.entry_date: candidate_features[w.entry_date]
            for w in gated if w.entry_date in (candidate_features or {})
        }
        # Windows *without* a features row fall through the KNN stage — we
        # keep them if they also pass the bucket gate, marked "bucket" source.
        missing = [w for w in gated if w.entry_date not in cand_map]
        scored = knn_top_n(user=user_features, candidates=cand_map, k=int(criteria.knn_top_n))
        kept_dates = {s.trade_date for s in scored}
        kept_by_date = {w.entry_date: w for w in gated if w.entry_date in kept_dates}
        # Preserve KNN rank order.
        ordered_knn = [kept_by_date[s.trade_date] for s in scored if s.trade_date in kept_by_date]
        # Append bucket-fallback windows (legacy RV20 gate).
        bucket_fallback = [
            w for w in missing
            if _bucket_distance(w.regime_bucket, criteria.target_regime) <= tol_buckets
        ]
        out = ordered_knn + bucket_fallback
        match_quality = summarize_match_quality(scored)
        match_quality["source"] = "knn"
        match_quality["kBucketFallback"] = int(len(bucket_fallback))
        match_quality["kKnn"] = int(len(ordered_knn))
        if return_match_quality:
            return out, match_quality
        return out

    if return_match_quality:
        return gated, None
    return gated


def map_user_strikes_to_analogue(
    *,
    user_spot: float,
    user_em_pct: float,
    analogue_spot: float,
    analogue_em_pct: float,
    user_strikes: Tuple[float, float, float, float],
) -> Tuple[float, float, float, float]:
    """Translate user strikes → analogue strike space preserving EM-distance.

    EM-distance (σ units) for a user strike K:
        z = ((K / user_spot) - 1) * 100 / user_em_pct

    Analogue strike with the same z:
        K_a = analogue_spot * (1 + z * analogue_em_pct / 100)

    Returns strikes in the same order as `user_strikes`:
        (short_put, long_put, short_call, long_call)
    """
    if user_em_pct <= 0 or analogue_em_pct <= 0 or user_spot <= 0 or analogue_spot <= 0:
        raise ValueError("spot and EM must be positive")

    out: List[float] = []
    for K in user_strikes:
        z = ((float(K) / float(user_spot)) - 1.0) * 100.0 / float(user_em_pct)
        Ka = float(analogue_spot) * (1.0 + z * float(analogue_em_pct) / 100.0)
        out.append(round(Ka, 2))
    return (out[0], out[1], out[2], out[3])
