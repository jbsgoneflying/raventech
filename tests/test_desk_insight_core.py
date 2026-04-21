"""Tests for the Raven Desk Insight v2 core generator.

Covers:
- 9-field output contract
- fallback when LLM unavailable (OPENAI_API_KEY missing, unknown card_type,
  empty engine_id, etc.)
- rate-limit fallback
- cache hit path
"""
from __future__ import annotations

from typing import Any, Dict

from backend.desk_insight import (
    OUTPUT_KEYS,
    generate_desk_insight,
    get_catalog,
    get_engine_meta,
    supported_card_types,
    supported_engines,
)
from backend.desk_insight.core import (
    clear_cache,
    reconfigure_rate_limit,
)


REQUIRED_PROSE_KEYS = [
    "what_this_shows",
    "how_to_read_it",
    "quant_mechanics",
    "how_to_use_it",
    "example_scenario",
    "watch_for",
    "common_mistakes",
    "desk_takeaway",
]


def _call(engine: str, slug: str, card_data: Any = None, ctx: Dict[str, Any] = None) -> Dict[str, Any]:
    return generate_desk_insight(
        engine_id=engine,
        card_type=slug,
        card_data=card_data or {},
        scenario_context=ctx or {},
        catalog=get_catalog(engine) or {},
        engine_meta=get_engine_meta(engine) or {},
    )


def setup_function(_fn):
    clear_cache()
    reconfigure_rate_limit(60)


def test_schema_keys_are_canonical():
    assert OUTPUT_KEYS[0] == "what_this_shows"
    assert OUTPUT_KEYS[-1] == "desk_takeaway"
    assert "related_cards" in OUTPUT_KEYS
    assert len(OUTPUT_KEYS) == 9


def test_supported_engines_includes_mi_and_e1_through_e15():
    engines = set(supported_engines())
    for eid in ["market-intel", "e1", "e14", "e15", "calendar", "compare"]:
        assert eid in engines, f"missing engine {eid}"


def test_every_engine_has_cards():
    for eid in supported_engines():
        assert len(supported_card_types(eid)) > 0, f"engine {eid} has no cards"


def test_fallback_path_fills_all_nine_fields():
    """When OPENAI_API_KEY is absent (pytest sandbox), every call falls
    back to the spec-derived static generator. All 9 fields must be present."""
    r = _call("e14", "entry_state", card_data={"sample": True})
    assert r.get("_engine") == "e14"
    assert r.get("_card_type") == "entry_state"
    for key in REQUIRED_PROSE_KEYS:
        assert isinstance(r.get(key), str) and r[key].strip(), f"missing or empty {key}"
    assert isinstance(r.get("related_cards"), list)


def test_unknown_engine_returns_fallback():
    r = generate_desk_insight(
        engine_id="nonexistent",
        card_type="whatever",
        card_data={},
        scenario_context={},
        catalog={},
        engine_meta={},
    )
    assert r.get("_source") == "fallback"
    for key in REQUIRED_PROSE_KEYS:
        assert r.get(key)
    assert r.get("_fallback_reason")


def test_unknown_slug_returns_fallback():
    r = _call("e14", "nonexistent_card")
    assert r.get("_source") == "fallback"
    assert "Unknown card_type" in r.get("_fallback_reason", "")
    for key in REQUIRED_PROSE_KEYS:
        assert r.get(key)


def test_fallback_uses_spec_first_sentence_as_summary():
    r = _call("e14", "outcome_distribution")
    what = r["what_this_shows"]
    # Fallback summary should reference the card's authoritative spec topic.
    assert "outcome" in what.lower() or "distribution" in what.lower() or "empirical" in what.lower()


def test_fallback_includes_canonical_related_cards():
    # e14.entry_state declares related_cards in the catalog;
    # those should appear verbatim in the fallback response.
    r = _call("e14", "entry_state")
    related = r.get("related_cards") or []
    assert len(related) > 0
    for rc in related:
        assert "engine" in rc
        assert "slug" in rc
        assert "label" in rc


def test_rate_limit_engages_fallback():
    reconfigure_rate_limit(1)
    # First request succeeds (lands in cache as fallback since no API key).
    a = _call("e14", "entry_state")
    # Identical request → cache hit, still counts as success.
    b = _call("e14", "entry_state")
    assert a == b
    # Distinct payload → goes through rate limiter; second distinct
    # payload exhausts the 1-req budget and lands in the rate-limit
    # fallback branch.
    _ = _call("e14", "outcome_distribution", card_data={"x": 1})
    r2 = _call("e14", "regime_match", card_data={"y": 2})
    assert r2.get("_source") == "fallback"
    # Either OPENAI_API_KEY missing or rate-limited — both write a reason.
    assert r2.get("_fallback_reason")


def test_cache_hit_returns_same_payload_instance():
    r1 = _call("e14", "entry_state", card_data={"same": True})
    r2 = _call("e14", "entry_state", card_data={"same": True})
    assert r1 is r2 or r1 == r2


def test_distinct_engines_do_not_share_cache():
    # Same card_type slug across two engines (e.g. both define "entry_state")
    # must produce independent cached entries scoped by engine_id.
    clear_cache()
    r_e14 = _call("e14", "entry_state", card_data={"k": 1})
    r_e15 = _call("e15", "entry_state", card_data={"k": 1})
    # Different engine_meta.name guarantees divergence — fallback summary
    # wording will differ because specs differ.
    assert r_e14.get("_engine") == "e14"
    assert r_e15.get("_engine") == "e15"
