"""Scoring context cache for the E2 Wing Console slider endpoint.

Parallel to ``backend.engine1.wing_console.ScoringContext`` + its
``store_scoring_context`` / ``get_scoring_context`` helpers, but keyed
on ``(underlying, entry_day, as_of_date)`` so the slider endpoint can
re-score arbitrary ``(em_mult, wing_pts)`` points against the same
historical + MC + MAE pool the scan built, without re-fetching
anything from ORATS.

Why a separate cache layer (instead of just re-calling
:func:`backend.engine2.wing_console.score_placements` on demand)?

- The scan path computes the weekly_pool + MAE distribution + MC
  results once. The slider path only needs to re-run the composite
  formula against that already-computed context.
- Keeps the slider endpoint sub-200ms regardless of pool size.
- Matches the E1 v2 pattern exactly so the frontend slider code
  mirrors the one we built for ``/api/breach/wing-console/score-placement``.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

from cachetools import TTLCache


@dataclass
class ScoringContext:
    """Snapshot of the inputs :func:`score_single_placement` needs.

    Cached per ``(underlying, entry_day, as_of_date)`` with a 10-minute
    TTL so slider ticks don't re-fetch ORATS or re-run MC.
    """

    underlying:          str = ""
    entry_day:           str = ""
    as_of_date:          str = ""
    spot:                float = 0.0
    em_pct:              float = 0.0                 # 1σ weekly EM in %
    hold_days:           int = 5
    weekly_pool:         List[Dict[str, Any]] = field(default_factory=list)
    mae_dist:            Optional[Dict[str, Any]] = None
    mc_result:           Optional[Dict[str, Any]] = None   # pre-computed MC against the grid
    regime_bucket:       Optional[str] = None
    macro_bucket:        Optional[str] = None
    regime_mi_v2:        Optional[Dict[str, Any]] = None
    weights:             Dict[str, Any] = field(default_factory=dict)
    flags_fp:            Tuple[Any, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------


_scoring_cache: TTLCache = TTLCache(maxsize=2048, ttl=10 * 60)
_scoring_lock = threading.Lock()


def _context_key(underlying: str, entry_day: str, as_of_date: str) -> str:
    return f"{(underlying or '').upper()}|{(entry_day or '').lower()}|{(as_of_date or '')[:10]}"


def store_scoring_context(ctx: ScoringContext) -> None:
    """Publish a :class:`ScoringContext` under its canonical triple key."""
    k = _context_key(ctx.underlying, ctx.entry_day, ctx.as_of_date)
    with _scoring_lock:
        _scoring_cache[k] = ctx


def get_scoring_context(
    underlying: str, entry_day: str, as_of_date: str,
) -> Optional[ScoringContext]:
    """Return the cached context, or ``None`` if expired / never set."""
    k = _context_key(underlying, entry_day, as_of_date)
    with _scoring_lock:
        return _scoring_cache.get(k)


def clear_scoring_cache() -> None:
    """Drop everything (tests only)."""
    with _scoring_lock:
        _scoring_cache.clear()


__all__ = [
    "ScoringContext",
    "clear_scoring_cache",
    "get_scoring_context",
    "store_scoring_context",
]
