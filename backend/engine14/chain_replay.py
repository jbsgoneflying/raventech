"""Engine 14 — per-day IC repricing helpers.

The simulator calls `reprice_ic` once per analogue-day to compute the
current net-debit-to-close of the short iron condor. That value vs the
entry credit is the mark-to-market P&L basis.

Inputs:
  * A `ChainRow` slice for one (ticker, trade_date, expiry) pulled from
    `chain_cache.fetch_chain_slice`.
  * Four target strikes in EM-distance units mapped to the analogue's
    spot + EM scale. Strikes are *snapped* to the nearest listed strike
    within `ENGINE14_STRIKE_SNAP_MAX_PTS`; failures surface explicitly so
    the caller can drop the analogue.

Sign convention: an iron condor SHORT position has P&L =
  credit_received - net_debit_to_close
where `net_debit_to_close = (short_call_mid + short_put_mid)
                           - (long_call_mid + long_put_mid)`.

Positive P&L = profit. P&L expressed as % of credit received in the
user-facing payload.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional

from backend.engine14.chain_cache import ChainRow

LOG = logging.getLogger("engine14.chain_replay")


@dataclass(frozen=True)
class LegPrice:
    strike_target: float
    strike_snapped: float
    snap_distance_pts: float
    mid: float
    bid: Optional[float]
    ask: Optional[float]


@dataclass(frozen=True)
class IcPrice:
    net_debit_to_close: float  # positive => costs money to buy back short IC
    short_put: LegPrice
    long_put: LegPrice
    short_call: LegPrice
    long_call: LegPrice
    pnl_vs_credit: float        # credit - net_debit_to_close (per point)
    pnl_pct_of_credit: float   # 100 * (credit - net_debit) / credit


def _snap(strikes: List[float], target: float, max_dist: float) -> Optional[int]:
    """Return the index of the listed strike nearest `target`, or None if
    outside the snap tolerance. Stable tie-break: lower strike wins."""
    if not strikes:
        return None
    best_i = 0
    best_d = abs(strikes[0] - target)
    for i in range(1, len(strikes)):
        d = abs(strikes[i] - target)
        if d < best_d - 1e-9:
            best_d = d
            best_i = i
    if best_d > max_dist + 1e-9:
        return None
    return best_i


def _put_mid_from(row: ChainRow) -> Optional[float]:
    return row.put_mid_px()


def _call_mid_from(row: ChainRow) -> Optional[float]:
    return row.call_mid_px()


def reprice_ic(
    *,
    chain: List[ChainRow],
    short_put_strike: float,
    long_put_strike: float,
    short_call_strike: float,
    long_call_strike: float,
    entry_credit: float,
    snap_max_pts: float = 5.0,
) -> Optional[IcPrice]:
    """Return per-day IC valuation, or None if any leg can't be priced.

    `entry_credit` is the original per-contract premium (in underlying
    points — e.g. 1.85 for a 1.85 credit) used only to compute the
    percent-of-credit P&L convenience field.

    Caller supplies the chain slice pre-filtered to a single (trade_date,
    expiry) pair — see `chain_cache.fetch_chain_slice`.
    """
    if not chain:
        return None
    if entry_credit is None or not math.isfinite(float(entry_credit)) or float(entry_credit) <= 0:
        return None

    strikes = [float(r.strike) for r in chain]

    def _leg(target: float, side: str) -> Optional[LegPrice]:
        idx = _snap(strikes, float(target), float(snap_max_pts))
        if idx is None:
            return None
        row = chain[idx]
        mid = _put_mid_from(row) if side == "put" else _call_mid_from(row)
        if mid is None or mid <= 0:
            return None
        bid = row.put_bid if side == "put" else row.call_bid
        ask = row.put_ask if side == "put" else row.call_ask
        return LegPrice(
            strike_target=float(target),
            strike_snapped=float(row.strike),
            snap_distance_pts=abs(float(row.strike) - float(target)),
            mid=float(mid),
            bid=(None if bid is None else float(bid)),
            ask=(None if ask is None else float(ask)),
        )

    sp = _leg(short_put_strike, "put")
    lp = _leg(long_put_strike, "put")
    sc = _leg(short_call_strike, "call")
    lc = _leg(long_call_strike, "call")
    if not (sp and lp and sc and lc):
        return None

    net_debit = (sp.mid + sc.mid) - (lp.mid + lc.mid)
    # Short IC was opened for a credit; closing it back costs `net_debit`.
    pnl = float(entry_credit) - float(net_debit)
    pnl_pct = 100.0 * pnl / float(entry_credit) if float(entry_credit) > 0 else 0.0

    return IcPrice(
        net_debit_to_close=float(net_debit),
        short_put=sp,
        long_put=lp,
        short_call=sc,
        long_call=lc,
        pnl_vs_credit=float(pnl),
        pnl_pct_of_credit=float(pnl_pct),
    )


def expiry_payoff(
    *,
    expiry_spot: float,
    short_put_strike: float,
    long_put_strike: float,
    short_call_strike: float,
    long_call_strike: float,
    entry_credit: float,
) -> float:
    """Deterministic IC terminal P&L (in credit-points, not dollars).

    Used as a fallback for the expiry day when the cached chain has no
    rows at T=0 (e.g. 0DTE removal). Same sign convention as `reprice_ic`.
    """
    s = float(expiry_spot)
    # Long put intrinsic, short put intrinsic, etc.
    sp_val = max(0.0, float(short_put_strike) - s)
    lp_val = max(0.0, float(long_put_strike) - s)
    sc_val = max(0.0, s - float(short_call_strike))
    lc_val = max(0.0, s - float(long_call_strike))
    net_debit = (sp_val + sc_val) - (lp_val + lc_val)
    return float(entry_credit) - float(net_debit)
