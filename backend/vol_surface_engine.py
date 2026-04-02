"""Intraday Vol Surface Engine.

Monitors option-implied volatility surface for SPX/SPY in real-time using
ORATS live strikes. Tracks skew, term structure, and put/call vol ratio
to provide actionable signals for Engines 2 and 12.

Key outputs:
- Put/call IV ratio (skew indicator)
- Term structure slope (contango/backwardation)
- ATM IV level and percentile
- Surface anomaly flags (inverted term structure, extreme skew, etc.)
"""
from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

_LOG = logging.getLogger(__name__)

_CACHE_KEY = "vol_surface:latest"
_HISTORY_KEY = "vol_surface:history"


@dataclass
class ExpirySlice:
    """IV data for one expiration."""
    expiry: str
    dte: int
    atm_iv: float
    put_25d_iv: float
    call_25d_iv: float
    put_call_ratio: float
    skew_25d: float
    strike_count: int = 0


@dataclass
class VolSurface:
    """Snapshot of the vol surface."""
    ticker: str = "SPY"
    as_of: str = ""
    spot: float = 0.0
    atm_iv: float = 0.0
    iv_percentile_30d: Optional[float] = None
    term_structure_slope: float = 0.0
    term_structure_label: str = "flat"
    put_call_ratio: float = 1.0
    skew_25d: float = 0.0
    skew_label: str = "normal"
    slices: List[ExpirySlice] = field(default_factory=list)
    anomalies: List[str] = field(default_factory=list)
    signal_strength: float = 0.0

    def to_dict(self) -> dict:
        d = asdict(self)
        d["slices"] = [asdict(s) for s in self.slices]
        return d


def _find_atm_strikes(rows: List[dict], spot: float) -> List[dict]:
    """Find strikes closest to spot price."""
    if not rows:
        return []
    sorted_rows = sorted(rows, key=lambda r: abs(float(r.get("strike", 0)) - spot))
    return sorted_rows[:4]


def _find_delta_strikes(
    rows: List[dict], target_delta: float, option_type: str = "put",
) -> Optional[dict]:
    """Find strike closest to target delta."""
    delta_key = "callDelta" if option_type == "call" else "putDelta"
    candidates = []
    for r in rows:
        d = r.get(delta_key)
        if d is not None:
            try:
                candidates.append((abs(float(d) - target_delta), r))
            except (TypeError, ValueError):
                pass
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0])
    return candidates[0][1]


def compute_expiry_slice(
    rows: List[dict], expiry: str, spot: float,
) -> Optional[ExpirySlice]:
    """Compute IV metrics for one expiration from ORATS strike data."""
    if not rows or spot <= 0:
        return None

    atm_strikes = _find_atm_strikes(rows, spot)
    if not atm_strikes:
        return None

    ivs = [float(r.get("callMidIv") or r.get("iv") or 0) for r in atm_strikes]
    ivs = [v for v in ivs if v > 0]
    atm_iv = sum(ivs) / len(ivs) if ivs else 0.0

    put_25d = _find_delta_strikes(rows, -0.25, "put")
    call_25d = _find_delta_strikes(rows, 0.25, "call")

    put_25d_iv = float(put_25d.get("putMidIv", 0) or 0) if put_25d else atm_iv
    call_25d_iv = float(call_25d.get("callMidIv", 0) or 0) if call_25d else atm_iv

    put_call_ratio = put_25d_iv / call_25d_iv if call_25d_iv > 0.001 else 1.0
    skew = put_25d_iv - call_25d_iv

    dte_val = 0
    if rows:
        try:
            exp_date = dt.date.fromisoformat(expiry[:10])
            dte_val = (exp_date - dt.date.today()).days
        except (ValueError, TypeError):
            pass

    return ExpirySlice(
        expiry=expiry,
        dte=dte_val,
        atm_iv=round(atm_iv, 4),
        put_25d_iv=round(put_25d_iv, 4),
        call_25d_iv=round(call_25d_iv, 4),
        put_call_ratio=round(put_call_ratio, 4),
        skew_25d=round(skew, 4),
        strike_count=len(rows),
    )


def compute_vol_surface(
    orats_client: Any,
    *,
    ticker: str = "SPY",
    spot: Optional[float] = None,
) -> VolSurface:
    """Build a full vol surface snapshot from ORATS live data.

    Args:
        orats_client: OratsClient instance.
        ticker: Underlying ticker (SPY/SPX).
        spot: Current spot price (fetched from ORATS if not provided).
    """
    surface = VolSurface(ticker=ticker, as_of=dt.datetime.utcnow().isoformat() + "Z")

    try:
        summary_resp = orats_client.live_summaries(ticker=ticker)
        summary_rows = summary_resp.rows or []
        if summary_rows:
            row0 = summary_rows[0]
            surface.spot = float(row0.get("stockPrice") or spot or 0)
            surface.atm_iv = float(row0.get("ivMean") or row0.get("iv30dMean") or 0)
    except Exception as exc:
        _LOG.warning("Vol surface: failed to fetch summaries for %s: %s", ticker, exc)
        if spot:
            surface.spot = spot

    if surface.spot <= 0:
        _LOG.warning("Vol surface: no spot price available for %s", ticker)
        return surface

    try:
        exp_resp = orats_client.live_expirations(ticker=ticker)
        expirations = [str(r.get("expirDate", "")) for r in (exp_resp.rows or []) if r.get("expirDate")]
    except Exception as exc:
        _LOG.warning("Vol surface: failed to fetch expirations for %s: %s", ticker, exc)
        return surface

    today = dt.date.today()
    near_exps = []
    for exp_str in expirations:
        try:
            exp_date = dt.date.fromisoformat(exp_str[:10])
            dte = (exp_date - today).days
            if 2 <= dte <= 90:
                near_exps.append((dte, exp_str))
        except (ValueError, TypeError):
            pass
    near_exps.sort()
    near_exps = near_exps[:6]

    slices: List[ExpirySlice] = []
    for dte, exp_str in near_exps:
        try:
            strikes_resp = orats_client.live_strikes_by_expiry(
                ticker=ticker, expiry=exp_str,
                fields="strike,callMidIv,putMidIv,callDelta,putDelta",
            )
            strike_rows = strikes_resp.rows or []
            sl = compute_expiry_slice(strike_rows, exp_str, surface.spot)
            if sl:
                slices.append(sl)
        except Exception as exc:
            _LOG.debug("Vol surface: failed to fetch strikes for %s %s: %s", ticker, exp_str, exc)

    surface.slices = slices

    if len(slices) >= 2:
        front = slices[0]
        back = slices[-1]
        if front.dte > 0 and back.dte > front.dte:
            slope = (back.atm_iv - front.atm_iv) / (back.dte - front.dte) * 30
            surface.term_structure_slope = round(slope, 4)
            if slope < -0.005:
                surface.term_structure_label = "backwardation"
            elif slope > 0.005:
                surface.term_structure_label = "contango"
            else:
                surface.term_structure_label = "flat"

    if slices:
        front = slices[0]
        surface.atm_iv = front.atm_iv
        surface.put_call_ratio = front.put_call_ratio
        surface.skew_25d = front.skew_25d

        if front.skew_25d > 0.08:
            surface.skew_label = "extreme_put_skew"
        elif front.skew_25d > 0.04:
            surface.skew_label = "elevated_put_skew"
        elif front.skew_25d < -0.02:
            surface.skew_label = "call_skew"
        else:
            surface.skew_label = "normal"

    # Anomaly detection
    anomalies: List[str] = []
    if surface.term_structure_label == "backwardation":
        anomalies.append("inverted_term_structure")
    if surface.skew_label == "extreme_put_skew":
        anomalies.append("extreme_put_demand")
    if surface.put_call_ratio > 1.3:
        anomalies.append("high_put_call_ratio")
    if surface.atm_iv > 0.30:
        anomalies.append("elevated_iv_level")
    surface.anomalies = anomalies

    signal_score = len(anomalies) * 25.0
    if surface.term_structure_label == "backwardation":
        signal_score += 15.0
    surface.signal_strength = min(100.0, round(signal_score, 1))

    return surface


def get_vol_surface(
    orats_client: Any,
    *,
    ticker: str = "SPY",
    store: Any = None,
    cache_ttl_s: int = 300,
    spot: Optional[float] = None,
) -> VolSurface:
    """High-level entry with Redis caching."""
    cache_key = f"{_CACHE_KEY}:{ticker}"

    if store is not None:
        try:
            cached = store.get_json(cache_key)
            if cached is not None:
                _LOG.debug("Vol surface cache hit: %s", cache_key)
                return VolSurface(**{k: v for k, v in cached.items()
                                     if k in VolSurface.__dataclass_fields__ and k != "slices"})
        except Exception:
            pass

    surface = compute_vol_surface(orats_client, ticker=ticker, spot=spot)

    if store is not None and surface.spot > 0:
        try:
            store.set_json(cache_key, surface.to_dict(), ttl_s=cache_ttl_s)
        except Exception:
            pass

    return surface
