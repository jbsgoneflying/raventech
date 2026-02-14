"""Tests for the Front Layer Cross-Asset Stress Module.

Covers:
  - AssetStressReading construction and serialization
  - Stress score computation per asset class
  - Direction classification
  - Equity relationship (confirming/diverging)
  - CrossAssetStressSnapshot aggregation
  - BTC/ETH ratio helper
"""

import pytest

from backend.cross_asset_stress import (
    AssetStressReading,
    CrossAssetStressSnapshot,
    compute_asset_stress,
    build_cross_asset_snapshot,
    compute_btc_eth_ratio,
    _safe_pct_change,
    _direction_from_change,
    _compute_equity_relationship,
    CROSS_ASSET_UNIVERSE,
)


# ---------------------------------------------------------------------------
# Dataclass round-trip
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_reading_roundtrip(self):
        r = AssetStressReading(
            symbol="VIX.INDX", name="VIX Spot", asset_class="volatility",
            direction="up", stress_score=75.0, change_vs_prior=2.5,
            equity_relationship="confirming",
        )
        d = r.to_dict()
        assert d["stress_score"] == 75.0
        r2 = AssetStressReading.from_dict(d)
        assert r2.symbol == "VIX.INDX"

    def test_snapshot_roundtrip(self):
        snap = CrossAssetStressSnapshot(
            timestamp="2026-02-13T08:00:00Z",
            readings=[{"symbol": "test"}],
            composite_score=62.0,
            composite_label="Risk-Off",
        )
        d = snap.to_dict()
        assert d["composite_label"] == "Risk-Off"

    def test_from_dict_handles_none(self):
        r = AssetStressReading.from_dict(None)
        assert r.symbol == ""


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


class TestMathHelpers:
    def test_pct_change_normal(self):
        assert _safe_pct_change(105.0, 100.0) == 5.0

    def test_pct_change_zero_prior(self):
        assert _safe_pct_change(100.0, 0.0) == 0.0

    def test_direction_up(self):
        assert _direction_from_change(1.0) == "up"

    def test_direction_down(self):
        assert _direction_from_change(-1.0) == "down"

    def test_direction_flat(self):
        assert _direction_from_change(0.05) == "flat"


# ---------------------------------------------------------------------------
# Stress scoring
# ---------------------------------------------------------------------------


class TestStressScoring:
    def test_vix_up_is_stress(self):
        """VIX going up should produce stress score above 50."""
        r = compute_asset_stress(
            symbol_key="VIX",
            current_close=25.0,
            prior_close=20.0,
            equity_return_1d=-1.5,
        )
        assert r.stress_score > 50
        assert r.direction == "up"
        assert r.asset_class == "volatility"

    def test_vix_down_is_calm(self):
        """VIX going down should produce stress score at or below 50."""
        r = compute_asset_stress(
            symbol_key="VIX",
            current_close=15.0,
            prior_close=20.0,
            equity_return_1d=1.0,
        )
        assert r.stress_score <= 50

    def test_gold_up_is_stress(self):
        """Gold going up (safe haven bid) is stress."""
        r = compute_asset_stress(
            symbol_key="GOLD",
            current_close=200.0,
            prior_close=190.0,
        )
        assert r.stress_score > 50
        assert r.direction == "up"

    def test_btc_down_is_stress(self):
        """BTC down = risk-off stress."""
        r = compute_asset_stress(
            symbol_key="BTC",
            current_close=50000.0,
            prior_close=55000.0,
        )
        assert r.stress_score > 50
        assert r.direction == "down"

    def test_copper_down_is_stress(self):
        """Copper down = demand stress."""
        r = compute_asset_stress(
            symbol_key="COPPER",
            current_close=20.0,
            prior_close=22.0,
        )
        assert r.stress_score > 50

    def test_unknown_symbol_returns_neutral(self):
        r = compute_asset_stress(
            symbol_key="UNKNOWN",
            current_close=100.0,
            prior_close=100.0,
        )
        assert r.stress_score == 50.0

    def test_history_changes_score(self):
        """Providing history should influence the stress score."""
        # Use a small move (0.5%) so directional score doesn't saturate to 100
        history = [100.0 + i * 0.1 for i in range(30)]
        r1 = compute_asset_stress(
            symbol_key="DXY", current_close=100.5, prior_close=100.0,
        )
        r2 = compute_asset_stress(
            symbol_key="DXY", current_close=100.5, prior_close=100.0,
            history_closes=history,
        )
        # Scores should differ due to percentile blending
        assert r1.stress_score != r2.stress_score


# ---------------------------------------------------------------------------
# Equity relationship
# ---------------------------------------------------------------------------


class TestEquityRelationship:
    def test_confirming_vix_up_equity_down(self):
        rel = _compute_equity_relationship(2.0, -1.0, "positive")
        assert rel == "confirming"

    def test_diverging_vix_up_equity_up(self):
        rel = _compute_equity_relationship(2.0, 1.0, "positive")
        assert rel == "diverging"

    def test_neutral_small_moves(self):
        rel = _compute_equity_relationship(0.05, 0.03, "positive")
        assert rel == "neutral"

    def test_variable_direction_neutral(self):
        rel = _compute_equity_relationship(2.0, -1.0, "variable")
        assert rel == "neutral"


# ---------------------------------------------------------------------------
# Snapshot aggregation
# ---------------------------------------------------------------------------


class TestSnapshotAggregation:
    def test_build_empty(self):
        snap = build_cross_asset_snapshot(readings=[], timestamp="2026-02-13")
        assert snap.composite_score == 50.0
        assert snap.composite_label == "Neutral"

    def test_build_stressed(self):
        readings = [
            AssetStressReading(asset_class="fx", stress_score=80.0),
            AssetStressReading(asset_class="commodity", stress_score=75.0),
            AssetStressReading(asset_class="volatility", stress_score=85.0),
        ]
        snap = build_cross_asset_snapshot(readings=readings)
        assert snap.composite_score > 70
        assert snap.composite_label in ("Stressed", "Risk-Off")

    def test_build_calm(self):
        readings = [
            AssetStressReading(asset_class="fx", stress_score=20.0),
            AssetStressReading(asset_class="commodity", stress_score=25.0),
            AssetStressReading(asset_class="volatility", stress_score=15.0),
            AssetStressReading(asset_class="crypto", stress_score=30.0),
        ]
        snap = build_cross_asset_snapshot(readings=readings)
        assert snap.composite_score < 35
        assert snap.composite_label == "Risk-On"


# ---------------------------------------------------------------------------
# BTC/ETH ratio
# ---------------------------------------------------------------------------


class TestBtcEthRatio:
    def test_normal_ratio(self):
        assert compute_btc_eth_ratio(50000.0, 3000.0) == round(50000.0 / 3000.0, 4)

    def test_zero_eth(self):
        assert compute_btc_eth_ratio(50000.0, 0.0) is None

    def test_negative_eth(self):
        assert compute_btc_eth_ratio(50000.0, -100.0) is None


# ---------------------------------------------------------------------------
# Universe coverage
# ---------------------------------------------------------------------------


class TestUniverse:
    def test_all_expected_keys_present(self):
        expected = {"DXY", "USDJPY", "USDCHF", "EMFX", "OIL", "COPPER", "GOLD", "SILVER", "BTC", "ETH", "VIX"}
        assert expected.issubset(set(CROSS_ASSET_UNIVERSE.keys()))

    def test_all_have_required_fields(self):
        for key, meta in CROSS_ASSET_UNIVERSE.items():
            assert "symbol" in meta, f"{key} missing symbol"
            assert "asset_class" in meta, f"{key} missing asset_class"
            assert "stress_direction" in meta, f"{key} missing stress_direction"
