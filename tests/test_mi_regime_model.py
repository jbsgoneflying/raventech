"""Unit tests for backend.market_intel.regime_model — the pure-Python HMM."""
from __future__ import annotations

import math
import random

import pytest

from backend.market_intel.regime_model import (
    MODEL_VERSION,
    N_STATES,
    STATE_LABELS,
    CalibratedModel,
    bootstrap_confidence,
    fit_model,
    infer,
    load_model,
    save_model,
    _default_sticky_model,
    _logsumexp,
)


def _synthetic_3regime(n_per: int = 120, sigma: float = 0.5, seed: int = 7):
    """Generate a clean 3-regime synthetic series."""
    rng = random.Random(seed)
    data = []
    for state in range(3):
        mu = (-1.0, 0.0, 1.0)[state]
        for _ in range(n_per):
            data.append([rng.gauss(mu, sigma) for _ in range(8)])
    return data


def test_logsumexp_basics():
    assert _logsumexp([]) == -math.inf
    # log(exp(0) + exp(0)) = log(2)
    assert pytest.approx(_logsumexp([0.0, 0.0]), abs=1e-9) == math.log(2.0)
    # Numerical stability test — large negative values.
    assert math.isfinite(_logsumexp([-1000.0, -1000.0]))


def test_default_model_has_correct_shape():
    m = _default_sticky_model()
    assert m.model_version == MODEL_VERSION
    assert m.n_states == N_STATES
    assert len(m.start_prob) == N_STATES
    assert len(m.trans_mat) == N_STATES
    assert all(len(row) == N_STATES for row in m.trans_mat)
    assert len(m.emission_means) == N_STATES
    # Sticky diagonal — high self-transition.
    for s in range(N_STATES):
        assert m.trans_mat[s][s] > 0.85


def test_fit_recovers_synthetic_3_regimes():
    data = _synthetic_3regime()
    model = fit_model(data, random_state=7)
    # State means should be ordered low → mid → high after re-ordering.
    composites = [sum(row) / len(row) for row in model.emission_means]
    assert composites[0] < composites[1] < composites[2]
    # And approximate the truth (-1, 0, +1) within a sigma.
    assert composites[0] < -0.5
    assert -0.5 < composites[1] < 0.5
    assert composites[2] > 0.5


def test_fit_with_thin_data_returns_default():
    short = [[0.0] * 8 for _ in range(50)]
    model = fit_model(short)
    # < 200 obs → falls through to default sticky model
    assert model.training_days == 50
    assert all(t > 0.85 for t in (model.trans_mat[s][s] for s in range(N_STATES)))


def test_infer_identifies_extreme_states():
    # Use stronger separation to make this test robust across HMM runs.
    data = _synthetic_3regime(n_per=150, sigma=0.4, seed=7)
    model = fit_model(data, random_state=7)
    # Cold vector → Risk-On with majority probability.
    cold = infer(model, [-1.8] * 8)
    assert cold.label == "Risk-On"
    assert cold.probs["risk_on"] > 0.5
    # Hot vector → Stressed with majority probability.
    hot = infer(model, [+1.8] * 8)
    assert hot.label == "Stressed"
    assert hot.probs["stressed"] > 0.5
    # Mid → Transitional should be at least competitive.
    mid = infer(model, [0.0] * 8)
    assert mid.probs["transitional"] >= 0.3


def test_infer_returns_complete_schema():
    data = _synthetic_3regime()
    model = fit_model(data, random_state=7)
    res = infer(model, [0.0] * 8)
    assert set(res.probs.keys()) == {"risk_on", "transitional", "stressed"}
    assert pytest.approx(sum(res.probs.values()), abs=1e-3) == 1.0
    assert res.label in STATE_LABELS
    assert 0.0 <= res.confidence <= 1.0
    assert 0.0 <= res.transition_risk_1d <= 1.0
    assert 0.0 <= res.anomaly_score <= 1.0
    assert len(res.factor_contributions) == 8


def test_transition_risk_smaller_when_already_stressed():
    """When today's posterior is concentrated on Stressed (state 2), the
    'transition-to-more-stressed' risk is bounded by the leftover mass on
    Transitional × P(Trans → Stressed). It should be SMALLER than the
    same metric computed from a Risk-On posture."""
    data = _synthetic_3regime(n_per=150, sigma=0.4, seed=7)
    model = fit_model(data, random_state=7)
    cold_risk = infer(model, [-1.8] * 8).transition_risk_1d
    hot_risk  = infer(model, [+1.8] * 8).transition_risk_1d
    assert cold_risk >= 0.0
    assert hot_risk >= 0.0
    # From risk-on (lots of mass on state 0) we have many transitions to
    # higher states; from stressed (mostly state 2) we have very few.
    assert hot_risk < cold_risk
    assert hot_risk < 0.05


def test_bootstrap_confidence_returns_band():
    data = _synthetic_3regime()
    model = fit_model(data, random_state=7)
    band = bootstrap_confidence(model, [0.5] * 8, n_samples=50, random_state=42)
    assert band.n_samples == 50
    for state in (band.risk_on, band.transitional, band.stressed):
        assert state["p5"] <= state["p50"] <= state["p95"]
        for v in state.values():
            assert 0.0 <= v <= 1.0


def test_bootstrap_deterministic_with_seed():
    data = _synthetic_3regime()
    model = fit_model(data, random_state=7)
    a = bootstrap_confidence(model, [0.5] * 8, n_samples=30, random_state=42)
    b = bootstrap_confidence(model, [0.5] * 8, n_samples=30, random_state=42)
    assert a.to_dict() == b.to_dict()


def test_save_load_round_trip(tmp_path):
    data = _synthetic_3regime()
    model = fit_model(data, random_state=7)
    path = str(tmp_path / "model.json")
    assert save_model(model, path) is True
    loaded = load_model(path)
    assert loaded is not None
    assert loaded.model_version == model.model_version
    assert loaded.n_states == model.n_states
    assert loaded.training_days == model.training_days
    # Inference should match.
    a = infer(model, [0.5] * 8)
    b = infer(loaded, [0.5] * 8)
    assert a.label == b.label
    assert pytest.approx(a.confidence, abs=1e-6) == b.confidence


def test_load_missing_file_returns_none(tmp_path):
    assert load_model(str(tmp_path / "nonexistent.json")) is None


def test_calibrated_model_dict_round_trip():
    m = _default_sticky_model()
    d = m.to_dict()
    m2 = CalibratedModel.from_dict(d)
    assert m2.start_prob == m.start_prob
    assert m2.trans_mat == m.trans_mat
