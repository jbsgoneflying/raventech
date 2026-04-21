"""Quality gates on every Desk Insight v2 catalog.

Ensures:
- every slug has title + spec + related_cards
- spec length is substantive (≥ 300 chars)
- every related_cards entry points at a real (engine, slug) pair
- engine metadata is complete
"""
from __future__ import annotations

import pytest

from backend.desk_insight import (
    get_catalog,
    get_engine_meta,
    supported_card_types,
    supported_engines,
)
from backend.desk_insight.catalogs import union_titles


SPEC_MIN_CHARS = 300


@pytest.mark.parametrize("engine_id", supported_engines())
def test_engine_meta_is_complete(engine_id):
    meta = get_engine_meta(engine_id)
    assert meta, f"engine {engine_id} has no metadata"
    assert meta.get("id") == engine_id
    assert meta.get("name"), f"{engine_id}: no name"
    assert meta.get("description"), f"{engine_id}: no description"
    assert meta.get("asset_class"), f"{engine_id}: no asset_class"


@pytest.mark.parametrize("engine_id", supported_engines())
def test_catalog_entries_are_well_formed(engine_id):
    catalog = get_catalog(engine_id)
    assert catalog, f"engine {engine_id} has no catalog"
    for slug, entry in catalog.items():
        assert isinstance(entry, dict), f"{engine_id}.{slug}: not a dict"
        assert entry.get("title"), f"{engine_id}.{slug}: no title"
        spec = entry.get("spec") or ""
        assert len(spec) >= SPEC_MIN_CHARS, (
            f"{engine_id}.{slug}: spec is only {len(spec)} chars "
            f"(need ≥ {SPEC_MIN_CHARS})"
        )


def _all_slug_pairs():
    titles = union_titles()
    return {(eid, slug) for eid, cards in titles.items() for slug in cards}


@pytest.mark.parametrize("engine_id", supported_engines())
def test_related_cards_resolve(engine_id):
    """Every related_cards cross-link must point at a slug that exists
    somewhere in the Desk Insight registry."""
    catalog = get_catalog(engine_id) or {}
    valid = _all_slug_pairs()
    for slug, entry in catalog.items():
        related = entry.get("related_cards") or []
        assert isinstance(related, list)
        for rc in related:
            assert isinstance(rc, dict), f"{engine_id}.{slug}: related item not dict"
            assert rc.get("engine"), f"{engine_id}.{slug}: related missing engine"
            assert rc.get("slug"), f"{engine_id}.{slug}: related missing slug"
            assert rc.get("label"), f"{engine_id}.{slug}: related missing label"
            key = (rc["engine"], rc["slug"])
            assert key in valid, (
                f"{engine_id}.{slug}: related_cards references unknown "
                f"pair {key!r}; valid engines={sorted(union_titles().keys())}"
            )


def test_catalog_total_card_count_is_substantive():
    """Guard against an accidental empty-catalog deploy."""
    total = sum(len(supported_card_types(e)) for e in supported_engines())
    assert total >= 100, f"expected ≥ 100 cards total; got {total}"


def test_e14_e15_have_all_legacy_slugs():
    """Backwards compat: the slugs that existed in the legacy
    engine14/engine15 card_explain.py must survive in the new catalogs."""
    e14_legacy = {
        "entry_state", "regime_match", "outcome_distribution", "outcome_mid",
        "outcome_adjusted", "modifiers", "mtm_timeline", "position_sizing",
        "greeks_attribution", "exit_optimization", "exit_sensitivity",
        "conditioning_notes", "matched_analogues", "actions", "post_trade_review",
    }
    e14_now = set(supported_card_types("e14"))
    assert e14_legacy.issubset(e14_now), f"E14 lost slugs: {e14_legacy - e14_now}"

    e15_legacy = {
        "e1_summary_strip", "entry_state", "planned_exit_timing",
        "planned_exit_outcome", "adjusted_distribution",
        "conditioning_modifiers", "mtm_timeline", "expected_value",
        "matched_events", "dropped_events", "notes_caveats",
        "actions_panel", "credit_richness", "vrp_crush_verdict",
        "outcome_distribution_empirical", "exit_rules_card",
        "conditioning_summary", "event_analogue_row",
    }
    e15_now = set(supported_card_types("e15"))
    assert e15_legacy.issubset(e15_now), f"E15 lost slugs: {e15_legacy - e15_now}"
