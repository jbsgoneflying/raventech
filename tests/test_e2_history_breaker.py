from backend.e2_history_breaker import compute_e2_history_breaker_risk


def _base_payload():
    return {
        "current": {"regime": {"bucket": "MODERATE"}},
        "recommendation": {"label": "Trade"},
        "oddsLikeNow": {"byWidth": [{"breachPct": 12.0}, {"breachPct": 15.0}]},
        "weeks": [{"signedMovePct": 0.9}] * 52,
    }


def test_history_breaker_low_when_stable():
    risk = compute_e2_history_breaker_risk(_base_payload())
    assert risk["level"] == "low"
    assert risk["gate"] == "OK"


def test_history_breaker_high_when_regime_and_recency_hot():
    payload = _base_payload()
    payload["current"]["regime"]["bucket"] = "NO_TRADE"
    payload["recommendation"]["label"] = "Avoid"
    payload["oddsLikeNow"]["byWidth"] = [{"breachPct": 28.0}]
    payload["weeks"] = ([{"signedMovePct": 0.5}] * 40) + ([{"signedMovePct": 1.4}] * 12)
    risk = compute_e2_history_breaker_risk(payload)
    assert risk["level"] == "high"
    assert risk["gate"] == "NO_TRADE"
    assert risk["overrideFavorableStats"] is True
