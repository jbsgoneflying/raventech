from backend.dealer_gamma_context import compute_dealer_gamma_context


def test_dealer_gamma_with_oi_basic_sign_and_bucket():
    rows = [
        {"strike": 100, "spotPrice": 100, "gamma": 0.01, "callOpenInterest": 1000, "putOpenInterest": 100, "callVolume": 0, "putVolume": 0},
        {"strike": 105, "spotPrice": 100, "gamma": 0.02, "callOpenInterest": 100, "putOpenInterest": 900, "callVolume": 0, "putVolume": 0},
    ]
    out = compute_dealer_gamma_context(rows, expiry="2025-12-27", band_pct=0.10, top_n=3)
    assert out["weightingMode"] == "oi"
    assert out["spot"] == 100.0
    assert out["expiry"] == "2025-12-27"
    assert out["netGammaSign"] in ("positive", "negative")
    # Net should reflect calls - puts weighting
    assert out["callsGex"] is not None and out["putsGex"] is not None and out["netGex"] is not None
    # Bucket is deterministic
    assert out["magnitudeBucket"] in ("low", "medium", "high")
    assert isinstance(out["topGammaStrikes"], list)


def test_dealer_gamma_fallback_to_volume_when_oi_missing():
    rows = [
        {"strike": 100, "spotPrice": 100, "gamma": 0.01, "callVolume": 200, "putVolume": 100},
        {"strike": 102, "spotPrice": 100, "gamma": 0.02, "callVolume": 50, "putVolume": 400},
    ]
    out = compute_dealer_gamma_context(rows, expiry="2025-12-27", band_pct=0.10, top_n=2)
    assert out["weightingMode"] == "volume"
    assert any("Open interest unavailable" in w for w in out["warnings"])


def test_dealer_gamma_gamma_only_when_no_weights():
    rows = [
        {"strike": 100, "spotPrice": 100, "gamma": 0.01},
        {"strike": 120, "spotPrice": 100, "gamma": 0.02},  # outside band if 10%
    ]
    out = compute_dealer_gamma_context(rows, expiry="2025-12-27", band_pct=0.10, top_n=5)
    assert out["weightingMode"] == "gamma_only"
    assert out["topGammaStrikes"]  # at least one inside band


def test_dealer_gamma_band_filters_strikes():
    rows = [
        {"strike": 100, "spotPrice": 100, "gamma": 0.01, "callOpenInterest": 10, "putOpenInterest": 10},
        {"strike": 200, "spotPrice": 100, "gamma": 0.50, "callOpenInterest": 10_000, "putOpenInterest": 10_000},
    ]
    out = compute_dealer_gamma_context(rows, expiry="2025-12-27", band_pct=0.05, top_n=10)
    # 200 strike should be excluded; top strikes should not include it
    strikes = [x["strike"] for x in out["topGammaStrikes"]]
    assert 200.0 not in strikes


