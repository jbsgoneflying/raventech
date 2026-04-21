"""Monte Carlo simulator for the SPX IC Command Deck.

Resamples historical weekly returns (or GBM-style daily paths) to
produce a forward-looking distribution of:

- ``breach_close_prob``  — P(close at expiry outside shorts)
- ``touch_intraweek_prob`` — P(spot touched a short strike during the hold)
- ``outside_wings_prob`` — P(close outside the long strikes)
- ``mae_distribution``    — simulated intraweek max-adverse-excursion

Two modes:

1. ``bootstrap`` (default): resample entire historical weekly paths
   (open, daily closes, expiry close) from a pool filtered by
   ``(regime_bucket, macro_proximity_bucket)``. Each sample is a
   genuine historical path scaled to today's ``(spot, em_1sigma_pct)``
   by matching z-score move.
2. ``gbm``: falls back to a daily-step geometric Brownian motion walk
   with empirical daily vol from the pool, used when the pool after
   conditioning is too thin (< ``min_pool``) and the desk has opted
   in to a model-based fallback.

Pool-conditioning hierarchy (same pattern as
:mod:`backend.mc_simulator`):

1. Requested (regime_bucket, macro_bucket) — only if both populate.
2. Regime-only bucket fallback.
3. Unconditioned fallback (full weekly pool) with a
   "conditioning_degraded" note on the response.

All randomness is seeded from
``(ticker, as_of_date, n_sims, flags_fp, conditioning_key)`` so the
Command Deck's cache hits yield identical results run-to-run.
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import random
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

LOG = logging.getLogger("engine2.mc_simulator")


# ---------------------------------------------------------------------------
# Public dataclasses
# ---------------------------------------------------------------------------


@dataclass
class MCPlacementResult:
    """Per-placement MC reading."""

    em_mult:               float = 0.0
    wing_pts:              float = 0.0
    breach_close_prob:     float = 0.0       # P(|close_return| > short_dist) at expiry
    touch_intraweek_prob:  float = 0.0       # P(max |intraweek move| >= short_dist)
    outside_wings_prob:    float = 0.0       # P(|close_return| > long_dist)
    mae_p50_pct:           float = 0.0
    mae_p75_pct:           float = 0.0
    mae_p90_pct:           float = 0.0
    mae_p95_pct:           float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class MCResult:
    """Top-level MC reading for a scan."""

    n_sims:                int = 0
    pool_size_used:        int = 0
    pool_size_total:       int = 0
    mode:                  str = "bootstrap"   # "bootstrap" | "gbm" | "unavailable"
    conditioning_used:     str = "unconditioned"   # or "regime+macro" / "regime" / "unconditioned"
    conditioning_key:      Dict[str, Any] = field(default_factory=dict)
    placements:            List[MCPlacementResult] = field(default_factory=list)
    seed:                  int = 0
    notes:                 List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["placements"] = [p.to_dict() for p in self.placements]
        return d


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _percentile(values: List[float], pct: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return float(abs(values[0]))
    xs = sorted([abs(x) for x in values])
    k = (pct / 100.0) * (len(xs) - 1)
    lo = int(math.floor(k))
    hi = int(math.ceil(k))
    if lo == hi:
        return float(xs[lo])
    frac = k - lo
    return float(xs[lo] + (xs[hi] - xs[lo]) * frac)


def _seed_from_context(*parts: Any) -> int:
    """Deterministic seed from arbitrary string parts."""
    payload = json.dumps(list(parts), sort_keys=True, default=str)
    h = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    return int(h[:15], 16)


# ---------------------------------------------------------------------------
# Pool conditioning
# ---------------------------------------------------------------------------


def _condition_pool(
    *,
    pool:            Sequence[Dict[str, Any]],
    want_regime:     Optional[str],
    want_macro:      Optional[str],
    min_pool:        int,
) -> Tuple[List[Dict[str, Any]], str]:
    """Filter the weekly pool through the standard conditioning
    hierarchy. Returns the filtered pool + a label describing which
    tier won.
    """
    if not pool:
        return [], "empty_pool"

    # Tier 1: both filters active.
    if want_regime and want_macro:
        tier1 = [
            r for r in pool
            if str(r.get("regime_bucket") or "").upper() == str(want_regime).upper()
            and str(r.get("macro_bucket") or "").upper() == str(want_macro).upper()
        ]
        if len(tier1) >= min_pool:
            return tier1, "regime+macro"

    # Tier 2: regime only.
    if want_regime:
        tier2 = [
            r for r in pool
            if str(r.get("regime_bucket") or "").upper() == str(want_regime).upper()
        ]
        if len(tier2) >= min_pool:
            return tier2, "regime"

    # Tier 3: unconditioned.
    return list(pool), "unconditioned"


# ---------------------------------------------------------------------------
# Path samplers
# ---------------------------------------------------------------------------


def _bootstrap_path(
    *,
    rng:            random.Random,
    pool:           Sequence[Dict[str, Any]],
    hold_days:      int,
) -> List[float]:
    """Draw one historical week's normalised returns.

    Each pool entry is expected to carry:

    - ``daily_returns`` : list of day-over-day log returns across the hold
      window (length ~ 4–5 for a Mon->Fri weekly), OR
    - ``signed_move_pct`` : single close-to-close % move as a fallback.

    Returns a list of per-day simple %-returns (one entry per trading
    day through the hold window).
    """
    row = rng.choice(pool)
    dr = row.get("daily_returns") or []
    if isinstance(dr, list) and len(dr) >= 1:
        # Clip / pad to hold_days. Most weekly pools have exactly the
        # right length; pad-with-zero handles short weeks.
        out = [float(x) for x in dr if _as_float(x) is not None][:hold_days]
        while len(out) < hold_days:
            out.append(0.0)
        return out
    # Fallback: single close-to-close; distribute evenly across days.
    total = _as_float(row.get("signed_move_pct") or row.get("signedMovePct") or row.get("returnPct"))
    if total is None:
        total = 0.0
    daily = float(total) / 100.0 / max(1, hold_days)
    return [daily] * hold_days


def _gbm_path(
    *,
    rng:            random.Random,
    daily_vol:      float,
    daily_drift:    float,
    hold_days:      int,
) -> List[float]:
    """One GBM-style daily path for the fallback mode.

    Returns per-day simple returns (not log). ``daily_vol`` is stddev
    of daily log-returns; ``daily_drift`` is the mean log-return per
    day (usually ~0 for SPX over a week).
    """
    out: List[float] = []
    for _ in range(max(1, hold_days)):
        log_r = rng.gauss(daily_drift, daily_vol)
        out.append(math.exp(log_r) - 1.0)
    return out


# ---------------------------------------------------------------------------
# Core path -> placement stats
# ---------------------------------------------------------------------------


def _accumulate_stats(
    *,
    paths:      List[List[float]],
    em_pct:     float,
    placements: Sequence[Tuple[float, float]],
) -> List[MCPlacementResult]:
    """For each (em_mult, wing_pts) placement, count breaches and
    touches across the simulated paths. Returns per-placement
    :class:`MCPlacementResult` entries.

    ``em_pct`` is today's 1σ weekly EM in % (e.g. 1.8 for 1.8%).
    Short strike distance = ``em_mult * em_pct`` (percent).
    Long strike distance  = short + wing_pts_as_pct.

    Wing points are converted to percent via
    ``wing_pct = wing_pts / spot * 100`` at call-site, so this inner
    helper takes the pre-resolved ``(em_mult, wing_pct)`` tuples.
    """
    results: List[MCPlacementResult] = []
    n = len(paths)
    if n == 0:
        return [MCPlacementResult(em_mult=em, wing_pts=wp) for (em, wp) in placements]

    # Pre-compute the intraweek cumulative trajectory (% move vs entry) + the
    # close-to-close total for each path so we don't recompute per placement.
    close_moves_pct: List[float] = []
    intraweek_extrema_pct: List[float] = []
    per_path_mae: List[List[float]] = []
    for p in paths:
        cum = 0.0
        extreme = 0.0
        path_cum_abs: List[float] = []
        for r in p:
            cum = (1.0 + cum) * (1.0 + r) - 1.0
            path_cum_abs.append(abs(cum) * 100.0)
            if abs(cum) > abs(extreme):
                extreme = cum
        close_moves_pct.append(cum * 100.0)
        intraweek_extrema_pct.append(abs(extreme) * 100.0)
        per_path_mae.append(path_cum_abs)

    for (em, wp_pct) in placements:
        short_dist = float(em) * float(em_pct)
        long_dist = short_dist + float(wp_pct)

        breach_close = 0
        touch_intraweek = 0
        outside_wings = 0
        mae_samples: List[float] = []

        for i in range(n):
            c = abs(close_moves_pct[i])
            e = intraweek_extrema_pct[i]
            if c > short_dist:
                breach_close += 1
            if e >= short_dist:
                touch_intraweek += 1
            if c > long_dist:
                outside_wings += 1
            # MAE for this placement: max |move| - short_dist, floored at 0;
            # we store the raw extreme for p95 reporting.
            mae_samples.append(e)

        results.append(MCPlacementResult(
            em_mult=round(float(em), 3),
            wing_pts=round(float(wp_pct), 3),    # stored as percent here; caller re-interprets
            breach_close_prob=round(breach_close / n, 4),
            touch_intraweek_prob=round(touch_intraweek / n, 4),
            outside_wings_prob=round(outside_wings / n, 4),
            mae_p50_pct=round(_percentile(mae_samples, 50), 3),
            mae_p75_pct=round(_percentile(mae_samples, 75), 3),
            mae_p90_pct=round(_percentile(mae_samples, 90), 3),
            mae_p95_pct=round(_percentile(mae_samples, 95), 3),
        ))
    return results


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------


def run_weekly_mc(
    *,
    ticker:              str,
    as_of_date:          str,
    spot:                float,
    em_pct:              float,
    hold_days:            int,
    weekly_pool:         Sequence[Dict[str, Any]],
    placements:          Sequence[Tuple[float, float]],   # list of (em_mult, wing_pts)
    n_sims:              int = 5000,
    min_pool:            int = 20,
    seed:                int = 1337,
    condition_on_regime: bool = True,
    condition_on_macro:  bool = True,
    want_regime_bucket:  Optional[str] = None,
    want_macro_bucket:   Optional[str] = None,
    gbm_fallback:        bool = False,
    flags_fp:            Optional[Tuple[Any, ...]] = None,
) -> MCResult:
    """Run the full MC pass for one scan.

    Returns an :class:`MCResult` with a per-placement reading for every
    ``(em_mult, wing_pts)`` tuple passed in.

    Deterministic for fixed ``(ticker, as_of_date, n_sims, flags_fp,
    conditioning_key)``.
    """
    if not weekly_pool:
        return MCResult(n_sims=0, pool_size_total=0, mode="unavailable",
                        notes=["weekly_pool empty"])
    if spot <= 0 or em_pct <= 0:
        return MCResult(n_sims=0, pool_size_total=len(weekly_pool), mode="unavailable",
                        notes=["invalid spot or em_pct"])
    if not placements:
        return MCResult(n_sims=int(n_sims), pool_size_total=len(weekly_pool),
                        notes=["no placements provided"])

    wr = want_regime_bucket if condition_on_regime else None
    wm = want_macro_bucket if condition_on_macro else None
    pool, used = _condition_pool(
        pool=list(weekly_pool), want_regime=wr, want_macro=wm, min_pool=int(min_pool),
    )

    conditioning_key = {
        "wantRegime": wr,
        "wantMacro":  wm,
        "used":       used,
    }
    seed_eff = _seed_from_context(ticker, as_of_date, int(n_sims), flags_fp or (), conditioning_key)
    rng = random.Random(seed_eff)

    # Convert wing-points to percent-of-spot so we can reuse one
    # MAE formula regardless of wing grid.
    placements_pct = [(float(em), float(wp) / float(spot) * 100.0) for (em, wp) in placements]

    notes: List[str] = []
    mode = "bootstrap"
    if pool and any((r.get("daily_returns") or r.get("signed_move_pct") is not None) for r in pool):
        path_fn = lambda: _bootstrap_path(rng=rng, pool=pool, hold_days=max(1, hold_days))
    elif gbm_fallback:
        # Fall back to GBM from pool-wide vol.
        moves = [_as_float(r.get("signed_move_pct") or r.get("signedMovePct")) for r in pool]
        moves = [m for m in moves if m is not None]
        if not moves:
            return MCResult(n_sims=0, pool_size_total=len(weekly_pool),
                            pool_size_used=len(pool), mode="unavailable",
                            notes=["pool has no usable returns"])
        sigma_weekly = statistics_stdev(moves) / 100.0
        sigma_daily = sigma_weekly / math.sqrt(max(1, hold_days))
        mode = "gbm"
        path_fn = lambda: _gbm_path(rng=rng, daily_vol=sigma_daily, daily_drift=0.0, hold_days=max(1, hold_days))
        notes.append("gbm fallback engaged (pool lacked per-day returns).")
    else:
        return MCResult(n_sims=0, pool_size_total=len(weekly_pool), pool_size_used=len(pool),
                        mode="unavailable",
                        notes=["pool lacks daily_returns and gbm_fallback=False"])

    if used == "unconditioned" and (wr or wm):
        notes.append("conditioning_degraded: regime/macro bucket filters yielded < min_pool rows")

    paths: List[List[float]] = [path_fn() for _ in range(int(n_sims))]

    per_placement = _accumulate_stats(
        paths=paths, em_pct=float(em_pct), placements=placements_pct,
    )
    # Restore original wing_pts into the result (we passed percent to
    # the accumulator for internal math).
    for (src_em, src_wp), r in zip(placements, per_placement):
        r.wing_pts = float(src_wp)

    return MCResult(
        n_sims=int(n_sims),
        pool_size_used=int(len(pool)),
        pool_size_total=int(len(weekly_pool)),
        mode=mode,
        conditioning_used=used,
        conditioning_key=conditioning_key,
        placements=per_placement,
        seed=int(seed_eff),
        notes=notes,
    )


# ---------------------------------------------------------------------------
# statistics.stdev proxy (avoid a hard statistics import at module top so
# pure-sim usage doesn't crash on exotic runtimes).
# ---------------------------------------------------------------------------


def statistics_stdev(values: Sequence[float]) -> float:
    import statistics as _s
    if len(values) < 2:
        return 0.0
    return float(_s.pstdev(values))
