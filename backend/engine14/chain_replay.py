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
  * An optional `FillModel` selecting how each leg's closing price is
    drawn from the cached quote.

Sign convention: an iron condor SHORT position has P&L =
  credit_received - net_debit_to_close
where `net_debit_to_close = (short_call_px + short_put_px)
                           - (long_call_px + long_put_px)`.

Positive P&L = profit. P&L expressed as % of credit received in the
user-facing payload.

Fill models
-----------
Three modes are supported via `FillModel.mode`:

  * ``"mid"``           — historical default. Uses the cached mid on every
                          leg. Optimistic: ignores spread cost entirely.
  * ``"nbbo"``           — realistic close. Buys back the shorts at the ASK
                          and sells the longs at the BID. This is what a
                          resting market order actually pays on exit. When
                          bid/ask are missing for a leg we fall back to
                          ``mid_penalty`` pricing for that leg so a single
                          missing quote doesn't abort the replay.
  * ``"mid_penalty"``    — mid-price plus a configurable fraction of the
                          half-spread. Good for back-tests where NBBO data
                          is only partially available.

Sign of slippage on each leg:
  - SHORT leg being CLOSED => buying to close => paying the ASK (worse price)
  - LONG  leg being CLOSED => selling to close => receiving the BID (worse price)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import List, Optional

from backend.engine14.chain_cache import ChainRow

LOG = logging.getLogger("engine14.chain_replay")


# ---- Fill model ---------------------------------------------------------

DEFAULT_PENALTY_PCT = 15.0  # % of half-spread added on top of mid in mid_penalty


@dataclass(frozen=True)
class FillModel:
    """How to price a leg on close.

    ``penalty_pct`` is interpreted as a percentage of the *half-spread*
    that gets added (for shorts being bought back) or subtracted (for
    longs being sold out) from the mid. 15% is a reasonable default for
    SPX weeklies where published NBBO is usually tight but retail fills
    rarely hit pure mid.
    """

    mode: str = "nbbo"             # "mid" | "nbbo" | "mid_penalty"
    penalty_pct: float = DEFAULT_PENALTY_PCT

    @classmethod
    def from_str(cls, mode: Optional[str], penalty_pct: float = DEFAULT_PENALTY_PCT) -> "FillModel":
        m = str(mode or "nbbo").strip().lower()
        if m not in ("mid", "nbbo", "mid_penalty"):
            m = "nbbo"
        return cls(mode=m, penalty_pct=float(penalty_pct))


# ---- Data classes -------------------------------------------------------

@dataclass(frozen=True)
class LegPrice:
    strike_target: float
    strike_snapped: float
    snap_distance_pts: float
    mid: float
    bid: Optional[float]
    ask: Optional[float]
    close_px: float            # the price actually used to close this leg
    fill_source: str           # "mid" | "nbbo" | "mid_penalty" | "mid_fallback"


@dataclass(frozen=True)
class IcPrice:
    net_debit_to_close: float  # positive => costs money to buy back short IC
    short_put: LegPrice
    long_put: LegPrice
    short_call: LegPrice
    long_call: LegPrice
    pnl_vs_credit: float        # credit - net_debit_to_close (per point)
    pnl_pct_of_credit: float   # 100 * (credit - net_debit) / credit
    fill_mode: str             # the FillModel.mode used


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


def _leg_close_price(
    *,
    row: ChainRow,
    side: str,
    is_short: bool,
    fill_model: FillModel,
) -> Optional[tuple]:
    """Return (close_px, mid, bid, ask, fill_source) for a single leg.

    close_px applies `fill_model` realistically:
      - mid           -> always mid
      - nbbo          -> short=ASK, long=BID (worse side); falls back to
                         mid_penalty if bid/ask missing.
      - mid_penalty   -> mid +/- penalty * (half-spread); if bid/ask missing,
                         falls back to pure mid.
    """
    mid = _put_mid_from(row) if side == "put" else _call_mid_from(row)
    if mid is None or mid <= 0:
        return None
    bid = row.put_bid if side == "put" else row.call_bid
    ask = row.put_ask if side == "put" else row.call_ask

    mode = fill_model.mode
    if mode == "mid":
        return (float(mid), float(mid), bid, ask, "mid")

    have_nbbo = (
        bid is not None and ask is not None
        and float(ask) > 0 and float(bid) >= 0 and float(ask) >= float(bid)
    )

    if mode == "nbbo":
        if have_nbbo:
            close_px = float(ask) if is_short else float(bid)
            # Guard: if NBBO produces a non-positive short-close price (can
            # happen on deep OTM rows with bad quotes) fall back to mid.
            if close_px <= 0:
                close_px = float(mid)
                return (close_px, float(mid), bid, ask, "mid_fallback")
            return (close_px, float(mid), float(bid), float(ask), "nbbo")
        # NBBO requested but unavailable -> fall through to mid_penalty style
        mode = "mid_penalty"

    if mode == "mid_penalty":
        if have_nbbo:
            half = max(0.0, (float(ask) - float(bid)) / 2.0)
            bump = half * (float(fill_model.penalty_pct) / 100.0)
            close_px = float(mid) + bump if is_short else max(0.0, float(mid) - bump)
            return (close_px, float(mid), float(bid), float(ask), "mid_penalty")
        # No NBBO — degrade gracefully to mid.
        return (float(mid), float(mid), bid, ask, "mid_fallback")

    # Unknown mode — default to mid.
    return (float(mid), float(mid), bid, ask, "mid")


def reprice_ic(
    *,
    chain: List[ChainRow],
    short_put_strike: float,
    long_put_strike: float,
    short_call_strike: float,
    long_call_strike: float,
    entry_credit: float,
    snap_max_pts: float = 5.0,
    fill_model: Optional[FillModel] = None,
) -> Optional[IcPrice]:
    """Return per-day IC valuation, or None if any leg can't be priced.

    `entry_credit` is the original per-contract premium (in underlying
    points — e.g. 1.85 for a 1.85 credit) used only to compute the
    percent-of-credit P&L convenience field.

    Caller supplies the chain slice pre-filtered to a single (trade_date,
    expiry) pair — see `chain_cache.fetch_chain_slice`.

    Pass a `FillModel` to control how leg exit prices are drawn. When
    omitted, defaults to NBBO (realistic close) with mid fallback for
    rows that lack published bid/ask data.
    """
    if not chain:
        return None
    if entry_credit is None or not math.isfinite(float(entry_credit)) or float(entry_credit) <= 0:
        return None

    fm = fill_model or FillModel()

    strikes = [float(r.strike) for r in chain]

    def _leg(target: float, side: str, is_short: bool) -> Optional[LegPrice]:
        idx = _snap(strikes, float(target), float(snap_max_pts))
        if idx is None:
            return None
        row = chain[idx]
        priced = _leg_close_price(
            row=row, side=side, is_short=is_short, fill_model=fm,
        )
        if priced is None:
            return None
        close_px, mid, bid, ask, src = priced
        return LegPrice(
            strike_target=float(target),
            strike_snapped=float(row.strike),
            snap_distance_pts=abs(float(row.strike) - float(target)),
            mid=float(mid),
            bid=(None if bid is None else float(bid)),
            ask=(None if ask is None else float(ask)),
            close_px=float(close_px),
            fill_source=str(src),
        )

    sp = _leg(short_put_strike, "put", is_short=True)
    lp = _leg(long_put_strike, "put", is_short=False)
    sc = _leg(short_call_strike, "call", is_short=True)
    lc = _leg(long_call_strike, "call", is_short=False)
    if not (sp and lp and sc and lc):
        return None

    net_debit = (sp.close_px + sc.close_px) - (lp.close_px + lc.close_px)
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
        fill_mode=str(fm.mode),
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
