"""Engine 13 — Gap Regime Scanner router.

GET  /api/engine13/scan    — full deterministic scan payload
POST /api/engine13/advisor — LLM desk note (accepts pre-computed scan to skip re-fetch)
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request

LOG = logging.getLogger(__name__)
router = APIRouter()

# ---------------------------------------------------------------------------
# In-memory TTL cache (mirrors Engine 12 pattern)
# ---------------------------------------------------------------------------

_scan_cache: Dict[str, Any] = {}
_scan_lock = threading.Lock()


def _cache_get(key: str, ttl_s: int) -> Optional[Dict[str, Any]]:
    with _scan_lock:
        entry = _scan_cache.get(key)
        if entry is None:
            return None
        ts, data = entry
        if (dt.datetime.utcnow() - ts).total_seconds() > ttl_s:
            _scan_cache.pop(key, None)
            return None
        return data


def _cache_set(key: str, data: Dict[str, Any]) -> None:
    with _scan_lock:
        _scan_cache[key] = (dt.datetime.utcnow(), data)


# ---------------------------------------------------------------------------
# Scan endpoint
# ---------------------------------------------------------------------------

@router.get("/api/engine13/scan")
def engine13_scan(
    request: Request,
    gap_threshold: float = Query(1.5, ge=0.5, le=5.0, description="Minimum gap % to include in analogues"),
):
    """Engine 13: full gap regime scan — gap characterisation, historical
    analogues, options microstructure, technicals, VIX, scenario probabilities."""
    from backend.config import get_flags
    flags = get_flags()
    if not getattr(flags, "ENABLE_ENGINE13_GAP_REGIME", True):
        raise HTTPException(status_code=503, detail="Engine 13 is disabled")

    ttl = int(getattr(flags, "ENGINE13_CACHE_TTL_SCAN", 10 * 60))
    cache_key = f"e13_scan_{dt.date.today().isoformat()}_{gap_threshold}"
    cached = _cache_get(cache_key, ttl)
    if cached is not None:
        return cached

    from backend.deps import get_client_optional, get_benzinga_client_optional
    from backend.price_service import get_price_service
    from backend.eodhd_client import EodhdClient

    orats = get_client_optional()
    benzinga = get_benzinga_client_optional()
    price_svc = get_price_service()
    eodhd = None
    try:
        eodhd = EodhdClient.from_env()
    except Exception:
        pass

    from backend.engine13_gap_regime import compute_gap_regime_scan

    try:
        result = compute_gap_regime_scan(
            orats=orats,
            benzinga=benzinga,
            eodhd=eodhd,
            price_service=price_svc,
            flags=flags,
            gap_threshold_pct=gap_threshold,
        )
        _cache_set(cache_key, result)
        return result
    except HTTPException:
        raise
    except Exception as exc:
        LOG.exception("Engine13 scan failed")
        raise HTTPException(status_code=500, detail=f"Engine 13 scan error: {type(exc).__name__}") from exc


# ---------------------------------------------------------------------------
# Advisor endpoint
# ---------------------------------------------------------------------------

@router.post("/api/engine13/advisor")
async def engine13_advisor(request: Request):
    """Engine 13: LLM desk note — HOLD / ROLL / ADJUST verdict.

    Accepts optional pre-computed scan payload in the request body so we
    skip the expensive re-fetch.  Falls back to a fresh scan if not provided.
    """
    from backend.config import get_flags
    flags = get_flags()
    if not getattr(flags, "ENABLE_ENGINE13_GAP_REGIME", True):
        raise HTTPException(status_code=503, detail="Engine 13 is disabled")

    try:
        body = await request.json()
    except Exception:
        body = {}

    scan_payload = body.get("scanPayload")

    if not isinstance(scan_payload, dict) or not scan_payload.get("gap"):
        from backend.deps import get_client_optional, get_benzinga_client_optional
        from backend.price_service import get_price_service
        from backend.eodhd_client import EodhdClient

        orats = get_client_optional()
        benzinga = get_benzinga_client_optional()
        price_svc = get_price_service()
        eodhd = None
        try:
            eodhd = EodhdClient.from_env()
        except Exception:
            pass

        from backend.engine13_gap_regime import compute_gap_regime_scan
        scan_payload = compute_gap_regime_scan(
            orats=orats,
            benzinga=benzinga,
            eodhd=eodhd,
            price_service=price_svc,
            flags=flags,
        )

    from backend.engine13_advisor import generate_gap_regime_analysis

    try:
        result = generate_gap_regime_analysis(scan_payload, flags=flags)
        return {
            "asOfDate": dt.date.today().isoformat(),
            "advisor": result,
        }
    except Exception as exc:
        LOG.exception("Engine13 advisor failed")
        raise HTTPException(status_code=500, detail=f"Engine 13 advisor error: {type(exc).__name__}") from exc
