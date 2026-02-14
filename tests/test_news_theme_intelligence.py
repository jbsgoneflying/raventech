"""Tests for the Front Layer News Theme Intelligence module.

Covers:
  - Keyword matching
  - Intensity scoring
  - Acceleration computation
  - Persistence counting
  - Theme scoring pipeline
  - Headline extraction helpers
"""

import pytest

from backend.news_theme_intelligence import (
    ThemeReading,
    NewsThemeSnapshot,
    THEME_DEFINITIONS,
    score_themes,
    extract_headlines_from_eodhd,
    extract_headlines_from_benzinga,
    _count_keyword_hits,
    _compute_intensity,
    _compute_acceleration,
    _compute_persistence,
)


# ---------------------------------------------------------------------------
# Dataclass round-trip
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_theme_reading_roundtrip(self):
        tr = ThemeReading(
            theme="AI Displacement", intensity=72.0,
            persistence_days=5, acceleration="rising",
            affected_sectors=["Technology"],
        )
        d = tr.to_dict()
        assert d["theme"] == "AI Displacement"
        tr2 = ThemeReading.from_dict(d)
        assert tr2.intensity == 72.0

    def test_snapshot_roundtrip(self):
        snap = NewsThemeSnapshot(
            date="2026-02-13",
            themes=[{"theme": "AI Displacement", "intensity": 72}],
            dominant_theme="AI Displacement",
            total_headline_count=100,
        )
        d = snap.to_dict()
        assert d["dominant_theme"] == "AI Displacement"

    def test_from_dict_handles_none(self):
        tr = ThemeReading.from_dict(None)
        assert tr.theme == ""


# ---------------------------------------------------------------------------
# Keyword matching
# ---------------------------------------------------------------------------


class TestKeywordMatching:
    def test_exact_match(self):
        headlines = ["AI layoffs continue at major tech firms"]
        keywords = ["ai layoff", "automation"]
        hits, matched = _count_keyword_hits(headlines, keywords)
        assert hits == 1
        assert "ai layoff" in matched

    def test_case_insensitive(self):
        headlines = ["ARTIFICIAL INTELLIGENCE replaces workers"]
        keywords = ["artificial intelligence"]
        hits, _ = _count_keyword_hits(headlines, keywords)
        assert hits == 1

    def test_no_match(self):
        headlines = ["Markets rally on strong earnings"]
        keywords = ["bankruptcy", "default"]
        hits, _ = _count_keyword_hits(headlines, keywords)
        assert hits == 0

    def test_one_hit_per_headline(self):
        """Even if multiple keywords match, count 1 per headline."""
        headlines = ["AI robot automation displaces jobs"]
        keywords = ["ai", "robot", "automation"]
        hits, _ = _count_keyword_hits(headlines, keywords)
        assert hits == 1

    def test_multiple_headlines(self):
        headlines = [
            "AI adoption accelerates",
            "Markets stable today",
            "ChatGPT disrupts another industry",
        ]
        keywords = ["ai adoption", "chatgpt"]
        hits, matched = _count_keyword_hits(headlines, keywords)
        assert hits == 2
        assert len(matched) == 2


# ---------------------------------------------------------------------------
# Intensity scoring
# ---------------------------------------------------------------------------


class TestIntensity:
    def test_zero_hits(self):
        assert _compute_intensity(0, 100) == 0.0

    def test_zero_total(self):
        assert _compute_intensity(5, 0) == 0.0

    def test_moderate_hits(self):
        intensity = _compute_intensity(5, 100)
        assert 0 < intensity < 100

    def test_high_hits(self):
        intensity = _compute_intensity(20, 100)
        assert intensity > 50

    def test_capped_at_100(self):
        intensity = _compute_intensity(50, 50)
        assert intensity <= 100


# ---------------------------------------------------------------------------
# Acceleration
# ---------------------------------------------------------------------------


class TestAcceleration:
    def test_rising(self):
        assert _compute_acceleration(60.0, [30.0, 25.0, 20.0]) == "rising"

    def test_falling(self):
        assert _compute_acceleration(20.0, [50.0, 55.0, 60.0]) == "falling"

    def test_stable(self):
        assert _compute_acceleration(50.0, [48.0, 52.0, 49.0]) == "stable"

    def test_no_history_high_intensity(self):
        assert _compute_acceleration(30.0, []) == "rising"

    def test_no_history_low_intensity(self):
        assert _compute_acceleration(5.0, []) == "stable"


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_continuous(self):
        assert _compute_persistence(50.0, [40.0, 30.0, 20.0]) == 4

    def test_broken(self):
        assert _compute_persistence(50.0, [40.0, 5.0, 30.0]) == 2

    def test_below_threshold(self):
        assert _compute_persistence(5.0, [40.0, 30.0]) == 0

    def test_no_history(self):
        assert _compute_persistence(50.0, []) == 1


# ---------------------------------------------------------------------------
# Full pipeline
# ---------------------------------------------------------------------------


class TestScoreThemes:
    def test_ai_displacement_detected(self):
        headlines = [
            "AI adoption accelerates across Fortune 500",
            "ChatGPT replaces customer service teams",
            "Generative AI disrupts legal sector",
            "Markets rally on strong GDP data",
            "Oil prices stable amid OPEC talks",
        ]
        snap = score_themes(headlines=headlines, date_str="2026-02-13")
        assert snap.total_headline_count == 5
        assert snap.date == "2026-02-13"

        # Find AI Displacement theme
        ai_theme = next((t for t in snap.themes if t["theme"] == "AI Displacement"), None)
        assert ai_theme is not None
        assert ai_theme["intensity"] > 0
        assert ai_theme["keyword_hits"] >= 3

    def test_no_themes_on_neutral_headlines(self):
        headlines = ["Markets stable", "Weather forecast sunny"]
        snap = score_themes(headlines=headlines)
        # All themes should have 0 intensity
        for t in snap.themes:
            assert t["intensity"] == 0

    def test_with_prior_history(self):
        headlines = ["Layoffs continue at tech companies", "Hiring freeze expands"]
        prior = [
            {"themes": [{"theme": "Labor Stress", "intensity": 40.0}]},
            {"themes": [{"theme": "Labor Stress", "intensity": 35.0}]},
        ]
        snap = score_themes(headlines=headlines, prior_snapshots=prior)
        labor = next((t for t in snap.themes if t["theme"] == "Labor Stress"), None)
        assert labor is not None
        assert labor["intensity"] > 0

    def test_dominant_theme(self):
        headlines = [
            "War escalates in region",
            "Military buildup continues",
            "Sanctions imposed on exports",
            "Geopolitical tensions rise",
        ]
        snap = score_themes(headlines=headlines)
        assert snap.dominant_theme == "Geopolitical Escalation"

    def test_all_themes_present(self):
        snap = score_themes(headlines=["test headline"])
        theme_names = {t["theme"] for t in snap.themes}
        expected = {defn["label"] for defn in THEME_DEFINITIONS.values()}
        assert theme_names == expected


# ---------------------------------------------------------------------------
# Headline extraction
# ---------------------------------------------------------------------------


class TestHeadlineExtraction:
    def test_eodhd_extraction(self):
        data = [
            {"title": "Markets rally today"},
            {"title": "Fed holds rates steady"},
            {"title": ""},  # empty
            {"description": "No title field"},
        ]
        headlines = extract_headlines_from_eodhd(data)
        assert len(headlines) == 2

    def test_benzinga_extraction(self):
        data = [
            {"title": "Earnings beat expectations"},
            {"headline": "Oil spikes on supply cut"},
            {"body": "No title"},
        ]
        headlines = extract_headlines_from_benzinga(data)
        assert len(headlines) == 2

    def test_empty_input(self):
        assert extract_headlines_from_eodhd([]) == []
        assert extract_headlines_from_benzinga([]) == []
