"""
Unit tests for the expected_move module (ATM-forward straddle algorithm).

Tests cover:
1. Core algorithm with synthetic option chain data
2. Helper functions (weighted median, linear interpolation, etc.)
3. Strike targets computation
4. Edge cases and fallbacks
"""

import datetime as dt
import pytest

from backend.expected_move import (
    StrikeQuote,
    compute_expected_move_from_chain,
    compute_strike_targets,
    _weighted_median,
    _linear_interp,
    _yearfrac,
    _discount_factor,
    _infer_forward_price,
    _interpolate_atm_straddle,
)


class TestHelperFunctions:
    """Test helper/utility functions."""

    def test_yearfrac_basic(self):
        t0 = dt.date(2026, 1, 13)
        t_exp = dt.date(2026, 1, 17)
        result = _yearfrac(t0, t_exp)
        assert result == pytest.approx(4 / 365.0, abs=1e-9)

    def test_yearfrac_same_day(self):
        t0 = dt.date(2026, 1, 13)
        result = _yearfrac(t0, t0)
        assert result == 0.0

    def test_yearfrac_past_date(self):
        t0 = dt.date(2026, 1, 13)
        t_exp = dt.date(2026, 1, 10)  # Past
        result = _yearfrac(t0, t_exp)
        assert result == 0.0  # Clamped to 0

    def test_discount_factor(self):
        # At 5% rate for 1 year
        df = _discount_factor(0.05, 1.0)
        assert df == pytest.approx(0.951229, abs=1e-5)

    def test_discount_factor_short_term(self):
        # 4 days at 5%
        T = 4 / 365.0
        df = _discount_factor(0.05, T)
        assert df == pytest.approx(0.99945, abs=1e-4)

    def test_weighted_median_basic(self):
        values = [(10.0, 1.0), (20.0, 1.0), (30.0, 1.0)]
        result = _weighted_median(values)
        assert result == 20.0

    def test_weighted_median_weighted(self):
        # Heavy weight on 10, should pull median down
        values = [(10.0, 10.0), (20.0, 1.0), (30.0, 1.0)]
        result = _weighted_median(values)
        assert result == 10.0

    def test_weighted_median_empty(self):
        result = _weighted_median([])
        assert result is None

    def test_linear_interp_basic(self):
        # Interpolate at x=1.5 between (1, 10) and (2, 20)
        result = _linear_interp(1.5, 1.0, 2.0, 10.0, 20.0)
        assert result == pytest.approx(15.0, abs=1e-9)

    def test_linear_interp_at_endpoints(self):
        assert _linear_interp(1.0, 1.0, 2.0, 10.0, 20.0) == pytest.approx(10.0)
        assert _linear_interp(2.0, 1.0, 2.0, 10.0, 20.0) == pytest.approx(20.0)

    def test_linear_interp_same_x(self):
        # Edge case: x0 == x1, should return average
        result = _linear_interp(1.0, 1.0, 1.0, 10.0, 20.0)
        assert result == pytest.approx(15.0)


class TestStrikeQuote:
    """Test StrikeQuote dataclass."""

    def test_strike_quote_mid_computation(self):
        q = StrikeQuote(
            strike=100.0,
            call_bid=5.0,
            call_ask=5.20,
            put_bid=4.80,
            put_ask=5.00,
        )
        assert q.call_mid == pytest.approx(5.10)
        assert q.put_mid == pytest.approx(4.90)
        assert q.call_spread == pytest.approx(0.20)
        assert q.put_spread == pytest.approx(0.20)

    def test_strike_quote_usable(self):
        q = StrikeQuote(
            strike=100.0,
            call_bid=5.0,
            call_ask=5.20,
            put_bid=4.80,
            put_ask=5.00,
        )
        assert q.is_usable() is True

    def test_strike_quote_not_usable_zero_bid(self):
        q = StrikeQuote(
            strike=100.0,
            call_bid=0.0,
            call_ask=5.20,
            put_bid=4.80,
            put_ask=5.00,
        )
        assert q.is_usable() is False

    def test_strike_quote_not_usable_none(self):
        q = StrikeQuote(
            strike=100.0,
            call_bid=None,
            call_ask=None,
            put_bid=None,
            put_ask=None,
        )
        assert q.is_usable() is False


class TestForwardInference:
    """Test forward price inference via put-call parity."""

    def test_infer_forward_basic(self):
        # Create quotes near spot=100
        quotes = [
            StrikeQuote(strike=98.0, call_bid=4.0, call_ask=4.20, put_bid=2.0, put_ask=2.20),
            StrikeQuote(strike=100.0, call_bid=3.0, call_ask=3.20, put_bid=3.0, put_ask=3.20),
            StrikeQuote(strike=102.0, call_bid=2.0, call_ask=2.20, put_bid=4.0, put_ask=4.20),
        ]
        df = 0.999  # Near 1 for short-term
        forward, n_used, warnings = _infer_forward_price(quotes, df)

        assert forward is not None
        assert n_used == 3
        # Forward should be close to 100 for ATM quotes with symmetric C/P
        assert forward == pytest.approx(100.0, abs=1.0)

    def test_infer_forward_empty(self):
        forward, n_used, warnings = _infer_forward_price([], 0.999)
        assert forward is None
        assert n_used == 0
        assert len(warnings) > 0


class TestATMInterpolation:
    """Test ATM straddle interpolation."""

    def test_interpolate_basic(self):
        quotes = [
            StrikeQuote(strike=98.0, call_bid=4.0, call_ask=4.20, put_bid=2.0, put_ask=2.20),
            StrikeQuote(strike=100.0, call_bid=3.0, call_ask=3.20, put_bid=3.0, put_ask=3.20),
            StrikeQuote(strike=102.0, call_bid=2.0, call_ask=2.20, put_bid=4.0, put_ask=4.20),
        ]
        forward = 100.0
        c_f, p_f, warnings = _interpolate_atm_straddle(quotes, forward)

        # At forward=100, should match the 100 strike exactly
        assert c_f == pytest.approx(3.10, abs=0.01)  # Mid of call at 100
        assert p_f == pytest.approx(3.10, abs=0.01)  # Mid of put at 100

    def test_interpolate_between_strikes(self):
        quotes = [
            StrikeQuote(strike=98.0, call_bid=4.0, call_ask=4.20, put_bid=2.0, put_ask=2.20),
            StrikeQuote(strike=102.0, call_bid=2.0, call_ask=2.20, put_bid=4.0, put_ask=4.20),
        ]
        forward = 100.0  # Between 98 and 102
        c_f, p_f, warnings = _interpolate_atm_straddle(quotes, forward)

        # Should interpolate between the two strikes
        assert c_f is not None
        assert p_f is not None
        # Midway between call mids (4.10 and 2.10) = 3.10
        assert c_f == pytest.approx(3.10, abs=0.01)


class TestComputeExpectedMoveFromChain:
    """Test the core expected move computation."""

    def test_basic_chain(self):
        # Simulate a simple option chain
        rows = [
            {
                "strike": 95.0,
                "spotPrice": 100.0,
                "callBidPrice": 6.0,
                "callAskPrice": 6.20,
                "putBidPrice": 1.0,
                "putAskPrice": 1.20,
            },
            {
                "strike": 100.0,
                "spotPrice": 100.0,
                "callBidPrice": 3.0,
                "callAskPrice": 3.20,
                "putBidPrice": 3.0,
                "putAskPrice": 3.20,
            },
            {
                "strike": 105.0,
                "spotPrice": 100.0,
                "callBidPrice": 1.0,
                "callAskPrice": 1.20,
                "putBidPrice": 6.0,
                "putAskPrice": 6.20,
            },
        ]

        result = compute_expected_move_from_chain(
            rows,
            spot=100.0,
            expiry=dt.date(2026, 1, 17),
            as_of=dt.date(2026, 1, 13),
            risk_free_rate=0.05,
        )

        assert result["spotPrice"] == 100.0
        assert result["dte"] == 4
        assert result["forwardPrice"] is not None
        assert result["expectedMoveDollars"] is not None
        assert result["expectedMovePct"] is not None

        # Expected move should be roughly the straddle price (~6.20 for ATM)
        # EM % should be around 6.2% for a 100 spot
        assert result["expectedMovePct"] == pytest.approx(6.2, abs=1.0)

    def test_empty_chain(self):
        result = compute_expected_move_from_chain(
            [],
            spot=100.0,
            expiry=dt.date(2026, 1, 17),
            as_of=dt.date(2026, 1, 13),
        )
        assert result["expectedMoveDollars"] is None
        assert result["expectedMovePct"] is None
        assert len(result["warnings"]) > 0

    def test_expired(self):
        rows = [
            {
                "strike": 100.0,
                "spotPrice": 100.0,
                "callBidPrice": 3.0,
                "callAskPrice": 3.20,
                "putBidPrice": 3.0,
                "putAskPrice": 3.20,
            },
        ]

        # Expiry in the past
        result = compute_expected_move_from_chain(
            rows,
            spot=100.0,
            expiry=dt.date(2026, 1, 10),  # Past
            as_of=dt.date(2026, 1, 13),
        )
        assert result["expectedMoveDollars"] is None
        assert "past" in str(result["warnings"]).lower()


class TestStrikeTargets:
    """Test strike targets computation."""

    def test_basic_targets(self):
        # 2.5% expected move on a $100 stock
        result = compute_strike_targets(2.5, 100.0)

        # White = 2.5% * 100 * 2 = 5.0
        assert result["whitePts"] == pytest.approx(5.0)

        # Blue = White * 1.5 = 7.5
        assert result["bluePts"] == pytest.approx(7.5)

        # Red = White * 2 = 10.0
        assert result["redPts"] == pytest.approx(10.0)

    def test_targets_with_large_em(self):
        # 10% expected move on a $50 stock
        result = compute_strike_targets(10.0, 50.0)

        # White = 10% * 50 * 2 = 10.0
        assert result["whitePts"] == pytest.approx(10.0)

        # Blue = 15.0
        assert result["bluePts"] == pytest.approx(15.0)

        # Red = 20.0
        assert result["redPts"] == pytest.approx(20.0)

    def test_targets_metadata(self):
        result = compute_strike_targets(5.0, 200.0)
        assert result["whiteMultiple"] == 1.0
        assert result["blueMultiple"] == 1.5
        assert result["redMultiple"] == 2.0
        assert result["basedOnEmPct"] == 5.0
        assert result["basedOnSpot"] == 200.0
