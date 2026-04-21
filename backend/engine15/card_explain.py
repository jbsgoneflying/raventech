"""Legacy thin shim for Engine 15 per-card LLM tooltips.

The full catalog and LLM pipeline now live under
:mod:`backend.desk_insight`. This module kills the old thread-unsafe
monkey-patch of Engine 14's catalog; it just forwards to the new API.

Prefer the new API:

    from backend.desk_insight import (
        generate_desk_insight,
        get_catalog,
        get_engine_meta,
    )
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.desk_insight import (
    generate_desk_insight,
    get_catalog,
    get_engine_meta,
)
from backend.desk_insight.catalogs import supported_card_types as _supported

__all__ = [
    "CARD_CATALOG",
    "supported_card_types",
    "generate_card_explanation",
]


def _catalog() -> Dict[str, Dict[str, Any]]:
    return get_catalog("e15") or {}


CARD_CATALOG: Dict[str, Dict[str, Any]] = _catalog()


def supported_card_types() -> List[str]:
    return _supported("e15")


def generate_card_explanation(
    *,
    card_type: str,
    card_data: Any,
    scenario_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Backwards-compat wrapper. Routes through desk_insight v2."""
    return generate_desk_insight(
        engine_id="e15",
        card_type=card_type,
        card_data=card_data,
        scenario_context=scenario_context or {},
        catalog=_catalog(),
        engine_meta=get_engine_meta("e15") or {},
    )
