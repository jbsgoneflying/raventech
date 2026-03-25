"""Tests for Engine 2 AI Trade Advisor — trades CRUD, tracking, and advisor module."""

import datetime as dt
import json
from unittest.mock import MagicMock, patch

import pytest

from backend.config import FeatureFlags
from backend.engine2_advisor import (
    _extract_dms_context,
    _parse_llm_json,
    _sanitize_e2_for_llm,
    compute_news_gate_score,
    compute_trade_tracking,
    generate_checkin_analysis,
    generate_trade_analysis,
)
from backend.engine2_trades import (
    _TRADE_INDEX_KEY,
    _TRADE_KEY_PREFIX,
    add_checkin,
    close_trade,
    get_trade,
    list_active_trades,
    log_trade,
)
from backend.news_theme_intelligence import (
    compute_market_adjusted_intensity,
    get_theme_impact_weight,
)


# ---------------------------------------------------------------------------
# Fake Redis store for trade tests
# ---------------------------------------------------------------------------

class FakeStore:
    def __init__(self):
        self._data = {}

    def set_json(self, key, value, ttl_s=None):
        self._data[key] = json.loads(json.dumps(value, default=str))
        return True

    def get_json(self, key):
        return self._data.get(key)


# ---------------------------------------------------------------------------
# Trade CRUD tests
# ---------------------------------------------------------------------------

class TestTradeCRUD:
    def _make_flags(self):
        return FeatureFlags(
            ENGINE2_TRADE_TTL_S=3600,
            ENGINE2_TRADE_MAX_INDEX=10,
            ENGINE2_ADVISOR_ENABLED=True,
        )

    def test_log_trade_returns_id(self):
        store = FakeStore()
        flags = self._make_flags()
        trade_data = {
            "source": "advisor",
            "entry": {"underlying": "SPX", "shortPutStrike": 6300, "shortCallStrike": 6600, "wingWidth": 10},
            "entryContext": {"regimeScore": 45, "regimeBucket": "MODERATE"},
        }
        tid = log_trade(trade_data, store=store, flags=flags)
        assert tid is not None
        assert tid.startswith("e2-")
        assert "SPX" in tid

    def test_log_trade_persists_to_store(self):
        store = FakeStore()
        flags = self._make_flags()
        trade_data = {
            "entry": {"underlying": "SPX", "shortPutStrike": 6300},
        }
        tid = log_trade(trade_data, store=store, flags=flags)
        assert store.get_json(f"{_TRADE_KEY_PREFIX}{tid}") is not None
        index = store.get_json(_TRADE_INDEX_KEY) or []
        assert tid in index

    def test_list_active_trades(self):
        store = FakeStore()
        flags = self._make_flags()
        log_trade({"entry": {"underlying": "SPX"}}, store=store, flags=flags)
        log_trade({"entry": {"underlying": "SPX"}}, store=store, flags=flags)
        trades = list_active_trades(store=store)
        assert len(trades) == 2
        assert all(t["status"] == "active" for t in trades)

    def test_close_trade(self):
        store = FakeStore()
        flags = self._make_flags()
        tid = log_trade({"entry": {"underlying": "SPX"}}, store=store, flags=flags)
        result = close_trade(tid, close_data={"reason": "target_hit"}, store=store, flags=flags)
        assert result is not None
        assert result["status"] == "closed"
        assert result["closeReason"] == "target_hit"
        active = list_active_trades(store=store)
        assert len(active) == 0

    def test_add_checkin(self):
        store = FakeStore()
        flags = self._make_flags()
        tid = log_trade({"entry": {"underlying": "SPX"}}, store=store, flags=flags)
        checkin = {"status": "on_track", "headline": "All good"}
        result = add_checkin(tid, checkin, store=store, flags=flags)
        assert result is not None
        assert len(result["checkIns"]) == 1
        assert result["checkIns"][0]["status"] == "on_track"

    def test_checkin_adjust_sets_monitoring(self):
        store = FakeStore()
        flags = self._make_flags()
        tid = log_trade({"entry": {"underlying": "SPX"}}, store=store, flags=flags)
        add_checkin(tid, {"status": "adjust"}, store=store, flags=flags)
        trade = get_trade(tid, store=store)
        assert trade["status"] == "monitoring"

    def test_log_trade_no_store_returns_none(self):
        tid = log_trade({"entry": {}}, store=None)
        assert tid is None

    def test_index_capped(self):
        store = FakeStore()
        flags = FeatureFlags(ENGINE2_TRADE_TTL_S=3600, ENGINE2_TRADE_MAX_INDEX=3, ENGINE2_ADVISOR_ENABLED=True)
        for _ in range(5):
            log_trade({"entry": {"underlying": "SPX"}}, store=store, flags=flags)
        index = store.get_json(_TRADE_INDEX_KEY)
        assert len(index) == 3


# ---------------------------------------------------------------------------
# Trade tracking tests
# ---------------------------------------------------------------------------

class TestTradeTracking:
    def _make_trade(self, sp=6300, sc=6600, spot_entry=6450, wing=10,
                    entry_date="2026-03-24", expiry_date="2026-03-28"):
        return {
            "entry": {
                "underlying": "SPX",
                "shortPutStrike": sp,
                "shortCallStrike": sc,
                "spotAtEntry": spot_entry,
                "wingWidth": wing,
                "entryDate": entry_date,
                "expiryDate": expiry_date,
            },
            "entryContext": {
                "regimeScore": 40,
                "regimeBucket": "MODERATE",
                "volPressureState": "NEUTRAL",
            },
        }

    def test_on_track(self):
        trade = self._make_trade()
        tracking = compute_trade_tracking(trade, current_spot=6450)
        assert tracking["deterministicStatus"] == "on_track"
        assert tracking["distPutPts"] > 0
        assert tracking["distCallPts"] > 0

    def test_caution_when_approaching_strike(self):
        trade = self._make_trade(sp=6300, sc=6600, spot_entry=6450)
        # Spot at 6360 -- 60 pts from short put (was 150 away at entry)
        # Proximity = (1 - 60/150) * 100 = 60%
        tracking = compute_trade_tracking(trade, current_spot=6360)
        assert tracking["deterministicStatus"] == "caution"

    def test_adjust_when_very_close(self):
        trade = self._make_trade(sp=6300, sc=6600, spot_entry=6450)
        # Spot at 6320 -- 20 pts from short put (was 150 away)
        # Proximity = (1 - 20/150) * 100 = 86.7%
        tracking = compute_trade_tracking(trade, current_spot=6320)
        assert tracking["deterministicStatus"] == "adjust"

    def test_exit_when_breached(self):
        trade = self._make_trade(sp=6300, sc=6600, spot_entry=6450)
        tracking = compute_trade_tracking(trade, current_spot=6290)
        assert tracking["deterministicStatus"] == "exit"

    def test_regime_drift_detection(self):
        trade = self._make_trade()
        tracking = compute_trade_tracking(
            trade,
            current_spot=6450,
            current_regime={"score": 70, "bucket": "ELEVATED"},
        )
        assert tracking["regimeDriftScore"] == 30.0
        assert tracking["regimeDriftBucket"] == "ELEVATED"

    def test_vol_shift_detection(self):
        trade = self._make_trade()
        tracking = compute_trade_tracking(
            trade,
            current_spot=6450,
            current_vol_pressure="BID",
        )
        assert tracking["volShift"] == "NEUTRAL -> BID"


# ---------------------------------------------------------------------------
# Advisor module unit tests
# ---------------------------------------------------------------------------

class TestParseJson:
    def test_plain_json(self):
        assert _parse_llm_json('{"a": 1}') == {"a": 1}

    def test_markdown_fences(self):
        text = '```json\n{"verdict": "TRADE"}\n```'
        assert _parse_llm_json(text) == {"verdict": "TRADE"}

    def test_preamble_text(self):
        text = 'Here is the result:\n{"verdict": "PASS"}'
        assert _parse_llm_json(text) == {"verdict": "PASS"}

    def test_returns_none_on_garbage(self):
        assert _parse_llm_json("not json at all") is None


class TestSanitize:
    def test_sanitize_extracts_key_fields(self):
        payload = {
            "asOfDate": "2026-03-25",
            "params": {"entryDay": "mon"},
            "underlying": {"symbol": "SPX"},
            "current": {"regime": {"score": 45}},
            "expectedMove": {"enabled": True},
            "strikeTargets": {},
            "oddsLikeNow": {},
            "recommendation": {"recommended": None},
            "recSimple": {"width": 10},
            "liveContext": {"volPressure": {"state": "NEUTRAL"}},
            "technicals": {"rsi": {"value": 55}},
            "telemetry": {"should": "be excluded"},
        }
        sanitized = _sanitize_e2_for_llm(payload)
        assert "asOfDate" in sanitized
        assert "telemetry" not in sanitized
        assert sanitized.get("liveContextSummary", {}).get("volPressure") == {"state": "NEUTRAL"}

    def test_dms_context_extraction(self):
        dms = {
            "regime": {"label": "Transitional"},
            "vol_state": {"level": 22},
            "cross_asset_stress": {"composite_score": 0.5, "composite_label": "elevated"},
            "news_themes": [
                {"theme": "Tariffs", "key": "geopolitical_escalation", "intensity": 40, "acceleration": 5},
                {"theme": "Minor", "key": "ai_displacement", "intensity": 5, "acceleration": 0},
            ],
        }
        ctx = _extract_dms_context(dms)
        assert ctx["composite_stress"] == 0.5
        assert len(ctx["active_themes"]) == 1
        t = ctx["active_themes"][0]
        assert t["theme"] == "Tariffs"
        assert t["adjustedIntensity"] == 40.0
        assert t["spxImpactWeight"] == 1.0

    def test_dms_context_adjusted_intensity_applied(self):
        dms = {
            "news_themes": [
                {"theme": "AI Displacement", "key": "ai_displacement", "intensity": 86.7, "acceleration": "rising"},
                {"theme": "Geopolitical Escalation", "key": "geopolitical_escalation", "intensity": 50, "acceleration": "stable"},
            ],
        }
        ctx = _extract_dms_context(dms)
        themes = ctx["active_themes"]
        assert len(themes) == 2
        ai = next(t for t in themes if t["key"] == "ai_displacement")
        geo = next(t for t in themes if t["key"] == "geopolitical_escalation")
        assert ai["adjustedIntensity"] == pytest.approx(21.7, abs=0.1)
        assert ai["spxImpactWeight"] == 0.25
        assert geo["adjustedIntensity"] == pytest.approx(50.0)
        assert geo["spxImpactWeight"] == 1.0

    def test_dms_context_includes_news_gate(self):
        dms = {
            "news_themes": [
                {"theme": "Geopolitical Escalation", "key": "geopolitical_escalation", "intensity": 70, "acceleration": "rising"},
            ],
        }
        ctx = _extract_dms_context(dms)
        assert "newsGate" in ctx
        assert ctx["newsGate"]["gate"] == "elevated"
        assert ctx["newsGate"]["maxAdjustedIntensity"] == 70.0


class TestAdvisorFallback:
    def test_disabled_returns_fallback(self):
        flags = FeatureFlags(ENGINE2_ADVISOR_ENABLED=False)
        result = generate_trade_analysis({}, flags=flags)
        assert result["_source"] == "fallback"
        assert result["verdict"] == "PASS"

    def test_checkin_disabled_returns_fallback(self):
        flags = FeatureFlags(ENGINE2_ADVISOR_ENABLED=False)
        result = generate_checkin_analysis({}, {"deterministicStatus": "on_track"}, flags=flags)
        assert result["_source"] == "fallback"
        assert result["status"] == "on_track"


# ---------------------------------------------------------------------------
# Multi-wing grid (config test)
# ---------------------------------------------------------------------------

class TestMultiWingConfig:
    def test_multi_wing_flag_default_true(self):
        flags = FeatureFlags()
        assert flags.ENGINE2_MULTI_WING is True

    def test_wing_pts_config_parsed(self):
        flags = FeatureFlags(ENGINE2_WING_WIDTH_PTS="5,10,15,20,25")
        parts = [p.strip() for p in flags.ENGINE2_WING_WIDTH_PTS.split(",")]
        assert len(parts) == 5

    def test_cache_key_includes_multi_wing(self):
        f1 = FeatureFlags(ENGINE2_MULTI_WING=True)
        f2 = FeatureFlags(ENGINE2_MULTI_WING=False)
        assert f1.cache_key_engine2() != f2.cache_key_engine2()


# ---------------------------------------------------------------------------
# Theme impact weighting tests
# ---------------------------------------------------------------------------

class TestThemeImpactWeighting:
    def test_get_weight_by_key(self):
        assert get_theme_impact_weight("geopolitical_escalation") == 1.0
        assert get_theme_impact_weight("ai_displacement") == 0.25
        assert get_theme_impact_weight("liquidity_shock") == 0.95

    def test_get_weight_by_label(self):
        assert get_theme_impact_weight("Geopolitical Escalation") == 1.0
        assert get_theme_impact_weight("AI Displacement") == 0.25
        assert get_theme_impact_weight("Credit Stress") == 0.80

    def test_get_weight_unknown_returns_default(self):
        assert get_theme_impact_weight("Unknown Theme") == 0.5
        assert get_theme_impact_weight("") == 0.5

    def test_adjusted_intensity_ai_discounted(self):
        adj = compute_market_adjusted_intensity(86.7, "ai_displacement")
        assert adj == pytest.approx(21.7, abs=0.1)

    def test_adjusted_intensity_geo_full_weight(self):
        adj = compute_market_adjusted_intensity(50.0, "geopolitical_escalation")
        assert adj == pytest.approx(50.0)

    def test_adjusted_intensity_by_label(self):
        adj = compute_market_adjusted_intensity(60.0, "Labor Stress")
        assert adj == pytest.approx(30.0)

    def test_adjusted_intensity_zero(self):
        assert compute_market_adjusted_intensity(0.0, "geopolitical_escalation") == 0.0


# ---------------------------------------------------------------------------
# News gate scoring tests
# ---------------------------------------------------------------------------

class TestNewsGateScore:
    def test_empty_themes_ok(self):
        result = compute_news_gate_score([])
        assert result["gate"] == "ok"
        assert result["maxAdjustedIntensity"] == 0
        assert result["dominantTheme"] is None

    def test_low_adjusted_ok(self):
        themes = [{"theme": "AI Displacement", "adjustedIntensity": 21.7}]
        result = compute_news_gate_score(themes)
        assert result["gate"] == "ok"

    def test_caution_threshold(self):
        themes = [{"theme": "Labor Stress", "adjustedIntensity": 35.0}]
        result = compute_news_gate_score(themes)
        assert result["gate"] == "caution"
        assert result["dominantTheme"] == "Labor Stress"

    def test_elevated_threshold(self):
        themes = [{"theme": "Credit Stress", "adjustedIntensity": 64.0}]
        result = compute_news_gate_score(themes)
        assert result["gate"] == "elevated"

    def test_block_threshold(self):
        themes = [{"theme": "Geopolitical Escalation", "adjustedIntensity": 85.0}]
        result = compute_news_gate_score(themes)
        assert result["gate"] == "block"
        assert result["dominantTheme"] == "Geopolitical Escalation"

    def test_dominant_is_highest_adjusted(self):
        themes = [
            {"theme": "AI Displacement", "adjustedIntensity": 21.7},
            {"theme": "Geopolitical Escalation", "adjustedIntensity": 50.0},
        ]
        result = compute_news_gate_score(themes)
        assert result["dominantTheme"] == "Geopolitical Escalation"
        assert result["maxAdjustedIntensity"] == 50.0
        assert result["gate"] == "caution"
        assert result["themeCount"] == 2
