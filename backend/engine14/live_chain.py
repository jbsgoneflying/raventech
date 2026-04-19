"""Live ORATS chain helpers for Engine 14 reconciliation + pre-check.

Two responsibilities:

* ``fetch_live_chain_nbbo``: snap live NBBO for the four legs of an iron
  condor at a specific expiry. Returns both per-leg prices and the
  implied *net* credit mid/bid/ask so reconciliation can compare the
  user's typed credit to a live anchor.

* ``validate_strikes_exist``: confirm that every user leg maps to an
  actually-tradable strike on the given expiry. This is the guardrail
  that catches the "7365 vs 7360" fat-finger error we diagnosed. When
  a leg is missing we also return the nearest live strike so the UI
  can offer a one-click fix.

Both helpers tolerate failure silently — callers degrade gracefully
and the UI collapses the corresponding reconciliation chip to "na".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("engine14.live_chain")

_LIVE_STRIKES_FIELDS = (
    "ticker,expirDate,strike,"
    "callBidPrice,callAskPrice,callMidIv,"
    "putBidPrice,putAskPrice,putMidIv,"
    "delta,callDelta,putDelta"
)


def _to_float(x: Any) -> Optional[float]:
    if x is None:
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # NaN / inf
        return None
    return v


@dataclass(frozen=True)
class Leg:
    """A single iron-condor leg for the live-chain helpers."""
    kind: str        # "shortPut" | "longPut" | "shortCall" | "longCall"
    right: str       # "P" | "C"
    strike: float
    side: str        # "short" (we collect) | "long" (we pay)


def _legs_from_strikes(
    *,
    short_put: float,
    long_put: float,
    short_call: float,
    long_call: float,
) -> List[Leg]:
    return [
        Leg("shortPut", "P", float(short_put), "short"),
        Leg("longPut",  "P", float(long_put),  "long"),
        Leg("shortCall","C", float(short_call),"short"),
        Leg("longCall", "C", float(long_call), "long"),
    ]


def _load_live_rows(
    client: Any,
    *,
    ticker: str,
    expiry: str,
) -> List[Dict[str, Any]]:
    """Fetch live strikes for an expiry and filter to the target expiry rows."""
    if client is None:
        return []
    try:
        # Preferred: monthly/expiry-scoped endpoint if present on the client.
        fn = getattr(client, "live_strikes_by_expiry", None)
        if callable(fn):
            resp = fn(ticker=ticker, expiry=expiry, fields=_LIVE_STRIKES_FIELDS)
            rows = list(resp.rows or [])
        else:
            resp = client.live_strikes(ticker=ticker, fields=_LIVE_STRIKES_FIELDS)
            rows = list(resp.rows or [])
    except Exception as e:
        LOG.warning("live_strikes fetch failed for %s %s: %s", ticker, expiry, e)
        return []

    # Filter by target expiry (some endpoints return the full chain).
    target = str(expiry)[:10]
    return [
        r for r in rows
        if isinstance(r, dict) and str(r.get("expirDate") or "")[:10] == target
    ]


def validate_strikes_exist(
    client: Any,
    *,
    ticker: str,
    expiry: str,
    short_put: float,
    long_put: float,
    short_call: float,
    long_call: float,
    tol: float = 0.01,
) -> Dict[str, Any]:
    """Check each leg has an exact strike on the live chain.

    Returns a payload with ``ok`` (all four strikes present), ``missing``
    (list of ``{leg, strike, nearest}`` for missing legs), and
    ``availableStrikes`` (sorted list of live strikes for the expiry).
    """
    rows = _load_live_rows(client, ticker=ticker, expiry=expiry)
    if not rows:
        return {
            "ok": False,
            "expiryFound": False,
            "availableStrikes": [],
            "missing": [],
            "note": (
                "Live chain unavailable for "
                f"{ticker} {expiry}. Strike existence not verified."
            ),
        }

    strikes_set: List[float] = sorted({
        float(s) for s in (_to_float(r.get("strike")) for r in rows) if s is not None
    })
    if not strikes_set:
        return {
            "ok": False,
            "expiryFound": True,
            "availableStrikes": [],
            "missing": [],
            "note": "Live chain returned rows but no usable strike field.",
        }

    legs = _legs_from_strikes(
        short_put=short_put, long_put=long_put,
        short_call=short_call, long_call=long_call,
    )
    missing = []
    for leg in legs:
        if not any(abs(leg.strike - k) <= tol for k in strikes_set):
            nearest = min(strikes_set, key=lambda k: abs(leg.strike - k))
            missing.append({
                "leg": leg.kind, "strike": leg.strike,
                "nearest": nearest,
                "diff": round(abs(leg.strike - nearest), 2),
            })

    return {
        "ok": len(missing) == 0,
        "expiryFound": True,
        "availableStrikes": strikes_set,
        "missing": missing,
        "note": (
            "All four strikes exist on the live chain."
            if not missing else
            f"{len(missing)} leg(s) do not exist for {ticker} {expiry}."
        ),
    }


def _leg_nbbo(row: Dict[str, Any], right: str) -> Dict[str, Optional[float]]:
    if right == "C":
        return {
            "bid": _to_float(row.get("callBidPrice")),
            "ask": _to_float(row.get("callAskPrice")),
        }
    return {
        "bid": _to_float(row.get("putBidPrice")),
        "ask": _to_float(row.get("putAskPrice")),
    }


def fetch_live_chain_nbbo(
    client: Any,
    *,
    ticker: str,
    expiry: str,
    short_put: float,
    long_put: float,
    short_call: float,
    long_call: float,
) -> Optional[Dict[str, Any]]:
    """Return live NBBO snapshot for the four legs plus net credit anchors.

    Shape::

        {
          "asOf":  <ISO timestamp from the chain row, best-effort>,
          "legs": {
            "shortPut": {"strike": 6890, "bid": 0.40, "ask": 0.50, "mid": 0.45, "side":"short"},
            ...
          },
          "netBid":  <credit if we sell at bid, buy at ask — worst case>,
          "netAsk":  <credit if we sell at ask, buy at bid — best case>,
          "mid":     <credit at mids>,
          "source":  "orats_live"
        }

    Sign convention: net credit is positive when we collect premium.
    Returns ``None`` if any leg lacks quotable NBBO data, so the caller
    can fall back to proxies without mixing apples and oranges.
    """
    rows = _load_live_rows(client, ticker=ticker, expiry=expiry)
    if not rows:
        return None

    by_strike = {float(r.get("strike")): r for r in rows if _to_float(r.get("strike")) is not None}
    legs = _legs_from_strikes(
        short_put=short_put, long_put=long_put,
        short_call=short_call, long_call=long_call,
    )

    out_legs: Dict[str, Any] = {}
    sells_mid = 0.0
    buys_mid = 0.0
    sells_bid = 0.0  # worst-case: sell at bid
    buys_ask = 0.0   # worst-case: buy at ask
    sells_ask = 0.0  # best-case: sell at ask
    buys_bid = 0.0   # best-case: buy at bid

    for leg in legs:
        row = by_strike.get(leg.strike)
        if row is None:
            return None
        q = _leg_nbbo(row, leg.right)
        bid, ask = q["bid"], q["ask"]
        if bid is None or ask is None or ask < bid:
            return None
        # Reject a row that has no actual market (0/0 quote is an ORATS
        # placeholder for "no NBBO"). Without this, mid collapses to zero
        # and the net-credit anchors become meaningless.
        if bid <= 0.0 and ask <= 0.0:
            return None
        mid = 0.5 * (bid + ask)
        out_legs[leg.kind] = {
            "strike": leg.strike,
            "side": leg.side,
            "bid": round(bid, 4),
            "ask": round(ask, 4),
            "mid": round(mid, 4),
        }
        if leg.side == "short":
            sells_mid += mid
            sells_bid += bid
            sells_ask += ask
        else:
            buys_mid += mid
            buys_ask += ask
            buys_bid += bid

    return {
        "source": "orats_live",
        "legs": out_legs,
        "mid": round(sells_mid - buys_mid, 4),
        # Worst case for us: sell at the bid, buy at the ask.
        "netBid": round(sells_bid - buys_ask, 4),
        # Best case for us: sell at the ask, buy at the bid.
        "netAsk": round(sells_ask - buys_bid, 4),
    }
