from backend.e1_history_breaker import compute_history_breaker_risk


def _base_events():
    return [
        {"earnDate": "2026-01-30", "breach": False},
        {"earnDate": "2025-10-31", "breach": False},
        {"earnDate": "2025-07-31", "breach": False},
        {"earnDate": "2025-04-30", "breach": False},
        {"earnDate": "2025-01-31", "breach": False},
        {"earnDate": "2024-10-31", "breach": True},
    ]


def test_history_breaker_low_when_signals_are_calm():
    out = compute_history_breaker_risk(
        summary={"breach_rate_pct": 10.0, "events_used": 12, "breaches": 1},
        events=_base_events(),
        regime={"guidance": {"tradeGate": "OK"}},
        regime_validation={"eventsUsed": 12, "breaches": 1, "breachesMissed": 0, "breachRateByGatePct": {"OK": 8.0, "NO_TRADE": 20.0}},
        stability={"tasSignAgreementPct": 86.0, "confidenceDerived": "HIGH"},
        gap_vs_ctc={"gap": {"1.0": 10.0}, "ctc": {"1.0": 14.0}},
        event_risk={"enabled": True, "score01": 0.30, "label": "LOW"},
        quarters={"Q2": {"recommendation": "Standard"}},
        current_quarter_key="Q2",
    )
    assert out["level"] == "low"
    assert out["gate"] == "OK"
    assert out["overrideFavorableStats"] is False


def test_history_breaker_elevated_on_recency_and_stability():
    events = _base_events()
    events[:4] = [
        {"earnDate": "2026-01-30", "breach": True},
        {"earnDate": "2025-10-31", "breach": True},
        {"earnDate": "2025-07-31", "breach": False},
        {"earnDate": "2025-04-30", "breach": True},
    ]
    out = compute_history_breaker_risk(
        summary={"breach_rate_pct": 18.0, "events_used": 16, "breaches": 3},
        events=events,
        regime={"guidance": {"tradeGate": "CAUTION"}},
        regime_validation={"eventsUsed": 16, "breaches": 4, "breachesMissed": 2, "breachRateByGatePct": {"OK": 28.0, "NO_TRADE": 12.0}},
        stability={"tasSignAgreementPct": 72.0, "confidenceDerived": "MED"},
        gap_vs_ctc={"gap": {"1.0": 16.0}, "ctc": {"1.0": 30.0}},
        event_risk={"enabled": True, "score01": 0.55, "label": "MEDIUM"},
        quarters={"Q2": {"recommendation": "Standard"}},
        current_quarter_key="Q2",
    )
    assert out["level"] in ("elevated", "high")
    assert out["gate"] in ("CAUTION", "NO_TRADE")
    assert out["overrideFavorableStats"] is True
    assert len(out["drivers"]) >= 1


def test_history_breaker_high_when_multiple_divergence_signals_stack():
    events = [
        {"earnDate": "2026-01-30", "breach": True},
        {"earnDate": "2025-10-31", "breach": True},
        {"earnDate": "2025-07-31", "breach": True},
        {"earnDate": "2025-04-30", "breach": True},
        {"earnDate": "2025-01-31", "breach": False},
        {"earnDate": "2024-10-31", "breach": False},
    ]
    out = compute_history_breaker_risk(
        summary={"breach_rate_pct": 22.0, "events_used": 20, "breaches": 4},
        events=events,
        regime={"guidance": {"tradeGate": "NO_TRADE"}},
        regime_validation={"eventsUsed": 20, "breaches": 6, "breachesMissed": 4, "breachRateByGatePct": {"OK": 35.0, "NO_TRADE": 14.0}},
        stability={"tasSignAgreementPct": 58.0, "confidenceDerived": "LOW"},
        gap_vs_ctc={"gap": {"1.0": 15.0}, "ctc": {"1.0": 33.0}},
        event_risk={"enabled": True, "score01": 0.80, "label": "HIGH"},
        quarters={"Q2": {"recommendation": "Avoid"}},
        current_quarter_key="Q2",
    )
    assert out["level"] == "high"
    assert out["gate"] == "NO_TRADE"
    assert out["overrideFavorableStats"] is True
    assert out["score"] >= 70.0

