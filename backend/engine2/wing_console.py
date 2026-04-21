"""Wing Decision Console scorer for the SPX IC Command Deck.

Heart of E2 v2. Takes the SPX IC engine payload (weekly breach pool,
live EM, regime + macro context, live chain credit estimate) plus
pre-computed MAE + MC distributions and emits a ranked list of
:class:`PlacementScore` candidates.

Scoring (see :class:`WingConsoleWeights`):

    composite = 100 * (
        w_close  * (1 - breach_close_prob) +
        w_touch  * (1 - touch_intraweek_prob) +
        w_mae    * (1 - clamp(mae_p95_vs_wing / MAX_TOLERABLE_MAE, 0, 1)) +
        w_theta  * clamp(theta_capture / TARGET_THETA, 0, 1) +
        w_credit * clamp(roc_est / TARGET_ROC, 0, 1)
    )

All five terms are in ``[0, 1]`` after their clamps, so the weighted
sum is bounded and renormalised by the total weight at scoring time.

Pure Python, no numpy; deterministic for fixed inputs so
:mod:`backend.engine2.shared_cache` is safe to hit.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from backend.config import FeatureFlags, get_flags
from backend.engine2.mae_proxy import MAEDistribution, mae_p95_vs_wing_ratio
from backend.engine2.mc_simulator import (
    MCPlacementResult, MCResult, run_weekly_mc,
)
from backend.engine2.scoring_context import (
    ScoringContext, get_scoring_context, store_scoring_context,
)

LOG = logging.getLogger("engine2.wing_console")


# ---------------------------------------------------------------------------
# Weights + placement dataclasses
# ---------------------------------------------------------------------------


@dataclass
class WingConsoleWeights:
    """Composite-score weights. Renormalised against the running weight
    total so the desk can tune individual terms without rescaling
    neighbours."""

    close:  float = 0.25
    touch:  float = 0.20
    mae:    float = 0.25
    theta:  float = 0.15
    credit: float = 0.15

    # Normalisation targets
    max_tolerable_mae_pct: float = 80.0   # MAE % of wing above which "forced close"
    target_theta_pct:      float = 60.0
    target_roc_pct:        float = 12.0

    def as_dict(self) -> Dict[str, float]:
        return asdict(self)

    @classmethod
    def from_flags(cls, flags: FeatureFlags) -> "WingConsoleWeights":
        return cls(
            close=float(getattr(flags, "E2_WING_SCORE_WEIGHT_CLOSE", 0.25)),
            touch=float(getattr(flags, "E2_WING_SCORE_WEIGHT_TOUCH", 0.20)),
            mae=float(getattr(flags, "E2_WING_SCORE_WEIGHT_MAE", 0.25)),
            theta=float(getattr(flags, "E2_WING_SCORE_WEIGHT_THETA", 0.15)),
            credit=float(getattr(flags, "E2_WING_SCORE_WEIGHT_CREDIT", 0.15)),
            max_tolerable_mae_pct=float(getattr(flags, "E2_WING_MAX_TOLERABLE_MAE_PCT", 80.0)),
            target_theta_pct=float(getattr(flags, "E2_WING_TARGET_THETA_PCT", 60.0)),
            target_roc_pct=float(getattr(flags, "E2_WING_TARGET_ROC_PCT", 12.0)),
        )


DEFAULT_WEIGHTS = WingConsoleWeights()


@dataclass
class PlacementScore:
    """One scored candidate placement. Stable shape for the API."""

    em_mult:             float = 0.0
    wing_pts:            float = 0.0

    short_put_strike:    Optional[float] = None
    short_call_strike:   Optional[float] = None
    long_put_strike:     Optional[float] = None
    long_call_strike:    Optional[float] = None

    # Risk metrics (0..1, lower = better)
    breach_close_prob:     float = 0.0
    touch_intraweek_prob:  float = 0.0
    outside_wings_prob:    float = 0.0
    mae_p95_vs_wing:       float = 0.0   # fraction of wing width; clamped [0, 1.5]

    # Reward metrics
    theta_capture_pct:   float = 0.0    # % of entry credit retained by planned exit
    credit_est:          float = 0.0    # entry credit in points
    credit_dollars:      float = 0.0
    max_loss:            float = 0.0
    roc_est:             float = 0.0    # credit / max_loss in %

    composite_score:     float = 0.0
    composite_breakdown: Dict[str, float] = field(default_factory=dict)

    n_historical:        int = 0
    n_mc_sims:           int = 0
    confidence:          str = "low"
    mae_source:          str = ""
    notes:               List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class WingConsolePayload:
    """Top-level response the frontend + advisor consume."""

    underlying:       str = ""
    entry_day:        str = ""
    as_of_date:       str = ""
    spot:             Optional[float] = None
    em_pct:           Optional[float] = None

    regime_label:     Optional[str] = None
    regime_bucket:    Optional[str] = None
    regime_mi_v2:     Optional[Dict[str, Any]] = None
    macro_bucket:     Optional[str] = None

    n_historical:     int = 0
    placements:       List[PlacementScore] = field(default_factory=list)
    grid:             Dict[str, Any] = field(default_factory=dict)
    weights_used:     Dict[str, float] = field(default_factory=dict)
    mae:              Dict[str, Any] = field(default_factory=dict)
    mc:               Dict[str, Any] = field(default_factory=dict)
    warnings:         List[str] = field(default_factory=list)
    generated_at:     str = ""
    cache_key:        str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["placements"] = [p.to_dict() for p in self.placements]
        return d


# ---------------------------------------------------------------------------
# Small utils
# ---------------------------------------------------------------------------


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


def _parse_grid_floats(raw: Any, fallback: Sequence[float]) -> List[float]:
    if isinstance(raw, (list, tuple)):
        vals = [_as_float(x) for x in raw]
        return [v for v in vals if v is not None and v > 0] or list(fallback)
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        vals = [_as_float(p) for p in parts]
        vals = [v for v in vals if v is not None and v > 0]
        return vals or list(fallback)
    return list(fallback)


# ---------------------------------------------------------------------------
# Theta + credit helpers
# ---------------------------------------------------------------------------


def _estimate_theta_capture_pct(
    *,
    hold_days:          int,
    dte_calendar_days:  int,
) -> float:
    """Very simple BS-approximation of what fraction of the entry
    credit a delta-neutral short IC retains by the planned exit.

    ``theta_capture_pct = 1 - sqrt(remaining / total_dte)``

    For a 5-day weekly IC held to Friday close, remaining=0 so
    capture = 100%. For a Mon-enter Wed-exit hold on a Fri expiry,
    remaining = 2/5 -> capture = 1 - sqrt(0.4) ~= 37%.
    """
    if dte_calendar_days <= 0:
        return 0.0
    hd = max(0, min(int(dte_calendar_days), int(hold_days)))
    remaining = max(0, int(dte_calendar_days) - hd)
    frac = float(remaining) / float(dte_calendar_days)
    return float(_clamp((1.0 - math.sqrt(frac)) * 100.0, 0.0, 100.0))


def _estimate_credit_points(
    *,
    em_multiple:       float,
    wing_pts:          float,
    implied_move_pts:  float,
) -> float:
    """Normal-IV closed-form proxy for entry credit in points.

    Same shape E1 v2 uses in the Wing Console: credit falls off
    roughly as ``2 * im * phi(em_multiple)`` with phi the standard
    normal density. Clamped at ``0.9 * wing_pts`` so it never
    exceeds the theoretical max credit for the spread.
    """
    if em_multiple <= 0 or wing_pts <= 0 or implied_move_pts <= 0:
        return 0.0
    try:
        phi = math.exp(-0.5 * em_multiple * em_multiple) / math.sqrt(2.0 * math.pi)
        credit = 2.0 * implied_move_pts * phi
        return float(_clamp(credit, 0.0, wing_pts * 0.9))
    except Exception:
        return 0.0


# ---------------------------------------------------------------------------
# Historical breach-close fallback (when MC pool is too thin)
# ---------------------------------------------------------------------------


def _historical_breach_prob(
    events: Sequence[Dict[str, Any]],
    *,
    em_multiple: float,
    em_pct_today: float,
) -> Tuple[float, int]:
    """Fallback estimator: fraction of historical weeks where the
    close-to-close move exceeded ``em_multiple * em_pct_today``.

    Returns ``(prob, n)``. Used when the MC pool is unavailable.
    """
    if em_multiple <= 0 or em_pct_today <= 0 or not events:
        return 0.0, 0
    thresh = em_multiple * em_pct_today
    n = 0
    breaches = 0
    for e in events:
        m = _as_float(e.get("signed_move_pct") or e.get("signedMovePct") or e.get("returnPct"))
        if m is None:
            continue
        n += 1
        if abs(m) > thresh:
            breaches += 1
    if n == 0:
        return 0.0, 0
    return breaches / n, n


# ---------------------------------------------------------------------------
# Per-placement scorer
# ---------------------------------------------------------------------------


def _score_one(
    *,
    em_mult:           float,
    wing_pts:          float,
    spot:              float,
    em_pct:            float,
    hold_days:         int,
    dte_calendar_days: int,
    mae_p95_pct:       float,
    mc_placement:      Optional[MCPlacementResult],
    historical_events: Sequence[Dict[str, Any]],
    weights:           WingConsoleWeights,
) -> PlacementScore:
    # Strike geometry
    short_dist_pts = (float(em_mult) * float(em_pct) / 100.0) * float(spot)
    short_put = float(spot) - short_dist_pts
    short_call = float(spot) + short_dist_pts
    long_put = short_put - float(wing_pts)
    long_call = short_call + float(wing_pts)
    max_loss = float(wing_pts)

    # MC-first; historical fallback if MC missing
    if mc_placement is not None:
        breach_close = float(mc_placement.breach_close_prob)
        touch_intraweek = float(mc_placement.touch_intraweek_prob)
        outside_wings = float(mc_placement.outside_wings_prob)
        n_mc_sims = 0  # filled below from mc_result.n_sims upstream
    else:
        hb, _n = _historical_breach_prob(historical_events, em_multiple=em_mult, em_pct_today=em_pct)
        breach_close = hb
        touch_intraweek = hb   # coarse proxy; MC would distinguish
        outside_wings = max(0.0, hb - 0.05)  # slightly below close-breach
        n_mc_sims = 0

    mae_vs_wing = mae_p95_vs_wing_ratio(
        mae_p95_pct=float(mae_p95_pct),
        em_multiple=float(em_mult),
        implied_move_pct=float(em_pct),
        wing_width_pts=float(wing_pts),
        spot=float(spot),
    )

    # Theta capture (simple BS-ish approximation)
    theta_pct = _estimate_theta_capture_pct(hold_days=hold_days, dte_calendar_days=dte_calendar_days)

    # Credit + ROC
    implied_move_pts = float(spot) * float(em_pct) / 100.0
    credit_pts = _estimate_credit_points(
        em_multiple=em_mult, wing_pts=wing_pts, implied_move_pts=implied_move_pts,
    )
    credit_dollars = credit_pts * 100.0
    roc = 0.0
    denom = max_loss - credit_pts
    if credit_pts > 0 and denom > 0:
        roc = (credit_pts / denom) * 100.0

    # Composite
    parts = {
        "close":  weights.close  * (1.0 - breach_close),
        "touch":  weights.touch  * (1.0 - touch_intraweek),
        "mae":    weights.mae    * (1.0 - _clamp(mae_vs_wing * 100.0 / max(1.0, weights.max_tolerable_mae_pct), 0.0, 1.0)),
        "theta":  weights.theta  * _clamp(theta_pct / max(1.0, weights.target_theta_pct), 0.0, 1.0),
        "credit": weights.credit * _clamp(roc / max(0.01, weights.target_roc_pct), 0.0, 1.0),
    }
    weight_total = sum(abs(w) for w in [
        weights.close, weights.touch, weights.mae, weights.theta, weights.credit,
    ]) or 1.0
    composite = 100.0 * sum(parts.values()) / weight_total

    # Confidence
    if mc_placement is not None:
        confidence = "high"
    elif len(historical_events) >= 60:
        confidence = "med"
    else:
        confidence = "low"

    return PlacementScore(
        em_mult=round(float(em_mult), 4),
        wing_pts=round(float(wing_pts), 3),
        short_put_strike=round(short_put, 2),
        short_call_strike=round(short_call, 2),
        long_put_strike=round(long_put, 2),
        long_call_strike=round(long_call, 2),
        breach_close_prob=round(breach_close, 4),
        touch_intraweek_prob=round(touch_intraweek, 4),
        outside_wings_prob=round(outside_wings, 4),
        mae_p95_vs_wing=round(mae_vs_wing, 3),
        theta_capture_pct=round(theta_pct, 2),
        credit_est=round(credit_pts, 4),
        credit_dollars=round(credit_dollars, 2),
        max_loss=round(max_loss, 2),
        roc_est=round(roc, 2),
        composite_score=round(composite, 2),
        composite_breakdown={k: round(v, 4) for k, v in parts.items()},
        n_historical=int(len(historical_events)),
        n_mc_sims=int(n_mc_sims),
        confidence=confidence,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_placements(
    *,
    underlying:        str,
    spot:              float,
    em_pct:            float,
    hold_days:         int,
    dte_calendar_days: int,
    historical_events: Sequence[Dict[str, Any]],
    mae:               Optional[MAEDistribution] = None,
    mc_result:         Optional[MCResult] = None,
    em_mults:          Optional[Sequence[float]] = None,
    wing_pts:          Optional[Sequence[float]] = None,
    weights:           Optional[WingConsoleWeights] = None,
) -> List[PlacementScore]:
    """Score the full grid and return placements ranked by composite.

    ``mc_result`` should carry per-placement entries for every
    ``(em_mult, wing_pts)`` tuple. When missing, the scorer falls back
    to historical close-breach rates and marks confidence=low.
    """
    if spot <= 0 or em_pct <= 0:
        return []

    w = weights or DEFAULT_WEIGHTS
    em_list = list(em_mults) if em_mults else [1.0, 1.25, 1.5, 2.0]
    wing_list = list(wing_pts) if wing_pts else [5.0, 10.0, 15.0]

    # Index MC placements by (em_mult, wing_pts_in_points) for O(1) lookup.
    mc_by_pair: Dict[Tuple[float, float], MCPlacementResult] = {}
    n_mc_sims = 0
    if mc_result is not None and mc_result.placements:
        n_mc_sims = int(mc_result.n_sims)
        for pr in mc_result.placements:
            # MCPlacementResult.wing_pts was re-hydrated to original points
            # in run_weekly_mc, so this matches our (em, wp) tuple directly.
            mc_by_pair[(round(float(pr.em_mult), 4), round(float(pr.wing_pts), 3))] = pr

    mae_p95 = float(mae.p95) if (mae and mae.n > 0) else 0.0

    placements: List[PlacementScore] = []
    for em in em_list:
        for wp in wing_list:
            key = (round(float(em), 4), round(float(wp), 3))
            mcp = mc_by_pair.get(key)
            ps = _score_one(
                em_mult=float(em),
                wing_pts=float(wp),
                spot=float(spot),
                em_pct=float(em_pct),
                hold_days=int(hold_days),
                dte_calendar_days=int(dte_calendar_days),
                mae_p95_pct=mae_p95,
                mc_placement=mcp,
                historical_events=historical_events,
                weights=w,
            )
            if mcp is not None:
                ps.n_mc_sims = int(n_mc_sims)
            placements.append(ps)

    placements.sort(
        key=lambda p: (-p.composite_score, p.breach_close_prob, p.touch_intraweek_prob)
    )
    return placements


def score_single_placement(
    *,
    context:          ScoringContext,
    em_mult:          float,
    wing_pts:         float,
    weights_override: Optional[WingConsoleWeights] = None,
) -> PlacementScore:
    """Exact-slider scoring against a cached :class:`ScoringContext`.

    The frontend slider POSTs to the score-placement endpoint with
    arbitrary ``(em_mult, wing_pts)`` — this helper re-runs MC for
    that single pair against the context's cached pool, then applies
    the same composite formula.
    """
    w = weights_override or DEFAULT_WEIGHTS
    # Re-run MC for just this placement (~100x cheaper than full grid
    # when pool + n_sims are the same). Uses the context's flags_fp to
    # stay deterministic w.r.t. the primary scan.
    mc = run_weekly_mc(
        ticker=context.underlying,
        as_of_date=context.as_of_date,
        spot=context.spot,
        em_pct=context.em_pct,
        hold_days=context.hold_days,
        weekly_pool=context.weekly_pool,
        placements=[(float(em_mult), float(wing_pts))],
        n_sims=5000,
        min_pool=20,
        want_regime_bucket=context.regime_bucket,
        want_macro_bucket=context.macro_bucket,
        flags_fp=context.flags_fp,
    )
    mcp = mc.placements[0] if mc.placements else None
    mae_dict = context.mae_dist or {}
    mae_obj: Optional[MAEDistribution] = None
    if mae_dict and int(mae_dict.get("n") or 0) > 0:
        mae_obj = MAEDistribution(
            n=int(mae_dict.get("n") or 0),
            p50=float(mae_dict.get("p50") or 0.0),
            p75=float(mae_dict.get("p75") or 0.0),
            p90=float(mae_dict.get("p90") or 0.0),
            p95=float(mae_dict.get("p95") or 0.0),
            max=float(mae_dict.get("max") or 0.0),
            source=str(mae_dict.get("source") or "daily_ohlc"),
        )
    mae_p95 = float(mae_obj.p95) if (mae_obj and mae_obj.n > 0) else 0.0

    # DTE for theta: approximate remaining calendar days = hold_days
    # when slider re-runs (context stores same hold for single scan).
    return _score_one(
        em_mult=float(em_mult),
        wing_pts=float(wing_pts),
        spot=float(context.spot),
        em_pct=float(context.em_pct),
        hold_days=int(context.hold_days),
        dte_calendar_days=int(context.hold_days),
        mae_p95_pct=mae_p95,
        mc_placement=mcp,
        historical_events=context.weekly_pool,
        weights=w,
    )


def run_mc_for_placement(
    *,
    context:  ScoringContext,
    em_mult:  float,
    wing_pts: float,
    n_sims:   int = 5000,
) -> MCResult:
    """Thin wrapper: run MC for exactly one placement against a cached context."""
    return run_weekly_mc(
        ticker=context.underlying,
        as_of_date=context.as_of_date,
        spot=context.spot,
        em_pct=context.em_pct,
        hold_days=context.hold_days,
        weekly_pool=context.weekly_pool,
        placements=[(float(em_mult), float(wing_pts))],
        n_sims=int(n_sims),
        min_pool=20,
        want_regime_bucket=context.regime_bucket,
        want_macro_bucket=context.macro_bucket,
        flags_fp=context.flags_fp,
    )


# ---------------------------------------------------------------------------
# High-level builder
# ---------------------------------------------------------------------------


def _extract_weekly_pool_from_engine_payload(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Normalise the SPX IC engine's ``weeks`` / ``riskGrid.cells`` list
    into a pool of dicts with a ``signed_move_pct`` + optional
    ``daily_returns`` + ``regime_bucket`` / ``macro_bucket`` for MC
    bootstrap.
    """
    pool: List[Dict[str, Any]] = []
    # The engine emits `payload["weeks"]` in the desk-friendly flat form
    # plus a richer per-event list we prefer when present.
    weeks = payload.get("weeks") or []
    if not weeks:
        # Fallback: some callers see the scan via `riskGrid.cells` which
        # aggregates rather than per-week; leave pool empty if missing.
        return pool
    for w in weeks:
        sm = w.get("signedMovePct")
        if sm is None:
            sm = w.get("signed_move_pct")
        if sm is None:
            sm = w.get("returnPct")
        if sm is None:
            continue
        pool.append({
            "entry_date":      w.get("entryDate"),
            "expiry_date":     w.get("expiryDate"),
            "entry_close":     w.get("entryPx"),
            "signed_move_pct": float(sm),
            "daily_returns":   w.get("dailyReturns") or [],
            "regime_bucket":   w.get("regimeBucket") or w.get("bucket"),
            "macro_bucket":    w.get("macroBucket") or w.get("mb"),
            "season":          w.get("season") or w.get("seasonality"),
        })
    return pool


def build_wing_console(
    *,
    underlying:    str,
    entry_day:     str,
    as_of_date:    str,
    spx_payload:   Dict[str, Any],
    mae:           Optional[MAEDistribution] = None,
    mc_result:     Optional[MCResult] = None,
    weights:       Optional[WingConsoleWeights] = None,
    em_mults:      Optional[Sequence[float]] = None,
    wing_pts:      Optional[Sequence[float]] = None,
    flags:         Optional[FeatureFlags] = None,
) -> WingConsolePayload:
    """High-level builder — feeds the router + Command Deck UI."""
    flags = flags or get_flags()
    w = weights or WingConsoleWeights.from_flags(flags)

    if em_mults is None:
        em_mults = _parse_grid_floats(
            getattr(flags, "E2_WING_EM_MULTS", None),
            fallback=[1.0, 1.25, 1.5, 2.0],
        )
    if wing_pts is None:
        wing_pts = _parse_grid_floats(
            getattr(flags, "E2_WING_PTS", None),
            fallback=[5.0, 10.0, 15.0],
        )

    current = spx_payload.get("current") or {}
    expected_move = spx_payload.get("expectedMove") or {}
    regime = spx_payload.get("regime") or {}
    spot = (
        _as_float(current.get("stockPrice"))
        or _as_float(expected_move.get("spotPrice"))
        or _as_float(expected_move.get("smartSpotPrice"))
    )
    em_pct = (
        _as_float(expected_move.get("expectedMovePct"))
        or _as_float(expected_move.get("oratsExpectedMovePct"))
    )
    hold_days = int(spx_payload.get("holdDaysTargeted") or expected_move.get("dte") or 5)
    dte_cal = int(expected_move.get("dte") or hold_days)

    warnings: List[str] = []
    if spot is None or spot <= 0:
        warnings.append("no spot price available; placements suppressed.")
    if em_pct is None or em_pct <= 0:
        warnings.append("no expected-move % available; placements suppressed.")

    pool = _extract_weekly_pool_from_engine_payload(spx_payload)

    placements: List[PlacementScore] = []
    if (spot is not None and spot > 0) and (em_pct is not None and em_pct > 0):
        placements = score_placements(
            underlying=underlying, spot=spot, em_pct=em_pct,
            hold_days=hold_days, dte_calendar_days=dte_cal,
            historical_events=pool,
            mae=mae, mc_result=mc_result,
            em_mults=em_mults, wing_pts=wing_pts,
            weights=w,
        )

        # Publish a ScoringContext for the slider endpoint.
        store_scoring_context(ScoringContext(
            underlying=underlying.upper(),
            entry_day=entry_day,
            as_of_date=as_of_date,
            spot=float(spot),
            em_pct=float(em_pct),
            hold_days=int(hold_days),
            weekly_pool=list(pool),
            mae_dist=(mae.to_dict() if mae else None),
            mc_result=(mc_result.to_dict() if mc_result else None),
            regime_bucket=str(regime.get("bucket") or regime.get("label") or "").upper() or None,
            macro_bucket=str((spx_payload.get("macro") or {}).get("bucket") or "").upper() or None,
            regime_mi_v2=regime.get("mi_v2") if isinstance(regime.get("mi_v2"), dict) else None,
            weights=w.as_dict(),
            flags_fp=tuple(flags.cache_fingerprint() or ()) if hasattr(flags, "cache_fingerprint") else (),
        ))

    grid_sig = hashlib.sha256(
        json.dumps({"em": list(em_mults), "wp": list(wing_pts)}, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

    import datetime as _dt
    return WingConsolePayload(
        underlying=underlying.upper(),
        entry_day=(entry_day or ""),
        as_of_date=(as_of_date or ""),
        spot=spot,
        em_pct=em_pct,
        regime_label=str(regime.get("label") or "") or None,
        regime_bucket=str(regime.get("bucket") or "") or None,
        regime_mi_v2=regime.get("mi_v2") if isinstance(regime.get("mi_v2"), dict) else None,
        macro_bucket=str((spx_payload.get("macro") or {}).get("bucket") or "") or None,
        n_historical=len(pool),
        placements=placements,
        grid={
            "em_mults":  list(em_mults),
            "wing_pts":  list(wing_pts),
            "grid_sig":  grid_sig,
        },
        weights_used=w.as_dict(),
        mae=(mae.to_dict() if mae else {}),
        mc=(mc_result.to_dict() if mc_result else {}),
        warnings=warnings,
        generated_at=_dt.datetime.now(_dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        cache_key=grid_sig,
    )


__all__ = [
    "DEFAULT_WEIGHTS",
    "PlacementScore",
    "WingConsolePayload",
    "WingConsoleWeights",
    "build_wing_console",
    "run_mc_for_placement",
    "score_placements",
    "score_single_placement",
]
