"""Engine 2 v2 — MI v2 regime overlay on scan + tracker."""
from __future__ import annotations

from dataclasses import replace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


class _FakeSnap:
    def __init__(self, label="Transitional"):
        self.label = label
        self.probabilities = {"Risk-On": 0.2, "Transitional": 0.7, "Stressed": 0.1}
        self.vol_state = "expanding"
        self.source = "v2_hmm"


def test_regime_resolver_prefers_mi_v2(monkeypatch):
    from backend.routers.engine2_spx_ic import _current_regime_for_tracker
    from backend.config import get_flags

    f = replace(get_flags(), ENABLE_MI_V2=True)

    with patch("backend.market_intel.regime_snapshot", return_value=_FakeSnap("Risk-On")):
        regime, vol, source = _current_regime_for_tracker(store=None, flags=f)
    assert source == "mi_v2"
    assert regime["bucket"] == "Risk-On"
    assert regime["source"] == "mi_v2"
    assert 0.0 <= regime["score"] <= 100.0
    assert vol == "expanding"


def test_regime_resolver_falls_back_to_engine5_when_mi_v2_disabled(monkeypatch):
    from backend.routers.engine2_spx_ic import _current_regime_for_tracker
    from backend.config import get_flags

    f = replace(get_flags(), ENABLE_MI_V2=False)

    def fake_select(store, *, max_age_days, snapshot_ttl):
        return {"data": {"regime": {"label": "Elevated", "score": 55, "vol_pressure_state": "expanding"}}}

    with patch("backend.engine5_snapshot.select_best_snapshot", side_effect=fake_select):
        regime, vol, source = _current_regime_for_tracker(store="dummy", flags=f)
    assert source == "engine5"
    assert regime["bucket"] == "Elevated"
    assert regime["source"] == "engine5"
    assert vol == "expanding"


def test_regime_resolver_returns_unavailable_when_both_paths_miss(monkeypatch):
    from backend.routers.engine2_spx_ic import _current_regime_for_tracker
    from backend.config import get_flags

    f = replace(get_flags(), ENABLE_MI_V2=False)

    def fake_select(store, *, max_age_days, snapshot_ttl):
        return None

    with patch("backend.engine5_snapshot.select_best_snapshot", side_effect=fake_select):
        regime, vol, source = _current_regime_for_tracker(store=None, flags=f)
    assert source == "unavailable"
    assert regime is None
