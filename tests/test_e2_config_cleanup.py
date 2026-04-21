"""Engine 2 v2 — config flag parity + cleanup of dead flags."""
from __future__ import annotations

import os

import pytest


def test_v2_flags_loadable_from_env(monkeypatch):
    monkeypatch.setenv("ENABLE_E2_V2", "1")
    monkeypatch.setenv("E2_EMIT_DESK_CONSENSUS", "1")
    monkeypatch.setenv("E2_WING_EM_MULTS", "0.8,1.0,1.2")
    monkeypatch.setenv("E2_WING_PTS", "5,15")
    monkeypatch.setenv("E2_MC_N_SIMS", "7777")
    monkeypatch.setenv("E2_WING_SCORE_WEIGHT_CLOSE", "0.5")
    # Force a fresh read of FeatureFlags.
    import backend.config as _cfg
    monkeypatch.setattr(_cfg, "_FLAGS_CACHE", None, raising=False)
    f = _cfg.FeatureFlags.from_env()
    assert f.ENABLE_E2_V2 is True
    assert f.E2_EMIT_DESK_CONSENSUS is True
    assert f.E2_WING_EM_MULTS == "0.8,1.0,1.2"
    assert f.E2_WING_PTS == "5,15"
    assert f.E2_MC_N_SIMS == 7777
    assert f.E2_WING_SCORE_WEIGHT_CLOSE == 0.5


def test_dead_flags_still_defined_for_bc():
    """Legacy flags are documented as dead but must remain in FeatureFlags
    so env deployments that set them don't crash on start."""
    from backend.config import FeatureFlags
    f = FeatureFlags()
    for name in (
        "ENGINE2_ENTRY_DAYS",
        "ENGINE2_EM_MULTS",
        "ENGINE2_LOOKBACK_YEARS_DEFAULT",
        "ENGINE2_MAX_WEEKS_RETURN",
    ):
        assert hasattr(f, name)


def test_v2_default_weights_sum_to_sensible_total():
    from backend.engine2.wing_console import DEFAULT_WEIGHTS
    total = sum([DEFAULT_WEIGHTS.close, DEFAULT_WEIGHTS.touch,
                 DEFAULT_WEIGHTS.mae, DEFAULT_WEIGHTS.theta, DEFAULT_WEIGHTS.credit])
    # Can be renormalised; just assert all five are in [0, 1].
    for w in (DEFAULT_WEIGHTS.close, DEFAULT_WEIGHTS.touch, DEFAULT_WEIGHTS.mae,
              DEFAULT_WEIGHTS.theta, DEFAULT_WEIGHTS.credit):
        assert 0.0 <= w <= 1.0
    assert total > 0.5  # avoid a bad-refactor scenario where all flip to 0
