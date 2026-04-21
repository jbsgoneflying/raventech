"""Command Deck cache — dedupes scan + wing-console round-trips.

Parallel to :mod:`backend.engine1.shared_cache`. A single SPX IC scan
happens in two places:

1. ``GET /api/spx-ic``            — primary scan + render pipeline.
2. ``POST /api/spx-ic/wing-console`` — ranked-placement console that
   reuses the same historical pool / MAE / MC.

Both routes read from the same :class:`TTLCache` keyed on
``(underlying, entry_day, as_of_date, weights_fp, flags_fp)`` so the
Command Deck pays ORATS cost once per (underlying, entry_day) per
trading day.

5-minute TTL balances:

- Long enough to span scan → wing-console → advisor round-trips
  that typically happen in under a minute.
- Short enough that intraday EM drift / macro calendar refreshes
  serve a new pool within the same desk session.
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
from typing import Any, Callable, Dict, Optional, Tuple

from cachetools import TTLCache

LOG = logging.getLogger("engine2.shared_cache")


# ---------------------------------------------------------------------------
# Cache + stats
# ---------------------------------------------------------------------------


_CMD_DECK_CACHE: TTLCache = TTLCache(maxsize=512, ttl=5 * 60)
_CMD_DECK_LOCK = threading.Lock()

_STATS: Dict[str, int] = {"hits": 0, "misses": 0, "stores": 0, "busts": 0}
_STATS_LOCK = threading.Lock()


def _stats_bump(kind: str) -> None:
    with _STATS_LOCK:
        _STATS[kind] = int(_STATS.get(kind, 0)) + 1


def get_stats_snapshot() -> Dict[str, int]:
    """Return a copy of the hit/miss counters."""
    with _STATS_LOCK:
        return dict(_STATS)


def reset_stats() -> None:
    """Zero the counters (tests)."""
    with _STATS_LOCK:
        for k in list(_STATS.keys()):
            _STATS[k] = 0


def clear() -> None:
    """Drop all cached Command Decks (tests + admin)."""
    with _CMD_DECK_LOCK:
        _CMD_DECK_CACHE.clear()
    _stats_bump("busts")


def _fingerprint(obj: Any) -> str:
    try:
        blob = json.dumps(obj, sort_keys=True, separators=(",", ":"), default=str)
    except Exception:
        blob = repr(obj)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:12]


def _build_key(
    *,
    underlying:  str,
    entry_day:   str,
    as_of_date:  str,
    weights:     Optional[Dict[str, Any]] = None,
    extra:       Optional[Dict[str, Any]] = None,
    flags_fp:    Tuple[Any, ...] = (),
) -> str:
    wf = _fingerprint(weights or {})
    xf = _fingerprint(extra or {})
    ff = _fingerprint(list(flags_fp) or [])
    return f"{(underlying or '').upper()}|{(entry_day or '').lower()}|{(as_of_date or '')[:10]}|{wf}|{xf}|{ff}"


def get_or_compute_command_deck(
    *,
    underlying:   str,
    entry_day:    str,
    as_of_date:   str,
    weights:      Optional[Dict[str, Any]] = None,
    extra:        Optional[Dict[str, Any]] = None,
    flags_fp:     Tuple[Any, ...] = (),
    compute:      Callable[[], Dict[str, Any]],
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Cache-first accessor for the full Command Deck payload.

    ``compute`` is the zero-arg callable that builds the payload on
    cache miss. Cache key includes the weights + flags fingerprint so
    desk-tunable weights don't collide across tabs.
    """
    key = _build_key(
        underlying=underlying, entry_day=entry_day, as_of_date=as_of_date,
        weights=weights, extra=extra, flags_fp=flags_fp,
    )
    if not force_refresh:
        with _CMD_DECK_LOCK:
            hit = _CMD_DECK_CACHE.get(key)
        if hit is not None:
            _stats_bump("hits")
            return hit

    _stats_bump("misses")
    payload = compute()
    with _CMD_DECK_LOCK:
        _CMD_DECK_CACHE[key] = payload
    _stats_bump("stores")
    return payload


__all__ = [
    "clear",
    "get_or_compute_command_deck",
    "get_stats_snapshot",
    "reset_stats",
]
