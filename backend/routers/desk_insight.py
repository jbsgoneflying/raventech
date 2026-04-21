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
from typing import Any, Dict, Optional

from fastapi import APIRouter, Body, HTTPException, Query

from backend.config import get_flags
from backend.desk_insight import (
    generate_desk_insight,
    get_catalog,
    get_engine_meta,
    supported_card_types,
    supported_engines,
)
from backend.desk_insight.catalogs import union_titles

LOG = logging.getLogger("desk_insight.router")

router = APIRouter()


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
# Backwards-compat shims — route legacy endpoints through the new pipeline.
# Scheduled for removal one release cycle after all pages have migrated.
# ---------------------------------------------------------------------------


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
    """Legacy E14 explain-card — routes through desk_insight v2."""
    _ensure_enabled()
    return _shim_generate("e14", body)


@router.get("/api/ic-scenario/explain-card/catalog")
def shim_e14_explain_catalog() -> Dict[str, Any]:
    _ensure_enabled()
    cat = get_catalog("e14") or {}
    return {
        "cardTypes": sorted(cat.keys()),
        "titles":    {s: v.get("title", s) for s, v in cat.items()},
    }


@router.post("/api/earnings-ic/explain-card")
def shim_e15_explain_card(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    """Legacy E15 explain-card — routes through desk_insight v2."""
    _ensure_enabled()
    return _shim_generate("e15", body)


@router.get("/api/earnings-ic/explain-card/catalog")
def shim_e15_explain_catalog() -> Dict[str, Any]:
    _ensure_enabled()
    cat = get_catalog("e15") or {}
    return {
        "cardTypes": sorted(cat.keys()),
        "titles":    {s: v.get("title", s) for s, v in cat.items()},
    }
