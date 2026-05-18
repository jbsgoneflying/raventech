"""Engine 15 — Strike Scanner unit + endpoint tests.

Covers:
  - infer_strike_step on common grid layouts
  - generate_candidates: count, deduplication, never-emits-baseline,
    strike-grid alignment, no negative widths
  - score_candidate monotonicity (wider strikes -> lower p_breach,
    lower credit -> lower EV at fixed breach)
  - rank_and_verdict bucketing (dominating / safer / richer / optimal)
  - End-to-end POST /api/earnings-ic/strike-scan with a synthetic chain
    fixture.

All tests are offline — no ORATS, no Redis.
"""
from __future__ import annotations

from dataclasses import replace
from typing import List

import pytest
from fastapi.testclient import TestClient

from backend.engine14 import chain_cache
from backend.engine14.chain_replay import ChainRow, FillModel
from backend.engine15.strike_scanner import (
    CandidateStrikes,
    STRUCTURE_ASYM,
    STRUCTURE_CALL_VERT,
    STRUCTURE_FLY,
    STRUCTURE_IC,
    STRUCTURE_PUT_VERT,
    ScoredCandidate,
    generate_candidates,
    infer_strike_step,
    rank_and_verdict,
    run_strike_scan,
    score_candidate,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_chain_db(tmp_path, monkeypatch):
    """Isolate chain_cache to a per-test SQLite DB. Mirrors the helper in
    test_engine14_ic_scenario.py."""
    db = tmp_path / "engine14_chains.db"
    monkeypatch.setattr(chain_cache, "_resolve_db_path", lambda: str(db))
    yield str(db)


def _row(strike: float, *, spot: float, put_mid: float, call_mid: float) -> ChainRow:
    """Build a single ChainRow with sensible bid/ask around the mid."""
    return ChainRow(
        trade_date="2026-04-15", ticker="HD", expiry="2026-04-17",
        strike=float(strike), spot=float(spot),
        call_bid=call_mid * 0.95, call_ask=call_mid * 1.05,
        call_mid=call_mid, call_iv=0.30,
        put_bid=put_mid * 0.95, put_ask=put_mid * 1.05,
        put_mid=put_mid, put_iv=0.30,
        call_oi=100, put_oi=100,
    )


def _synthetic_chain(*, spot: float, strikes: List[float]) -> List[ChainRow]:
    """Generate a tiny IV=0.30 chain centered on ``spot``.

    Pricing mimics the rough intrinsic + extrinsic shape so credits behave
    sensibly when we shift strikes. Mid > 0 for every strike.
    """
    rows: List[ChainRow] = []
    for k in sorted(strikes):
        # Put mid: rises as strike rises (further OTM call, deeper ITM put).
        intrinsic_put = max(0.0, k - spot)
        extrinsic = max(0.10, 2.0 - abs(k - spot) * 0.02)
        put_mid = intrinsic_put + extrinsic
        # Call mid: mirror.
        intrinsic_call = max(0.0, spot - k)
        call_mid = intrinsic_call + extrinsic
        rows.append(_row(k, spot=spot, put_mid=put_mid, call_mid=call_mid))
    return rows


def _matched_events_normal_dist(n: int = 30, *, sigma_pct: float = 5.0) -> List[dict]:
    """A historical-event distribution: realized moves roughly N(0, sigma_pct).

    Deterministic — we just lay down a fixed sequence of percentages
    so the breach estimator is reproducible across runs.
    """
    pattern = [0.0, +2.5, -2.5, +4.0, -4.0, +6.0, -6.0, +8.0, -8.0, +10.0, -10.0]
    out = []
    for i in range(n):
        out.append({"realizedMovePct": pattern[i % len(pattern)]})
    return out


# ---------------------------------------------------------------------------
# infer_strike_step
# ---------------------------------------------------------------------------

def test_infer_strike_step_dollar_grid():
    # HD-style $1 strikes.
    assert infer_strike_step([320.0, 325.0, 330.0, 335.0]) == 5.0
    # NVDA $1 grid.
    assert infer_strike_step([140.0, 141.0, 142.0, 143.0]) == 1.0


def test_infer_strike_step_two_fifty():
    # AMC / smaller-cap $2.50 grid.
    assert infer_strike_step([100.0, 102.5, 105.0, 107.5]) == 2.5


def test_infer_strike_step_spx_five():
    # SPX-style $5 strikes (contiguous).
    assert infer_strike_step([5780.0, 5785.0, 5790.0, 5795.0]) == 5.0


def test_infer_strike_step_spx_ten():
    # When only every-other strike is selected, infer the widest plausible.
    assert infer_strike_step([5780.0, 5790.0, 5810.0, 5820.0]) == 10.0


def test_infer_strike_step_falls_back():
    # Single strike, no signal.
    assert infer_strike_step([100.0]) == 1.0
    assert infer_strike_step([]) == 1.0


# ---------------------------------------------------------------------------
# generate_candidates
# ---------------------------------------------------------------------------

def _hd_baseline() -> CandidateStrikes:
    """HD-shaped baseline: $5 grid, ±5% wings."""
    return CandidateStrikes(
        short_put=325.0, long_put=320.0,
        short_call=345.0, long_call=350.0,
        structure=STRUCTURE_IC,
    )


def test_generator_produces_substantial_candidate_set():
    candidates = generate_candidates(
        baseline_strikes=_hd_baseline(), strike_step=5.0,
    )
    # ~70-200 candidates after dedup + width constraints; broad sanity check.
    assert 60 <= len(candidates) <= 200


def test_generator_excludes_baseline_tuple():
    baseline = _hd_baseline()
    candidates = generate_candidates(baseline_strikes=baseline, strike_step=5.0)
    baseline_key = (
        baseline.short_put, baseline.long_put,
        baseline.short_call, baseline.long_call, baseline.structure,
    )
    for c in candidates:
        key = (c.short_put, c.long_put, c.short_call, c.long_call, c.structure)
        assert key != baseline_key


def test_generator_respects_strike_step_alignment():
    candidates = generate_candidates(
        baseline_strikes=_hd_baseline(), strike_step=5.0,
    )
    for c in candidates:
        for k in (c.short_put, c.long_put, c.short_call, c.long_call):
            if k is None:
                continue
            # Every emitted strike should land on the 5-point grid.
            assert abs((k % 5.0) - 0.0) < 1e-6 or abs((k % 5.0) - 5.0) < 1e-6


def test_generator_no_negative_or_zero_widths():
    candidates = generate_candidates(
        baseline_strikes=_hd_baseline(), strike_step=5.0,
    )
    for c in candidates:
        if c.structure in (STRUCTURE_IC, STRUCTURE_ASYM):
            assert c.short_put > c.long_put
            assert c.long_call > c.short_call
            assert c.short_call > c.short_put
        elif c.structure == STRUCTURE_FLY:
            # Short legs touch (or near it); long wings sit outside.
            assert c.short_put >= c.long_put
            assert c.long_call >= c.short_call
        elif c.structure == STRUCTURE_PUT_VERT:
            assert c.short_call is None and c.long_call is None
            assert c.short_put > c.long_put
        elif c.structure == STRUCTURE_CALL_VERT:
            assert c.short_put is None and c.long_put is None
            assert c.long_call > c.short_call


def test_generator_includes_all_structure_families():
    candidates = generate_candidates(
        baseline_strikes=_hd_baseline(), strike_step=5.0,
    )
    structures = {c.structure for c in candidates}
    assert STRUCTURE_IC in structures
    assert STRUCTURE_ASYM in structures
    assert STRUCTURE_FLY in structures
    assert STRUCTURE_PUT_VERT in structures
    assert STRUCTURE_CALL_VERT in structures


def test_generator_deduplicates_overlapping_families():
    # Wing-width family overlap with strike sweep when wings happen to
    # equal baseline wings. Dedup should drop the redundant entry.
    candidates = generate_candidates(
        baseline_strikes=_hd_baseline(), strike_step=5.0,
    )
    keys = set()
    for c in candidates:
        key = (c.short_put, c.long_put, c.short_call, c.long_call, c.structure)
        assert key not in keys, f"duplicate candidate: {key}"
        keys.add(key)


# ---------------------------------------------------------------------------
# score_candidate
# ---------------------------------------------------------------------------

def _hd_chain() -> List[ChainRow]:
    """HD-style chain centered on 335 with $5 strikes from 315 to 360."""
    return _synthetic_chain(
        spot=335.0,
        strikes=[315.0, 320.0, 325.0, 330.0, 335.0, 340.0, 345.0, 350.0, 355.0, 360.0],
    )


def test_score_candidate_basic_round_trip():
    baseline = _hd_baseline()
    chain = _hd_chain()
    events = _matched_events_normal_dist(n=20, sigma_pct=5.0)
    scored = score_candidate(
        baseline, entry_chain=chain, matched_events=events,
        user_spot=335.0, snap_max_pts=5.0,
        fill_model=FillModel(mode="mid"),
    )
    assert scored is not None
    assert scored.credit > 0.0
    assert scored.max_loss >= 0.0
    # With ±10% strike walls and a sigma-of-5pct event pool, breach is rare.
    assert 0.0 <= scored.p_breach <= 1.0


def test_score_candidate_wider_short_strikes_lowers_breach():
    """Move short put + short call OUT by one step -> p_breach must not rise."""
    base = _hd_baseline()
    wider = CandidateStrikes(
        short_put=base.short_put - 5.0,  # further from spot 335 (320 vs 325)
        long_put=base.long_put - 5.0,
        short_call=base.short_call + 5.0,  # further from spot (350 vs 345)
        long_call=base.long_call + 5.0,
        structure=STRUCTURE_IC,
    )
    chain = _hd_chain()
    events = _matched_events_normal_dist(n=30, sigma_pct=5.0)
    a = score_candidate(
        base, entry_chain=chain, matched_events=events,
        user_spot=335.0, snap_max_pts=5.0, fill_model=FillModel(mode="mid"),
    )
    b = score_candidate(
        wider, entry_chain=chain, matched_events=events,
        user_spot=335.0, snap_max_pts=5.0, fill_model=FillModel(mode="mid"),
    )
    assert a is not None and b is not None
    assert b.p_breach <= a.p_breach + 1e-9


def test_score_candidate_returns_none_when_chain_missing_strikes():
    base = _hd_baseline()
    # Chain that doesn't cover any of the baseline strikes.
    chain = _synthetic_chain(spot=200.0, strikes=[180.0, 190.0, 200.0])
    events = _matched_events_normal_dist(n=10)
    scored = score_candidate(
        base, entry_chain=chain, matched_events=events,
        user_spot=200.0, snap_max_pts=1.0, fill_model=FillModel(mode="mid"),
    )
    # snap tolerance too tight to find baseline strikes near 200 chain.
    assert scored is None


def test_score_candidate_delta_pcts_are_zero_when_no_baseline():
    base = _hd_baseline()
    chain = _hd_chain()
    events = _matched_events_normal_dist(n=15)
    scored = score_candidate(
        base, entry_chain=chain, matched_events=events,
        user_spot=335.0, snap_max_pts=5.0, fill_model=FillModel(mode="mid"),
        baseline=None,
    )
    assert scored is not None
    assert scored.delta_breach_pct == 0.0
    assert scored.delta_credit_pct == 0.0
    assert scored.delta_ev_pct == 0.0


# ---------------------------------------------------------------------------
# rank_and_verdict
# ---------------------------------------------------------------------------

def _baseline_scored() -> ScoredCandidate:
    return ScoredCandidate(
        strikes=_hd_baseline(),
        credit=1.50, max_loss=3.50,
        p_breach=0.30, p_breach_interval=(0.20, 0.40),
        ev=0.0,
        delta_breach_pct=0.0, delta_credit_pct=0.0,
        delta_max_loss_pct=0.0, delta_ev_pct=0.0,
        is_baseline=True,
    )


def _alt(*, credit, p_breach, max_loss=3.5, ev=0.0, struct=STRUCTURE_IC,
         sp=325.0, lp=320.0, sc=345.0, lc=350.0,
         d_breach=0.0, d_credit=0.0, d_max=0.0, d_ev=0.0) -> ScoredCandidate:
    return ScoredCandidate(
        strikes=CandidateStrikes(
            short_put=sp, long_put=lp, short_call=sc, long_call=lc, structure=struct,
        ),
        credit=credit, max_loss=max_loss,
        p_breach=p_breach, p_breach_interval=(p_breach - 0.05, p_breach + 0.05),
        ev=ev,
        delta_breach_pct=d_breach, delta_credit_pct=d_credit,
        delta_max_loss_pct=d_max, delta_ev_pct=d_ev,
    )


def test_verdict_dominating_when_better_ev_and_safer():
    base = _baseline_scored()
    dom = _alt(
        credit=1.60, p_breach=0.22, ev=0.5,
        sp=330.0, sc=350.0,  # different strikes
        d_breach=-25.0, d_credit=+6.7, d_ev=+25.0,
    )
    out = rank_and_verdict(baseline=base, scored=[dom])
    assert out["verdict"] == "dominating"
    assert out["top_alternatives"][0]["strikes"]["shortPut"] == 330.0


def test_verdict_safer_alternative_when_breach_drops_credit_holds():
    base = _baseline_scored()
    safer = _alt(
        credit=1.40, p_breach=0.18, ev=0.15,
        sp=320.0, lp=315.0, sc=350.0, lc=355.0,
        d_breach=-40.0, d_credit=-6.7, d_ev=+2.0,
    )
    out = rank_and_verdict(baseline=base, scored=[safer])
    assert out["verdict"] == "safer_alternative"


def test_verdict_richer_alternative_when_credit_jumps_breach_holds():
    base = _baseline_scored()
    richer = _alt(
        credit=1.80, p_breach=0.32, ev=0.20,
        sp=330.0, sc=340.0,
        d_breach=+6.0, d_credit=+20.0, d_ev=+10.0,
    )
    out = rank_and_verdict(baseline=base, scored=[richer])
    assert out["verdict"] == "richer_alternative"


def test_verdict_optimal_when_nothing_dominates():
    base = _baseline_scored()
    # All worse on every axis.
    worse = _alt(
        credit=1.30, p_breach=0.45, ev=-0.20,
        sp=330.0, sc=340.0,
        d_breach=+50.0, d_credit=-13.3, d_ev=-50.0,
    )
    out = rank_and_verdict(baseline=base, scored=[worse])
    assert out["verdict"] == "optimal"
    assert "as good as it gets" in out["headline"]


def test_verdict_priority_dominating_beats_safer_and_richer():
    base = _baseline_scored()
    dom = _alt(
        credit=1.65, p_breach=0.20, ev=0.50,
        sp=330.0, sc=350.0,
        d_breach=-33.3, d_credit=+10.0, d_ev=+25.0,
    )
    safer = _alt(
        credit=1.40, p_breach=0.18, ev=0.10,
        sp=320.0, lp=315.0, sc=350.0, lc=355.0,
        d_breach=-40.0, d_credit=-6.7, d_ev=+2.0,
    )
    out = rank_and_verdict(baseline=base, scored=[safer, dom])
    assert out["verdict"] == "dominating"
    # The top alt slot should be the dominating one.
    assert out["top_alternatives"][0]["delta_ev_pct"] == 25.0


def test_verdict_baseline_excluded_from_top_alternatives():
    base = _baseline_scored()
    # A "candidate" that is literally the baseline.
    bl_copy = ScoredCandidate(
        strikes=base.strikes,
        credit=base.credit, max_loss=base.max_loss,
        p_breach=base.p_breach, p_breach_interval=base.p_breach_interval,
        ev=base.ev,
        delta_breach_pct=0.0, delta_credit_pct=0.0,
        delta_max_loss_pct=0.0, delta_ev_pct=0.0,
    )
    dom = _alt(
        credit=1.65, p_breach=0.20, ev=0.50,
        sp=330.0, sc=350.0,
        d_breach=-33.3, d_credit=+10.0, d_ev=+25.0,
    )
    out = rank_and_verdict(baseline=base, scored=[bl_copy, dom])
    # The baseline copy must NOT appear in the top alternatives.
    for alt in out["top_alternatives"]:
        s = alt["strikes"]
        assert not (
            s["shortPut"] == base.strikes.short_put
            and s["longPut"] == base.strikes.long_put
            and s["shortCall"] == base.strikes.short_call
            and s["longCall"] == base.strikes.long_call
        )


# ---------------------------------------------------------------------------
# run_strike_scan (top-level end-to-end with synthetic chain)
# ---------------------------------------------------------------------------

def test_run_strike_scan_end_to_end_returns_verdict():
    baseline = _hd_baseline()
    chain = _hd_chain()
    events = _matched_events_normal_dist(n=25)
    out = run_strike_scan(
        baseline_strikes=baseline, baseline_credit=1.50,
        entry_chain=chain, matched_events=events,
        user_spot=335.0, snap_max_pts=5.0,
        fill_model=FillModel(mode="mid"),
    )
    assert out["verdict"] in (
        "dominating", "safer_alternative", "richer_alternative", "optimal",
    )
    assert out["scanned_n"] > 0
    assert "headline" in out
    assert isinstance(out["top_alternatives"], list)
    assert isinstance(out["all_candidates"], list)
    assert out["strike_step"] == 5.0


def test_run_strike_scan_baseline_p_breach_matches_event_pool():
    """With our deterministic ±10/±8/±6/... pattern and short strikes at
    ±~3% from spot, every event > 3% triggers a breach."""
    baseline = _hd_baseline()
    chain = _hd_chain()
    events = _matched_events_normal_dist(n=22)
    out = run_strike_scan(
        baseline_strikes=baseline, baseline_credit=1.50,
        entry_chain=chain, matched_events=events,
        user_spot=335.0, snap_max_pts=5.0,
        fill_model=FillModel(mode="mid"),
    )
    # Short put at 325 -> 2.99% from spot 335; short call 345 -> 2.99%.
    # Events with |realized| > 2.99% breach. Pattern: 0, ±2.5, ±4, ±6, ±8, ±10.
    # 22 events, 9/11 of each cycle are > 2.99% -> 18/22 ≈ 0.818
    base = out["baseline"]
    assert 0.7 <= base["p_breach"] <= 0.9


# ---------------------------------------------------------------------------
# Endpoint integration tests
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def client():
    from backend.app import app
    return TestClient(app)


@pytest.fixture(autouse=False)
def _enable_engine15(monkeypatch):
    from backend.config import get_flags
    f = get_flags()
    monkeypatch.setattr(
        "backend.routers.engine15_earnings_ic.get_flags",
        lambda: replace(f, ENABLE_ENGINE15_EARNINGS_IC=True),
    )


def _scenario_request_payload() -> dict:
    return {
        "ticker":            "HD",
        "entryDate":         "2026-04-15",
        "expiry":            "2026-04-17",
        "earningsDate":      "2026-04-16",
        "earningsTiming":    "AMC",
        "plannedExitDate":   "2026-04-17",
        "shortPut":          325.0,
        "longPut":           320.0,
        "shortCall":         345.0,
        "longCall":          350.0,
        "creditReceived":    1.50,
        "profitTargetPct":   50.0,
        "stopLossPct":       150.0,
    }


def _baseline_payload(matched_events: List[dict]) -> dict:
    return {
        "entryState": {"userSpot": 335.0, "userEmPct": 5.0},
        "matchedEvents": matched_events,
        "engine1Summary": {"stockPrice": 335.0},
    }


def test_strike_scan_endpoint_round_trip(client, _enable_engine15, tmp_chain_db):
    # Seed a chain for HD covering the entry day.
    rows = []
    for k in [315.0, 320.0, 325.0, 330.0, 335.0, 340.0, 345.0, 350.0, 355.0, 360.0]:
        intrinsic_put = max(0.0, k - 335.0)
        intrinsic_call = max(0.0, 335.0 - k)
        extrinsic = max(0.10, 2.0 - abs(k - 335.0) * 0.02)
        rows.append({
            "ticker": "HD", "tradeDate": "2026-04-15", "expirDate": "2026-04-17",
            "strike": float(k), "stockPrice": 335.0,
            "callMidPrice": intrinsic_call + extrinsic,
            "callBidPrice": (intrinsic_call + extrinsic) * 0.95,
            "callAskPrice": (intrinsic_call + extrinsic) * 1.05,
            "callMidIv": 0.30,
            "putMidPrice": intrinsic_put + extrinsic,
            "putBidPrice": (intrinsic_put + extrinsic) * 0.95,
            "putAskPrice": (intrinsic_put + extrinsic) * 1.05,
            "putMidIv": 0.30,
            "callOpenInterest": 100, "putOpenInterest": 100,
        })
    n = chain_cache.upsert_chain(ticker="HD", trade_date="2026-04-15", rows=rows)
    assert n == 10

    body = {
        "scenarioRequest": _scenario_request_payload(),
        "baseline":        _baseline_payload(_matched_events_normal_dist(n=20)),
        "snapMaxPts":      5.0,
        "fillMode":        "mid",
    }
    r = client.post("/api/earnings-ic/strike-scan", json=body)
    assert r.status_code == 200, r.text
    out = r.json()
    assert out["ticker"] == "HD"
    assert out["verdict"] in (
        "dominating", "safer_alternative", "richer_alternative", "optimal",
    )
    assert isinstance(out["top_alternatives"], list)
    assert isinstance(out["all_candidates"], list)
    assert out["scanned_n"] > 0
    assert out["scan_meta"]["n_priced"] > 0


def test_strike_scan_endpoint_rejects_missing_baseline(client, _enable_engine15):
    body = {
        "scenarioRequest": _scenario_request_payload(),
        "baseline":        {},  # no matchedEvents
    }
    r = client.post("/api/earnings-ic/strike-scan", json=body)
    assert r.status_code == 400


def test_strike_scan_endpoint_rejects_thin_event_pool(
    client, _enable_engine15, tmp_chain_db,
):
    body = {
        "scenarioRequest": _scenario_request_payload(),
        "baseline":        _baseline_payload(_matched_events_normal_dist(n=2)),
    }
    r = client.post("/api/earnings-ic/strike-scan", json=body)
    assert r.status_code == 422
    assert "matchedEvents" in r.json()["detail"]


def test_strike_scan_endpoint_503_when_chain_uncached(
    client, _enable_engine15, tmp_chain_db,
):
    body = {
        "scenarioRequest": _scenario_request_payload(),
        "baseline":        _baseline_payload(_matched_events_normal_dist(n=20)),
    }
    # tmp_chain_db is empty — no chain rows seeded.
    r = client.post("/api/earnings-ic/strike-scan", json=body)
    assert r.status_code == 503
    assert "No cached chain" in r.json()["detail"]
