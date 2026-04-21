"""Raven Desk Insight v2 — unified tooltip API.

One endpoint for every engine's per-card LLM explanation. Replaces the
fragmented ``/api/ic-scenario/explain-card``, ``/api/earnings-ic/"
``explain-card``, ``/api/front-layer/card-insight``, ``/api/engine9/"
``explain``, ``/api/engine12/explain``, ``/api/engine7-pairs/desk-view``
family.

Public routes:

- ``GET  /api/desk-insight/engines`` — list of canonical engine ids + metadata.
- ``GET  /api/desk-insight/catalog`` — union of ``{engine: {slug: title}}``
  used by the frontend cross-link chip resolver.
- ``GET  /api/desk-insight/catalog/{engine}`` — single-engine detail.
- ``POST /api/desk-insight`` — generate a 9-section desk insight for a card.

Request body (POST):

    {
      "engine":          "e14",
      "cardType":        "outcome_distribution",
      "cardData":        <card-specific JSON slice>,
      "scenarioContext": <optional high-level context>
    }

Feature-flagged: when ``ENABLE_DESK_INSIGHT_V2=0`` the POST returns HTTP
404 so pages can detect the kill-switch and keep using legacy endpoints.
"""
from __future__ import annotations

import logging
import os
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, Header, HTTPException, Query

from backend.config import get_flags
from backend.desk_insight import (
    generate_desk_insight,
    get_catalog,
    get_engine_meta,
    get_stats_snapshot,
    supported_card_types,
    supported_engines,
)
from backend.desk_insight.catalogs import union_titles

LOG = logging.getLogger("desk_insight.router")

router = APIRouter()

# Counter for shim calls — surfaces through /api/desk-insight/stats so the
# desk can watch the deprecation fade out before the legacy routes are
# physically removed in the next release.
_SHIM_CALLS: Dict[str, int] = {}


def _admin_token_ok(supplied: Optional[str]) -> bool:
    expected = os.getenv("DESK_INSIGHT_ADMIN_TOKEN", "").strip()
    if not expected:
        # Fall back to ENGINE15_ADMIN_TOKEN / ENGINE14_ADMIN_TOKEN so the
        # same token the admin already uses for backfills also gates /stats.
        expected = (
            os.getenv("ENGINE15_ADMIN_TOKEN", "").strip()
            or os.getenv("ENGINE14_ADMIN_TOKEN", "").strip()
        )
    if not expected:
        return False
    return bool(supplied) and str(supplied).strip() == expected


def _ensure_enabled() -> None:
    flags = get_flags()
    if not getattr(flags, "ENABLE_DESK_INSIGHT_V2", True):
        raise HTTPException(
            status_code=404,
            detail="Desk Insight v2 disabled (ENABLE_DESK_INSIGHT_V2=0).",
        )


@router.get("/api/desk-insight/engines")
def desk_insight_engines() -> Dict[str, Any]:
    """List all registered engines with their metadata."""
    _ensure_enabled()
    out = []
    for eid in supported_engines():
        meta = get_engine_meta(eid) or {}
        out.append({
            "id":          eid,
            "name":        meta.get("name", eid),
            "description": meta.get("description", ""),
            "asset_class": meta.get("asset_class", ""),
            "card_count":  len(supported_card_types(eid)),
        })
    return {"engines": out}


@router.get("/api/desk-insight/catalog")
def desk_insight_catalog_union(
    engine: Optional[str] = Query(default=None, description="Filter to a single engine id."),
) -> Dict[str, Any]:
    """Return ``{engine: {slug: title}}`` across every registered engine,
    or a single engine if ``?engine=`` is provided."""
    _ensure_enabled()
    if engine:
        eid = engine.strip().lower()
        cat = get_catalog(eid)
        if cat is None:
            raise HTTPException(
                status_code=404,
                detail=f"Unknown engine {engine!r}. Known: {', '.join(supported_engines())}",
            )
        return {
            "engine":    eid,
            "meta":      get_engine_meta(eid),
            "cards":     {s: {"title": v.get("title", s)} for s, v in cat.items()},
        }
    return {"engines": union_titles()}


@router.get("/api/desk-insight/catalog/{engine}")
def desk_insight_catalog_engine(engine: str) -> Dict[str, Any]:
    """Detailed catalog for a single engine — includes specs for debug."""
    _ensure_enabled()
    eid = engine.strip().lower()
    cat = get_catalog(eid)
    if cat is None:
        raise HTTPException(
            status_code=404,
            detail=f"Unknown engine {engine!r}. Known: {', '.join(supported_engines())}",
        )
    return {
        "engine":    eid,
        "meta":      get_engine_meta(eid),
        "cardTypes": supported_card_types(eid),
        "cards":     {
            s: {
                "title":         v.get("title", s),
                "related_cards": v.get("related_cards") or [],
            }
            for s, v in cat.items()
        },
    }


@router.post("/api/desk-insight")
def desk_insight_generate(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Generate a 9-section desk insight for a single card."""
    _ensure_enabled()

    engine = str(body.get("engine") or "").strip().lower()
    if not engine:
        raise HTTPException(status_code=400, detail="'engine' is required.")
    card_type = str(body.get("cardType") or "").strip()
    if not card_type:
        raise HTTPException(status_code=400, detail="'cardType' is required.")

    catalog = get_catalog(engine)
    meta = get_engine_meta(engine)
    if catalog is None or meta is None:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown engine {engine!r}. Known: {', '.join(supported_engines())}",
        )
    if card_type not in catalog:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Unknown cardType {card_type!r} for engine {engine!r}. "
                f"Valid: {', '.join(supported_card_types(engine))}"
            ),
        )

    card_data = body.get("cardData")
    scenario_context = body.get("scenarioContext") or {}

    return generate_desk_insight(
        engine_id=engine,
        card_type=card_type,
        card_data=card_data,
        scenario_context=scenario_context,
        catalog=catalog,
        engine_meta=meta,
    )


# ---------------------------------------------------------------------------
# /api/desk-insight/stats — admin-gated telemetry for content iteration.
# Shows cache hit rate, rate-limit pressure, per-engine / per-card usage
# counts since last process restart. Requires DESK_INSIGHT_ADMIN_TOKEN
# (or ENGINE15_ADMIN_TOKEN / ENGINE14_ADMIN_TOKEN fallback) in the
# X-Admin-Token header.
# ---------------------------------------------------------------------------


@router.get("/api/desk-insight/stats")
def desk_insight_stats(
    x_admin_token: Optional[str] = Header(default=None, alias="X-Admin-Token"),
    top_n: int = Query(default=20, ge=1, le=200, description="Top-N cards to list by requests_total."),
) -> Dict[str, Any]:
    """Admin-gated telemetry. In-process counters only (not multi-worker aggregated)."""
    _ensure_enabled()
    if not _admin_token_ok(x_admin_token):
        raise HTTPException(
            status_code=401,
            detail="Invalid or missing X-Admin-Token "
                   "(DESK_INSIGHT_ADMIN_TOKEN / ENGINE15_ADMIN_TOKEN / ENGINE14_ADMIN_TOKEN).",
        )

    snap = get_stats_snapshot()
    # Shape for easy consumption: rank engines + cards by requests_total.
    by_engine_list = [
        {"engine": e, **stats}
        for e, stats in snap.get("by_engine", {}).items()
    ]
    by_engine_list.sort(
        key=lambda r: r.get("requests_total", 0), reverse=True,
    )
    by_card_list = [
        {"card": k, "engine": k.split(":", 1)[0], "slug": k.split(":", 1)[1] if ":" in k else k, **stats}
        for k, stats in snap.get("by_card", {}).items()
    ]
    by_card_list.sort(
        key=lambda r: r.get("requests_total", 0), reverse=True,
    )
    return {
        "totals": {
            "requests_total":       snap["requests_total"],
            "cache_hits":           snap["cache_hits"],
            "llm_calls":            snap["llm_calls"],
            "fallback_calls":       snap["fallback_calls"],
            "rate_limited":         snap["rate_limited"],
            "llm_errors":           snap["llm_errors"],
            "parse_errors":         snap["parse_errors"],
            "missing_field_errors": snap["missing_field_errors"],
        },
        "rates": {
            "cache_hit_rate":  round(snap["cache_hit_rate"],  4),
            "llm_rate":        round(snap["llm_rate"],        4),
            "fallback_rate":   round(snap["fallback_rate"],   4),
            "rate_limit_rate": round(snap["rate_limit_rate"], 4),
        },
        "uptime_seconds":     snap["uptime_seconds"],
        "started_at_utc":     snap["started_at_utc"],
        "last_request_utc":   snap["last_request_utc"],
        "top_engines":        by_engine_list,
        "top_cards":          by_card_list[:top_n],
        "legacy_shim_calls":  dict(_SHIM_CALLS),
    }


# ---------------------------------------------------------------------------
# Backwards-compat shims — route legacy endpoints through the new pipeline.
# Scheduled for removal one release cycle after all pages have migrated.
# Every call emits a deprecation warning + increments a shim counter so the
# desk can watch usage fade to zero before the routes are physically deleted.
# ---------------------------------------------------------------------------


def _shim_note(route: str) -> None:
    """Log a deprecation warning + bump the per-route shim counter."""
    LOG.warning(
        "Desk Insight legacy shim called: %s — clients should POST /api/desk-insight instead.",
        route,
    )
    _SHIM_CALLS[route] = _SHIM_CALLS.get(route, 0) + 1


def _shim_generate(engine: str, body: Dict[str, Any]) -> Dict[str, Any]:
    card_type = str(body.get("cardType") or "").strip()
    if not card_type:
        raise HTTPException(status_code=400, detail="cardType is required.")
    catalog = get_catalog(engine)
    meta = get_engine_meta(engine)
    if catalog is None or meta is None:
        raise HTTPException(
            status_code=503,
            detail=f"Desk Insight catalog for {engine!r} is not loaded.",
        )
    if card_type not in catalog:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown cardType {card_type!r} for engine {engine!r}.",
        )
    return generate_desk_insight(
        engine_id=engine,
        card_type=card_type,
        card_data=body.get("cardData"),
        scenario_context=body.get("scenarioContext") or {},
        catalog=catalog,
        engine_meta=meta,
    )


@router.post("/api/ic-scenario/explain-card")
def shim_e14_explain_card(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Legacy E14 explain-card — routes through desk_insight v2. DEPRECATED: use POST /api/desk-insight with engine=e14."""
    _ensure_enabled()
    _shim_note("POST /api/ic-scenario/explain-card")
    return _shim_generate("e14", body)


@router.get("/api/ic-scenario/explain-card/catalog")
def shim_e14_explain_catalog() -> Dict[str, Any]:
    """DEPRECATED: use GET /api/desk-insight/catalog?engine=e14."""
    _ensure_enabled()
    _shim_note("GET /api/ic-scenario/explain-card/catalog")
    cat = get_catalog("e14") or {}
    return {
        "cardTypes": sorted(cat.keys()),
        "titles":    {s: v.get("title", s) for s, v in cat.items()},
    }


@router.post("/api/earnings-ic/explain-card")
def shim_e15_explain_card(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Legacy E15 explain-card — routes through desk_insight v2. DEPRECATED: use POST /api/desk-insight with engine=e15."""
    _ensure_enabled()
    _shim_note("POST /api/earnings-ic/explain-card")
    return _shim_generate("e15", body)


@router.get("/api/earnings-ic/explain-card/catalog")
def shim_e15_explain_catalog() -> Dict[str, Any]:
    """DEPRECATED: use GET /api/desk-insight/catalog?engine=e15."""
    _ensure_enabled()
    _shim_note("GET /api/earnings-ic/explain-card/catalog")
    cat = get_catalog("e15") or {}
    return {
        "cardTypes": sorted(cat.keys()),
        "titles":    {s: v.get("title", s) for s, v in cat.items()},
    }
