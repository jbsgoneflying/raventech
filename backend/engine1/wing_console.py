"""Wing Decision Console — deterministic scoring engine.

This is the heart of Engine 1 v2. It takes a breach-stats payload
(already computed by :func:`backend.earnings_logic.compute_breach_stats`)
plus the required ``event_date`` + ``event_timing`` and returns a
ranked list of candidate wing placements.

Each candidate is ``(em_multiple, wing_width_pts, symmetry)``. For the
default grid, we score **15 candidates** (5 EM multiples × 3 wing
widths, symmetric only). An asymmetric extension is Phase 2.

Scoring is fully deterministic + cacheable — the LLM advisor is kept
as a separate on-demand narrative layer. See module docstring in
:mod:`backend.engine1` for the full contract.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import math
import threading
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

from cachetools import TTLCache

from backend.config import FeatureFlags, get_flags
from backend.engine1.mae_proxy import MAEDistribution, mae_percentile_to_credit_pct
from backend.engine1.theta_capture import (
    ThetaCaptureReading,
    estimate_theta_capture,
    expected_decay_capture,
)

LOG = logging.getLogger("engine1.wing_console")


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


@dataclass
class WingConsoleWeights:
    """Composite-score weights. Need not sum to 1 — the composite is
    renormalized against the running weight total, so the desk can
    increment one weight without rescaling others."""

    gap:    float = 0.30
    ctc:    float = 0.20
    mae:    float = 0.25
    theta:  float = 0.15
    credit: float = 0.10

    # Normalization anchors
    max_tolerable_mae_pct: float = 60.0  # MAE % of wing above which "white-knuckle"
    target_theta_pct:      float = 50.0
    target_credit_mult:    float = 1.5

    def as_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_flags(cls, flags: FeatureFlags) -> "WingConsoleWeights":
        """Build from config. All knobs named ``E1_WING_*``."""
        return cls(
            gap=float(getattr(flags, "E1_WING_SCORE_WEIGHT_GAP", 0.30)),
            ctc=float(getattr(flags, "E1_WING_SCORE_WEIGHT_CTC", 0.20)),
            mae=float(getattr(flags, "E1_WING_SCORE_WEIGHT_MAE", 0.25)),
            theta=float(getattr(flags, "E1_WING_SCORE_WEIGHT_THETA", 0.15)),
            credit=float(getattr(flags, "E1_WING_SCORE_WEIGHT_CREDIT", 0.10)),
            max_tolerable_mae_pct=float(
                getattr(flags, "E1_WING_MAX_TOLERABLE_MAE_PCT", 60.0)
            ),
            target_theta_pct=float(
                getattr(flags, "E1_WING_TARGET_THETA_PCT", 50.0)
            ),
            target_credit_mult=float(
                getattr(flags, "E1_WING_TARGET_CREDIT_MULT", 1.5)
            ),
        )


DEFAULT_WEIGHTS = WingConsoleWeights()


@dataclass
class PlacementScore:
    """One scored candidate placement. Shape is stable for the API."""

    em_mult:             float = 0.0
    wing_pts:            float = 0.0
    symmetry:            str = "symmetric"

    short_put_strike:    Optional[float] = None
    short_call_strike:   Optional[float] = None
    long_put_strike:     Optional[float] = None
    long_call_strike:    Optional[float] = None

    breach_gap_prob:     float = 0.0
    breach_ctc_prob:     float = 0.0
    mae_p95_pct:         float = 0.0       # MAE p95 as % of wing width
    mae_p95_raw_pct:     float = 0.0       # raw MAE p95 in underlying %

    theta_capture_pct:   float = 0.0       # expected % of entry credit retained
    credit_est:          float = 0.0       # estimated entry credit in points
    credit_dollars:      float = 0.0       # per contract (× 100)

    composite_score:     float = 0.0
    composite_breakdown: Dict[str, float] = field(default_factory=dict)

    n_historical:        int = 0
    mae_source:          str = ""
    confidence:          str = "low"

    notes:               List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class WingConsolePayload:
    """Top-level response — what the frontend + LLM advisor consume."""

    ticker:          str = ""
    event_date:      str = ""
    event_timing:    str = ""
    regime_label:    str = ""
    regime_prob:     Optional[float] = None
    spot:            Optional[float] = None
    implied_move_pct: Optional[float] = None
    n_events:        int = 0

    placements:      List[PlacementScore] = field(default_factory=list)
    grid:            Dict[str, Any] = field(default_factory=dict)      # em_mults / wing_pts / symmetries
    weights_used:    Dict[str, float] = field(default_factory=dict)

    mae:             Dict[str, Any] = field(default_factory=dict)
    theta:           Dict[str, Any] = field(default_factory=dict)

    warnings:        List[str] = field(default_factory=list)
    generated_at:    str = ""
    cache_key:       str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["placements"] = [p.to_dict() for p in self.placements]
        return d


# ---------------------------------------------------------------------------
# Caching
# ---------------------------------------------------------------------------


_console_cache: TTLCache = TTLCache(maxsize=2048, ttl=10 * 60)
_console_cache_lock = threading.Lock()


def _cache_key(
    *,
    ticker: str,
    event_date: str,
    event_timing: str,
    weights: WingConsoleWeights,
    grid_sig: str,
) -> str:
    payload = {
        "t": ticker.upper(),
        "d": event_date,
        "tm": event_timing.upper(),
        "w": weights.as_dict(),
        "g": grid_sig,
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


def _parse_grid_floats(raw: Any, fallback: Sequence[float]) -> List[float]:
    if isinstance(raw, (list, tuple)):
        vals = [_safe_float(x) for x in raw]
        return [v for v in vals if v is not None and v > 0] or list(fallback)
    if isinstance(raw, str):
        parts = [p.strip() for p in raw.split(",") if p.strip()]
        vals = [_safe_float(p) for p in parts]
        vals = [v for v in vals if v is not None and v > 0]
        return vals or list(fallback)
    return list(fallback)


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Core scoring
# ---------------------------------------------------------------------------


def _historic_breach_rate(
    events: Sequence[Dict[str, Any]],
    *,
    em_multiple: float,
    field_name: str,
) -> Tuple[float, int]:
    """Fraction of events where ``|field_name| > em_multiple * impliedMovePct``.

    Returns ``(rate_pct, n_used)``. ``rate_pct`` in ``[0, 1]``.
    """
    if em_multiple <= 0 or not events:
        return 0.0, 0
    n = 0
    breaches = 0
    for ev in events:
        em = _safe_float(ev.get("impliedMovePct"))
        move = _safe_float(ev.get(field_name))
        if em is None or em <= 0 or move is None:
            continue
        n += 1
        if abs(move) > em_multiple * em:
            breaches += 1
    if n == 0:
        return 0.0, 0
    return breaches / n, n


def _estimate_credit(
    *,
    median_credit_pts: Optional[float],
    em_multiple: float,
    wing_width_pts: float,
    implied_move_pts: float,
) -> Tuple[float, str]:
    """Estimate entry credit for a placement.

    Strategy (in order):
    1. If the caller passed a ``median_credit_pts`` from the live trade
       builder, scale it by ``(reference_em / em_multiple)`` — wider
       placements collect less credit roughly inversely proportional to
       distance past the EM.
    2. Else fall back to the closed-form short-IC approximation:

          credit ≈ 0.5 * em * exp(-0.5 * em_multiple^2) * 2  (both sides)
          capped at wing_width_pts * 0.9

       This is a normal-IV proxy: at 1.0 EM the IC captures ~40% of the
       expected-move premium; at 2.0 EM it captures < 10%.
    """
    if em_multiple <= 0 or wing_width_pts <= 0 or implied_move_pts <= 0:
        return 0.0, "invalid_inputs"

    if median_credit_pts is not None and median_credit_pts > 0:
        # ref EM for the trade builder is 1.0 by convention
        scaled = median_credit_pts * (1.0 / em_multiple)
        capped = min(scaled, wing_width_pts * 0.9)
        return float(max(0.0, capped)), "live_trade_builder_scaled"

    # Normal-IV closed-form proxy
    # Approximate probability density at the short strike distance.
    # Credit ≈ wing_value * Φ(mu_past_short) * 2 * richness, simplified.
    try:
        phi = math.exp(-0.5 * em_multiple * em_multiple) / math.sqrt(2.0 * math.pi)
        credit = 2.0 * implied_move_pts * phi  # both wings
        capped = min(credit, wing_width_pts * 0.9)
        return float(max(0.0, capped)), "normal_iv_proxy"
    except Exception:
        return 0.0, "proxy_failed"


def _confidence_tag(*, n_events: int, mae_n: int, mae_source: str) -> str:
    """High / med / low confidence tag based on sample quality."""
    if n_events >= 15 and mae_n >= 10 and mae_source == "daily_ohlc_proxy":
        return "high"
    if n_events >= 8 and mae_n >= 5:
        return "med"
    return "low"


def _score_placement(
    *,
    em_mult: float,
    wing_pts: float,
    symmetry: str,
    spot: float,
    implied_move_pct: float,
    events: Sequence[Dict[str, Any]],
    mae: Optional[MAEDistribution],
    theta_reading: ThetaCaptureReading,
    weights: WingConsoleWeights,
    median_credit_pts: Optional[float],
) -> PlacementScore:
    """Score one candidate placement on the 5-metric composite."""
    implied_move_pts = spot * (implied_move_pct / 100.0)
    em_pts = implied_move_pts * em_mult

    short_put  = spot - em_pts
    short_call = spot + em_pts
    long_put   = short_put - wing_pts
    long_call  = short_call + wing_pts

    # --- Risk metrics (lower = better) ---
    gap_prob, n_gap = _historic_breach_rate(
        events, em_multiple=em_mult, field_name="signedMovePct"
    )
    ctc_prob, n_ctc = _historic_breach_rate(
        events, em_multiple=em_mult, field_name="ctcSignedMovePct"
    )

    # MAE p95 as a raw % move, converted to "% of wing width"
    if mae and mae.n > 0:
        mae_p95_raw = float(mae.p95)
        mae_pct_of_wing = mae_percentile_to_credit_pct(
            mae_pct_move=mae_p95_raw,
            em_multiple=em_mult,
            implied_move_pct=implied_move_pct,
            wing_width_pts=wing_pts,
            underlying_spot=spot,
        )
        # Convert 0-1.5 ratio into "% of wing" for UI: 1.0 = hit max loss
        mae_p95_pct = mae_pct_of_wing * 100.0
        mae_source = mae.source
        mae_n = mae.n
    else:
        mae_p95_raw = 0.0
        mae_p95_pct = 0.0
        mae_source = "unavailable"
        mae_n = 0

    # --- Reward metrics (higher = better) ---
    theta_out = expected_decay_capture(
        reading=theta_reading, events=events, em_multiple=em_mult,
    )
    theta_pct = float(theta_out["capture_pct"])

    credit_est, credit_src = _estimate_credit(
        median_credit_pts=median_credit_pts,
        em_multiple=em_mult,
        wing_width_pts=wing_pts,
        implied_move_pts=implied_move_pts,
    )

    # --- Composite ---
    target_mae = max(0.01, weights.max_tolerable_mae_pct)
    target_theta = max(0.01, weights.target_theta_pct)
    target_credit_pts = (median_credit_pts or implied_move_pts * 0.10) * weights.target_credit_mult
    target_credit_pts = max(0.05, target_credit_pts)

    parts = {
        "gap":    weights.gap    * (1.0 - gap_prob),
        "ctc":    weights.ctc    * (1.0 - ctc_prob),
        "mae":    weights.mae    * (1.0 - _clamp(mae_p95_pct / target_mae, 0.0, 1.0)),
        "theta":  weights.theta  * _clamp(theta_pct / target_theta, 0.0, 1.0),
        "credit": weights.credit * _clamp(credit_est / target_credit_pts, 0.0, 1.0),
    }

    weight_total = sum(abs(w) for w in [
        weights.gap, weights.ctc, weights.mae, weights.theta, weights.credit,
    ]) or 1.0
    composite = 100.0 * sum(parts.values()) / weight_total

    n_hist = max(n_gap, n_ctc)

    return PlacementScore(
        em_mult=round(float(em_mult), 4),
        wing_pts=round(float(wing_pts), 4),
        symmetry=str(symmetry),
        short_put_strike=round(short_put, 2),
        short_call_strike=round(short_call, 2),
        long_put_strike=round(long_put, 2),
        long_call_strike=round(long_call, 2),
        breach_gap_prob=round(gap_prob, 4),
        breach_ctc_prob=round(ctc_prob, 4),
        mae_p95_pct=round(mae_p95_pct, 2),
        mae_p95_raw_pct=round(mae_p95_raw, 3),
        theta_capture_pct=round(theta_pct, 2),
        credit_est=round(credit_est, 4),
        credit_dollars=round(credit_est * 100.0, 2),
        composite_score=round(composite, 2),
        composite_breakdown={k: round(v, 4) for k, v in parts.items()},
        n_historical=int(n_hist),
        mae_source=str(mae_source),
        confidence=_confidence_tag(n_events=n_hist, mae_n=mae_n, mae_source=mae_source),
        notes=[credit_src] if credit_src else [],
    )


def _clamp(x: float, lo: float, hi: float) -> float:
    if x < lo:
        return lo
    if x > hi:
        return hi
    return x


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def score_placements(
    *,
    ticker: str,
    spot: float,
    implied_move_pct: float,
    events: Sequence[Dict[str, Any]],
    mae:   Optional[MAEDistribution] = None,
    weights: Optional[WingConsoleWeights] = None,
    em_mults: Optional[Sequence[float]] = None,
    wing_pts: Optional[Sequence[float]] = None,
    median_credit_pts: Optional[float] = None,
) -> Tuple[List[PlacementScore], ThetaCaptureReading]:
    """Score the full grid and return ranked placements + theta context.

    Callers that want the full console response should prefer
    :func:`build_wing_console` which wraps this and builds the
    :class:`WingConsolePayload`.
    """
    if spot <= 0 or implied_move_pct <= 0:
        return [], ThetaCaptureReading(n_events=0, notes=["invalid spot/IM"])

    w = weights or DEFAULT_WEIGHTS
    em_list = list(em_mults) if em_mults else [1.0, 1.25, 1.5, 1.75, 2.0]
    wing_list = list(wing_pts) if wing_pts else [5.0, 7.5, 10.0]

    theta_reading = estimate_theta_capture(events)

    placements: List[PlacementScore] = []
    for em in em_list:
        for wp in wing_list:
            placements.append(_score_placement(
                em_mult=float(em),
                wing_pts=float(wp),
                symmetry="symmetric",
                spot=float(spot),
                implied_move_pct=float(implied_move_pct),
                events=events,
                mae=mae,
                theta_reading=theta_reading,
                weights=w,
                median_credit_pts=median_credit_pts,
            ))

    # Sort by composite descending, ties broken by lower breach_gap then higher theta.
    placements.sort(
        key=lambda p: (-p.composite_score, p.breach_gap_prob, -p.theta_capture_pct)
    )
    return placements, theta_reading


def build_wing_console(
    *,
    ticker: str,
    event_date: str,
    event_timing: str,
    payload: Dict[str, Any],
    mae_distribution: Optional[MAEDistribution] = None,
    weights: Optional[WingConsoleWeights] = None,
    em_mults: Optional[Sequence[float]] = None,
    wing_pts: Optional[Sequence[float]] = None,
    flags: Optional[FeatureFlags] = None,
) -> WingConsolePayload:
    """High-level entry point for the router.

    ``payload`` is the response from :func:`compute_breach_stats`. We
    pull ``events``, ``current.stockPrice``, ``current.impliedMovePct``
    (or ``nextEvent.impliedMovePctPlanned``), ``tradeBuilder.totalCredit``,
    and the regime snapshot.
    """
    flags = flags or get_flags()
    w = weights or WingConsoleWeights.from_flags(flags)

    if em_mults is None:
        em_mults = _parse_grid_floats(
            getattr(flags, "E1_WING_EM_MULTS", None),
            fallback=[1.0, 1.25, 1.5, 1.75, 2.0],
        )
    if wing_pts is None:
        wing_pts = _parse_grid_floats(
            getattr(flags, "E1_WING_PTS", None),
            fallback=[5.0, 7.5, 10.0],
        )

    current = payload.get("current") or {}
    next_event = payload.get("nextEvent") or {}
    events = payload.get("events") or []
    trade_builder = payload.get("tradeBuilder") or {}

    spot = _safe_float(current.get("stockPrice"))
    implied_move_pct = (
        _safe_float(current.get("impliedMovePct")) or
        _safe_float(next_event.get("impliedMovePctPlanned"))
    )

    warnings: List[str] = []
    if spot is None or spot <= 0:
        warnings.append("no spot price available; placements suppressed.")
    if implied_move_pct is None or implied_move_pct <= 0:
        warnings.append("no implied move available; placements suppressed.")

    median_credit_pts = _safe_float(trade_builder.get("totalCredit"))

    # Regime from MI v2 (best effort — degrades gracefully).
    regime_label = ""
    regime_prob: Optional[float] = None
    try:
        from backend.market_intel import regime_snapshot
        snap = regime_snapshot()
        if snap is not None:
            regime_label = str(getattr(snap, "label", "")) or ""
            probs = getattr(snap, "probabilities", None) or {}
            if regime_label and isinstance(probs, dict):
                regime_prob = _safe_float(probs.get(regime_label))
    except Exception as err:
        LOG.debug("wing_console: regime_snapshot unavailable (%s)", err)

    placements: List[PlacementScore] = []
    theta_reading = ThetaCaptureReading()
    if (
        spot is not None and spot > 0 and
        implied_move_pct is not None and implied_move_pct > 0
    ):
        placements, theta_reading = score_placements(
            ticker=ticker,
            spot=spot,
            implied_move_pct=implied_move_pct,
            events=events,
            mae=mae_distribution,
            weights=w,
            em_mults=em_mults,
            wing_pts=wing_pts,
            median_credit_pts=median_credit_pts,
        )

    grid_sig = hashlib.sha256(
        json.dumps({
            "em": list(em_mults),
            "wp": list(wing_pts),
        }, sort_keys=True).encode("utf-8")
    ).hexdigest()[:12]

    ck = _cache_key(
        ticker=ticker,
        event_date=event_date,
        event_timing=event_timing,
        weights=w,
        grid_sig=grid_sig,
    )

    return WingConsolePayload(
        ticker=ticker.upper(),
        event_date=event_date,
        event_timing=event_timing.upper(),
        regime_label=regime_label,
        regime_prob=regime_prob,
        spot=spot,
        implied_move_pct=implied_move_pct,
        n_events=sum(1 for e in events if e.get("signedMovePct") is not None),
        placements=placements,
        grid={
            "em_mults": list(em_mults),
            "wing_pts": list(wing_pts),
            "symmetries": ["symmetric"],
        },
        weights_used=w.as_dict(),
        mae=(mae_distribution.to_dict() if mae_distribution else {}),
        theta=theta_reading.to_dict(),
        warnings=warnings,
        generated_at=dt.datetime.now(dt.timezone.utc).replace(microsecond=0, tzinfo=None).isoformat() + "Z",
        cache_key=ck,
    )


# ---------------------------------------------------------------------------
# Router-level cache
# ---------------------------------------------------------------------------


def cached_console(
    *,
    ticker: str,
    event_date: str,
    event_timing: str,
    cache_key: str,
    builder,
) -> WingConsolePayload:
    """Thread-safe (ticker, event_date, event_timing, weights, grid)-keyed cache.

    ``builder`` is a zero-arg callable that produces a
    :class:`WingConsolePayload` on cache miss.
    """
    with _console_cache_lock:
        hit = _console_cache.get(cache_key)
        if hit is not None:
            return hit
    payload = builder()
    with _console_cache_lock:
        _console_cache[cache_key] = payload
    return payload
