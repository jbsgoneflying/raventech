"""Unit tests for backend.market_intel.regime_service."""
from __future__ import annotations

import datetime as dt

import pytest

from backend.market_intel import (
    canonical_vol_state,
    clear_cache,
    regime_snapshot,
    service_health,
)
from backend.market_intel.factors import OK, FactorReading, FactorSnapshot


def setup_function(_fn):
    clear_cache()


def test_service_health_returns_metadata():
    h = service_health()
    assert h["model_version"] == "mi_hmm_v1"
    assert h["model_source"] in ("default", "disk", "redis", "memo")
    assert "feature_keys" in h
    assert len(h["feature_keys"]) == 8
    assert "state_labels" in h
    assert h["state_labels"] == ["Risk-On", "Transitional", "Stressed"]


def test_regime_snapshot_offline_returns_legacy_fallback():
    """With no clients, all factors are MISSING — service should return
    the legacy_fallback path with a synthesized probs vector."""
    snap = regime_snapshot(force_refresh=True)
    assert snap.source == "legacy_fallback"
    assert snap.label in ("Risk-On", "Transitional", "Risk-Off", "Stressed")
    assert sum(snap.probs.values()) == pytest.approx(1.0, abs=1e-3)
    assert snap.data_quality["insufficient"] is True
    # All 8 factors should be in MISSING.
    assert len(snap.data_quality["missing"]) == 8


def test_canonical_vol_state_with_factor_snapshot():
    snap = FactorSnapshot(as_of="2026-04-21")
    snap.readings["vix_term_slope"] = FactorReading(
        key="vix_term_slope", quality=OK, z=1.5, value=2.0,
    )
    snap.readings["rv_spx_20d"] = FactorReading(
        key="rv_spx_20d", quality=OK, z=0.5, value=18.0,
    )
    snap.readings["dealer_gamma"] = FactorReading(
        key="dealer_gamma", quality=OK, z=1.5, value=1.5,
    )
    vs = canonical_vol_state(factor_snap=snap)
    assert vs["term_structure"] == "backwardation"  # slope z > 0.5
    assert vs["level"] == 18.0  # from rv_spx_20d
    assert vs["skew"] == "elevated"  # dealer gamma z > 1.0


def test_canonical_vol_state_legacy_fallback():
    vs = canonical_vol_state(engine5_vol_direction="rising")
    assert vs["term_structure"] == "backwardation"
    vs = canonical_vol_state(engine5_vol_direction="compressing")
    assert vs["term_structure"] == "contango"


def test_regime_snapshot_caches_for_5_minutes():
    a = regime_snapshot()
    b = regime_snapshot()
    # Same as_of date → cache hit, should be the same object.
    assert a is b
    # force_refresh bypasses.
    c = regime_snapshot(force_refresh=True)
    assert c is not a or a.generated_at == c.generated_at


def test_regime_snapshot_with_engine5_label_pass_through():
    """When MI v2 is in fallback mode, it copies the E5 label."""
    e5 = {"data": {"regime": {"label": "Stressed", "score": 82.0}}}
    snap = regime_snapshot(engine5_snapshot=e5, force_refresh=True)
    if snap.source == "legacy_fallback":
        assert snap.label == "Stressed"
        # And probs concentrate on stressed.
        assert snap.probs["stressed"] > 0.6
