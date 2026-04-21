"""UI parity: every `data-insight="slug"` on a page must resolve to a
real slug in the matching engine's catalog.

Guards against the classic drift where a divider points at a slug the
backend doesn't know, causing a 400 when the user clicks the 'i' button.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Dict, List, Set, Tuple

import pytest

from backend.desk_insight import get_catalog, supported_card_types
from backend.desk_insight.catalogs import union_titles


REPO_ROOT = Path(__file__).resolve().parent.parent
STATIC_DIR = REPO_ROOT / "static"

# Map each page HTML to the engine its data-insight slugs should resolve
# against. Entries are ("html_filename", "engine_id"). Pages missing from
# this map are ignored (they're expected not to have data-insight markers).
PAGE_TO_ENGINE: Dict[str, str] = {
    "index.html":              "e1",
    "spx.html":                "e2",
    "red-dog.html":            "e3",
    "ichimoku.html":           "e4",
    "engine5.html":            "e5",
    "pairs.html":              "e7",
    "post-event.html":         "e8",
    "engine9.html":            "e9",
    "news-risk.html":          "e11",
    "vix-fade.html":           "e12",
    "gap-regime.html":         "e13",
    "ic-scenario.html":        "e14",
    "earnings-ic.html":        "e15",
    "calendar.html":           "calendar",
    "compare.html":            "compare",
    "market-intelligence.html": "market-intel",
}


DATA_INSIGHT_RX = re.compile(r'data-insight="([^"]+)"')


def _extract_slugs(html_path: Path) -> List[str]:
    text = html_path.read_text(encoding="utf-8")
    return DATA_INSIGHT_RX.findall(text)


def _all_valid_pairs() -> Set[Tuple[str, str]]:
    titles = union_titles()
    return {(eng, slug) for eng, cards in titles.items() for slug in cards}


@pytest.mark.parametrize(
    "page,engine",
    sorted(PAGE_TO_ENGINE.items()),
    ids=[f"{p}->{e}" for p, e in sorted(PAGE_TO_ENGINE.items())],
)
def test_page_data_insight_slugs_resolve(page: str, engine: str):
    """Every data-insight slug on the page must exist in its engine's catalog."""
    html_path = STATIC_DIR / page
    assert html_path.exists(), f"missing HTML file {html_path}"
    slugs = _extract_slugs(html_path)
    if not slugs:
        # Not every page has to have markers yet; parity test just ensures
        # any markers present ARE valid. No markers = nothing to validate.
        pytest.skip(f"{page} has no data-insight markers")
    catalog = set(supported_card_types(engine))
    valid_pairs = _all_valid_pairs()
    for slug in slugs:
        # Allow the slug to live in the page's engine OR in ANY engine
        # (cross-engine refs are legal — desk-insight.js auto-routes them).
        assert slug in catalog or any(
            (eng, slug) in valid_pairs for eng in union_titles()
        ), (
            f"{page}: data-insight=\"{slug}\" does not resolve in "
            f"engine {engine!r} catalog (nor any other engine). "
            f"Valid {engine} slugs: {sorted(catalog)[:8]}..."
        )


def test_every_page_in_registry_has_html():
    """Guard against typos in PAGE_TO_ENGINE above."""
    for page in PAGE_TO_ENGINE:
        assert (STATIC_DIR / page).exists(), f"missing {page}"


def test_at_least_seven_engine_pages_have_markers():
    """After this follow-up PR, the bulk of engine pages should carry markers.
    Regression guard: if a refactor strips all markers, this trips."""
    pages_with_markers = 0
    for page in PAGE_TO_ENGINE:
        slugs = _extract_slugs(STATIC_DIR / page)
        if slugs:
            pages_with_markers += 1
    assert pages_with_markers >= 12, (
        f"expected >=12 pages with data-insight markers; got {pages_with_markers}. "
        "Did a refactor strip them?"
    )


def test_desk_insight_css_is_loaded_on_every_marked_page():
    """Any page that uses data-insight MUST load desk-insight.css — otherwise
    the `i` button is unstyled and misaligned."""
    for page, _engine in PAGE_TO_ENGINE.items():
        text = (STATIC_DIR / page).read_text(encoding="utf-8")
        if 'data-insight="' not in text:
            continue
        assert "desk-insight.css" in text, (
            f"{page} uses data-insight but doesn't load desk-insight.css"
        )
        assert "desk-insight.js" in text, (
            f"{page} uses data-insight but doesn't load desk-insight.js"
        )
