import math

from backend.oi_clusters import compute_open_interest_clusters


def _mk_row(strike, spot=100.0, call_oi=0, put_oi=0):
    return {
        "strike": strike,
        "spotPrice": spot,
        "gamma": 0.001,
        "callOpenInterest": call_oi,
        "putOpenInterest": put_oi,
    }


def test_oi_clusters_finds_walls_and_clusters_deterministically():
    # Two call peaks near 105 and 108, but within cluster window they should merge
    rows = [
        _mk_row(95, call_oi=10, put_oi=500),   # put wall region
        _mk_row(96, call_oi=5, put_oi=450),
        _mk_row(97, call_oi=5, put_oi=100),
        _mk_row(104, call_oi=200, put_oi=10),  # call wall region
        _mk_row(105, call_oi=300, put_oi=10),
        _mk_row(106, call_oi=150, put_oi=10),
        _mk_row(108, call_oi=280, put_oi=5),
        _mk_row(109, call_oi=50, put_oi=5),
    ]
    out = compute_open_interest_clusters(rows, expiry="2026-01-02", band_pct=0.20, top_n=3, cluster_steps=2)
    assert out["expiry"] == "2026-01-02"
    assert out["weightingMode"] == "oi"
    assert math.isclose(out["spot"], 100.0)

    # Walls exist
    assert out["putWall"] is not None
    assert out["callWall"] is not None

    # Put wall should point to the max OI strike in its top cluster (95)
    assert out["putWall"]["peakStrike"] == 95.0
    assert out["putWall"]["peakOI"] == 500.0

    # Call wall should be centered around the 104-109 region, max strike should be 105
    assert out["callWall"]["peakStrike"] == 105.0
    assert out["callWall"]["peakOI"] == 300.0
    assert out["callWall"]["minStrike"] <= 105.0 <= out["callWall"]["maxStrike"]


def test_oi_clusters_missing_spot_returns_empty():
    out = compute_open_interest_clusters([{"strike": 100, "callOpenInterest": 10, "putOpenInterest": 10}], expiry="2026-01-02")
    assert out["spot"] is None
    assert out["callClusters"] == []
    assert out["putClusters"] == []


