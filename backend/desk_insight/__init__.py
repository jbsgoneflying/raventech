"""Raven Desk Insight v2 — unified LLM tooltip pipeline.

One module, one schema, one popup. Every card across Market Intelligence
and Engines 1-15 speaks the same nine-section "senior quant explainer"
language. Catalogs live under :mod:`backend.desk_insight.catalogs`; the
generator lives in :mod:`backend.desk_insight.core`.

Public API:

- :func:`generate_desk_insight` — LLM-grounded, 9-section tooltip for a card.
- :func:`get_catalog` / :func:`get_engine_meta` — catalog lookup helpers.
- :func:`supported_engines` / :func:`supported_card_types` — discovery.
- :data:`OUTPUT_KEYS`, :data:`OUTPUT_LABELS` — the canonical schema.
"""
from __future__ import annotations

from backend.desk_insight.core import (
    OUTPUT_KEYS,
    OUTPUT_LABELS,
    generate_desk_insight,
)
from backend.desk_insight.catalogs import (
    get_catalog,
    get_engine_meta,
    resolve_related_label,
    supported_card_types,
    supported_engines,
)

__all__ = [
    "OUTPUT_KEYS",
    "OUTPUT_LABELS",
    "generate_desk_insight",
    "get_catalog",
    "get_engine_meta",
    "resolve_related_label",
    "supported_card_types",
    "supported_engines",
]
