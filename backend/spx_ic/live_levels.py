from __future__ import annotations

import datetime as dt
import logging
import math
from typing import Any, Dict, List, Optional, Tuple

from backend.dealer_gamma_context import compute_dealer_gamma_context
from backend.expected_move import compute_expected_move_from_chain
from backend.oi_clusters import compute_open_interest_clusters
from backend.orats_client import OratsClient, OratsError
from backend.technicals import fetch_live_price_context_optional

from backend.spx_ic.utils import (
    _fmt_date,
    _parse_date,
    _to_float,
    _iv_to_pct,
    _now_et,
    _after_cash_close_et,
    _normalize_expiry_dates,
    _pick_nearest_expiry_date,
    _pick_weekly_close_expiry_date,
    _pick_spot_from_live_rows,
)
from backend.spx_ic.heatmap import (
    _finite,
    compute_spx_net_gex_heatmap,
    _apply_slope,
    _select_expiries_by_dte,
    _bucket_for_dte,
    _bucket_label,
    _exp_decay_weight,
    _weighted_sum_rows,
    _find_accel_boundary_from_spot,
    _classify_stability,
)

LOG = logging.getLogger("spx_ic_engine")


# ---------------------------------------------------------------------------
# Expiry helpers
# ---------------------------------------------------------------------------

def _pick_live_expiry(expirations_rows: List[dict], *, today: dt.date) -> Optional[str]:
    exp_dates: List[str] = []
    for r in expirations_rows or []:
        if not isinstance(r, dict):
            continue
        d0 = str(r.get("expirDate") or r.get("expiry") or r.get("expDate") or r.get("exp_date") or "")[:10]
        if d0 and len(d0) >= 10:
            exp_dates.append(d0)
    return _pick_nearest_expiry_date(exp_dates, today=today)


def _infer_live_expiries_from_strikes(rows: List[dict]) -> List[str]:
    exp_dates: List[str] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d0 = str(r.get("expirDate") or r.get("expiry") or r.get("expDate") or r.get("exp_date") or "")[:10]
        if d0 and len(d0) >= 10:
            exp_dates.append(d0)
    return sorted(list(dict.fromkeys(exp_dates)))


def _select_expiry_from_dates(exp_dates: List[str], *, today: dt.date) -> Optional[str]:
    return _pick_nearest_expiry_date(exp_dates, today=today)


def _filter_chain_by_expiry(rows: List[dict], *, expiry: str) -> List[dict]:
    ex = str(expiry)[:10]
    out: List[dict] = []
    for r in rows or []:
        if not isinstance(r, dict):
            continue
        d0 = str(r.get("expirDate") or r.get("expiry") or r.get("expDate") or r.get("exp_date") or "")[:10]
        if d0 == ex:
            out.append(r)
    return out


def _pick_friday_weekly_expiry(exp_dates: List[str], *, today: dt.date) -> Optional[str]:
    """
    Pick the nearest Friday weekly expiry from available expiration dates.
    Excludes dailies (0DTE, Mon-Thu) - only uses Friday expirations.

    Returns the nearest Friday that is >= today.
    """
    fridays: List[str] = []
    for d in exp_dates:
        try:
            ed = _parse_date(str(d)[:10])
            if ed.weekday() == 4 and ed >= today:
                fridays.append(d)
        except Exception:
            continue

    if not fridays:
        return None

    fridays.sort()
    return fridays[0]


def _imp_to_pct(v: Any) -> Optional[float]:
    x = _to_float(v)
    if x is None:
        return None
    x = abs(float(x))
    return x * 100.0 if x <= 1.0 else x


def _iv_to_weekly_em(iv_annual: Optional[float], dte: int) -> Optional[float]:
    """Convert annualized IV to expected move % for a given DTE."""
    if iv_annual is None or iv_annual <= 0 or dte <= 0:
        return None
    return abs(iv_annual) * math.sqrt(dte / 365.0)


def _pick_iv(row: dict) -> Optional[float]:
    """Pick the best IV value from an ORATS cores row, preferring short-term."""
    for k in ("iv7", "iv7d", "iv7Day", "iv30", "iv30d", "iv30Day", "iv"):
        v = _to_float(row.get(k))
        if v is not None and v > 0:
            return float(v) if v > 1.0 else float(v) * 100.0
    return None


def _fetch_orats_em_snapshot(client: OratsClient, *, ticker: str, today: dt.date, dte: int = 5) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "eodImpliedMovePct": None,
        "eodAsOfDate": None,
        "delayedImpliedMovePct": None,
        "delayedUpdatedAt": None,
        "delayedTradeDate": None,
        "oratsExpectedMovePct": None,
        "oratsExpectedMoveSource": None,
        "warnings": [],
    }

    # Fields to request: impErnMv for stocks with earnings, IV fields for indices
    delayed_fields = "ticker,tradeDate,stockPrice,impErnMv,iv7,iv7d,iv7Day,iv30,iv30d,iv30Day,iv,updatedAt"
    eod_fields = "ticker,tradeDate,stockPrice,impErnMv,iv7,iv7d,iv7Day,iv30,iv30d,iv30Day,iv"

    # --- Delayed snapshot ---
    try:
        fetcher = getattr(client, "cores_delayed", None) or getattr(client, "cores", None)
        if callable(fetcher):
            snap = fetcher(ticker=ticker, fields=delayed_fields)
            snap_row = next((r for r in (snap.rows or []) if isinstance(r, dict)), None)
            if snap_row:
                delayed_pct = _imp_to_pct(snap_row.get("impErnMv"))
                if delayed_pct is not None and delayed_pct > 0:
                    out["delayedImpliedMovePct"] = round(float(delayed_pct), 2)
                else:
                    iv = _pick_iv(snap_row)
                    em_from_iv = _iv_to_weekly_em(iv, dte)
                    if em_from_iv is not None:
                        out["delayedImpliedMovePct"] = round(em_from_iv, 2)
                if out["delayedImpliedMovePct"] is not None:
                    out["delayedUpdatedAt"] = str(snap_row.get("updatedAt") or "")
                    out["delayedTradeDate"] = str(snap_row.get("tradeDate") or "")[:10]
    except Exception as e:
        out["warnings"].append(f"Delayed ORATS EM unavailable: {type(e).__name__}")

    # --- EOD history (walk back up to 8 days) ---
    for i in range(0, 8):
        ds = _fmt_date(today - dt.timedelta(days=i))
        try:
            resp = client.hist_cores(ticker=ticker, trade_date=ds, fields=eod_fields)
            row = next((r for r in (resp.rows or []) if isinstance(r, dict)), None)
            if row:
                eod_pct = _imp_to_pct(row.get("impErnMv"))
                if eod_pct is not None and eod_pct > 0:
                    out["eodImpliedMovePct"] = round(float(eod_pct), 2)
                    out["eodAsOfDate"] = str(row.get("tradeDate") or ds)[:10]
                    break
                iv = _pick_iv(row)
                em_from_iv = _iv_to_weekly_em(iv, dte)
                if em_from_iv is not None:
                    out["eodImpliedMovePct"] = round(em_from_iv, 2)
                    out["eodAsOfDate"] = str(row.get("tradeDate") or ds)[:10]
                    break
        except Exception:
            continue

    if out["delayedImpliedMovePct"] is not None:
        out["oratsExpectedMovePct"] = out["delayedImpliedMovePct"]
        out["oratsExpectedMoveSource"] = "delayed"
    elif out["eodImpliedMovePct"] is not None:
        out["oratsExpectedMovePct"] = out["eodImpliedMovePct"]
        out["oratsExpectedMoveSource"] = "eod"

    return out


# ---------------------------------------------------------------------------
# Expected move (weekly)
# ---------------------------------------------------------------------------

def compute_expected_move_weekly(
    client: OratsClient,
    *,
    ticker: str,
    today: dt.date,
    symbols: Optional[Tuple[str, ...]] = None,
) -> Dict[str, Any]:
    """
    Compute expected move for Engine 2 using ONLY weekly (Friday) options.

    This explicitly excludes dailies (0DTE, Mon-Thu expiries) and only uses
    the nearest Friday weekly expiration for the expected move calculation.

    Args:
        client: OratsClient instance
        ticker: Underlying ticker (SPX, SPY, QQQ)
        today: Current date
        symbols: Optional tuple of symbols to try (e.g., ("SPXW", "SPX", "SPY"))

    Returns:
        Dict with expected move data
    """
    t = str(ticker).strip().upper()
    warnings: List[str] = []

    result: Dict[str, Any] = {
        "ticker": t,
        "asOfDate": today.isoformat(),
        "expiry": None,
        "dte": None,
        "source": None,
        "spotPrice": None,
        "forwardPrice": None,
        "straddlePV": None,
        "expectedMoveDollars": None,
        "expectedMovePct": None,
        "discountFactor": None,
        "strikesUsedForForward": 0,
        "smartSpotPrice": None,
        "smartSpotSource": None,
        "smartSpotMode": None,
        "smartSpotMarketOpen": None,
        "eodImpliedMovePct": None,
        "eodAsOfDate": None,
        "delayedImpliedMovePct": None,
        "delayedUpdatedAt": None,
        "delayedTradeDate": None,
        "oratsExpectedMovePct": None,
        "oratsExpectedMoveSource": None,
        "warnings": [],
        "notes": ["Using weekly (Friday) options only - dailies excluded."],
    }

    if symbols is None:
        if t == "SPX":
            symbols = ("SPXW", "SPX", "SPY")
        elif t == "QQQ":
            symbols = ("QQQ",)
        else:
            symbols = (t,)

    fields = (
        "ticker,tradeDate,expirDate,strike,spotPrice,stockPrice,"
        "callBidPrice,callAskPrice,putBidPrice,putAskPrice,"
        "callOpenInterest,putOpenInterest"
    )

    used_symbol: Optional[str] = None
    exp_date: Optional[dt.date] = None
    chain_rows: List[dict] = []
    spot: Optional[float] = None

    for sym in symbols:
        try:
            exp_dates: List[str] = []
            try:
                exp_resp = client.live_expirations(ticker=sym)
                for r in (exp_resp.rows or []):
                    if isinstance(r, dict):
                        d0 = str(r.get("expirDate") or r.get("expiry") or "")[:10]
                        if d0 and len(d0) >= 10:
                            exp_dates.append(d0)
            except Exception:
                pass

            if not exp_dates:
                try:
                    all_rows = client.live_strikes(ticker=sym, fields=fields).rows or []
                    exp_dates = _infer_live_expiries_from_strikes(all_rows)
                except Exception:
                    continue

            friday_exp = _pick_friday_weekly_expiry(exp_dates, today=today)
            if not friday_exp:
                warnings.append(f"{sym}: No Friday weekly expiry found.")
                continue

            exp_date = _parse_date(friday_exp)

            try:
                resp = client.live_strikes_by_expiry(ticker=sym, expiry=friday_exp, fields=fields)
                chain_rows = [r for r in (resp.rows or []) if isinstance(r, dict)]
            except Exception:
                try:
                    all_rows = client.live_strikes(ticker=sym, fields=fields).rows or []
                    chain_rows = _filter_chain_by_expiry(all_rows, expiry=friday_exp)
                except Exception:
                    chain_rows = []

            if not chain_rows:
                warnings.append(f"{sym}: No chain rows for Friday expiry {friday_exp}.")
                continue

            for r in chain_rows:
                s = _to_float(r.get("spotPrice")) or _to_float(r.get("stockPrice"))
                if s and s > 0:
                    spot = s
                    break

            if spot is None:
                warnings.append(f"{sym}: Could not determine spot price.")
                continue

            used_symbol = sym
            break

        except Exception as e:
            warnings.append(f"{sym}: Error - {type(e).__name__}")
            continue

    if not chain_rows or spot is None or exp_date is None:
        result["warnings"] = warnings + ["No valid weekly Friday chain found."]
        return result

    result["expiry"] = exp_date.isoformat()
    result["dte"] = (exp_date - today).days
    result["spotPrice"] = round(spot, 2)

    # Smart spot (open: live, closed: close) for EM percentage normalization.
    smart_spot_ctx = fetch_live_price_context_optional(client, ticker=t)
    smart_spot = _to_float(smart_spot_ctx.get("price")) if isinstance(smart_spot_ctx, dict) else None
    if smart_spot is None or smart_spot <= 0:
        smart_spot = spot
    if isinstance(smart_spot_ctx, dict):
        result["smartSpotPrice"] = round(float(smart_spot), 2) if smart_spot is not None else None
        result["smartSpotSource"] = smart_spot_ctx.get("source")
        result["smartSpotMode"] = smart_spot_ctx.get("mode")
        result["smartSpotMarketOpen"] = smart_spot_ctx.get("marketOpen")

    em_result = compute_expected_move_from_chain(
        chain_rows,
        spot=float(smart_spot) if (smart_spot is not None and smart_spot > 0) else spot,
        expiry=exp_date,
        as_of=today,
        risk_free_rate=0.05,
    )

    result["source"] = "live"
    result["forwardPrice"] = em_result.get("forwardPrice")
    result["straddlePV"] = em_result.get("straddlePV")
    result["expectedMoveDollars"] = em_result.get("expectedMoveDollars")
    result["expectedMovePct"] = em_result.get("expectedMovePct")
    result["discountFactor"] = em_result.get("discountFactor")
    result["strikesUsedForForward"] = em_result.get("strikesUsedForForward", 0)
    result["symbolUsed"] = used_symbol
    em_snap = _fetch_orats_em_snapshot(client, ticker=t, today=today, dte=max(result.get("dte") or 5, 1))
    result["eodImpliedMovePct"] = em_snap.get("eodImpliedMovePct")
    result["eodAsOfDate"] = em_snap.get("eodAsOfDate")
    result["delayedImpliedMovePct"] = em_snap.get("delayedImpliedMovePct")
    result["delayedUpdatedAt"] = em_snap.get("delayedUpdatedAt")
    result["delayedTradeDate"] = em_snap.get("delayedTradeDate")
    result["oratsExpectedMovePct"] = em_snap.get("oratsExpectedMovePct")
    result["oratsExpectedMoveSource"] = em_snap.get("oratsExpectedMoveSource")
    result["warnings"] = warnings + (em_result.get("warnings") or []) + (em_snap.get("warnings") or [])

    return result


# ---------------------------------------------------------------------------
# Live chain with fallback
# ---------------------------------------------------------------------------

def _live_chain_with_fallback(
    client: OratsClient,
    *,
    tickers: List[str],
    expiry: str,
    fields: str,
) -> Tuple[Optional[str], List[dict], List[str]]:
    warnings: List[str] = []
    for t in tickers:
        try:
            resp = client.live_strikes(ticker=t, fields=fields)
            rows = [r for r in (resp.rows or []) if isinstance(r, dict)]
            ex = str(expiry)[:10]
            filt = [r for r in rows if str(r.get("expirDate") or r.get("expiry") or r.get("expDate") or r.get("exp_date") or "")[:10] == ex]
            if filt:
                return t, filt, warnings
            warnings.append(f"Live chain empty for {t} expiry={ex} (filtered from live_strikes)")
        except Exception as e:
            warnings.append(f"Live chain error for {t}: {type(e).__name__}")
    return None, [], warnings


# ---------------------------------------------------------------------------
# Gamma flip
# ---------------------------------------------------------------------------

def _compute_gamma_flip_strike(
    chain_rows: List[dict],
    *,
    spot: float,
    band_pct: float,
    weighting_mode: str,
    contract_multiplier: int = 100,
) -> Optional[float]:
    """
    Best-effort "gamma flip" proxy: find a strike where net gamma exposure changes sign.

    We compute per-strike netGex ≈ gamma * (callWeight - putWeight) * multiplier
    within a spot band and look for a sign change across adjacent strikes. If found,
    return the flip closest to spot (linear interpolation).
    """
    try:
        s0 = float(spot)
    except Exception:
        return None
    if not math.isfinite(s0) or s0 <= 0:
        return None

    lo = s0 * (1.0 - float(band_pct))
    hi = s0 * (1.0 + float(band_pct))

    pts: List[Tuple[float, float]] = []
    for r in chain_rows or []:
        if not isinstance(r, dict):
            continue
        strike = _to_float(r.get("strike"))
        gamma = _to_float(r.get("gamma"))
        if strike is None or gamma is None:
            continue
        k = float(strike)
        if not (lo <= k <= hi):
            continue
        if weighting_mode == "oi":
            c = _to_float(r.get("callOpenInterest")) or 0.0
            p = _to_float(r.get("putOpenInterest")) or 0.0
        elif weighting_mode == "volume":
            c = _to_float(r.get("callVolume")) or 0.0
            p = _to_float(r.get("putVolume")) or 0.0
        else:
            c = 1.0
            p = 1.0
        c = max(0.0, float(c))
        p = max(0.0, float(p))
        net = float(gamma) * (c - p) * float(contract_multiplier)
        if math.isfinite(net):
            pts.append((k, float(net)))

    if len(pts) < 2:
        return None
    pts.sort(key=lambda x: x[0])

    best = None
    best_dist = None
    prev_k, prev_v = pts[0]
    prev_sign = 0 if prev_v == 0 else (1 if prev_v > 0 else -1)
    for k, v in pts[1:]:
        sign = 0 if v == 0 else (1 if v > 0 else -1)
        if sign == 0:
            flip = float(k)
        elif prev_sign == 0:
            flip = float(prev_k)
        elif sign != prev_sign:
            denom = (v - prev_v)
            if denom == 0:
                flip = float(k)
            else:
                t = (0.0 - prev_v) / denom
                t = max(0.0, min(1.0, float(t)))
                flip = float(prev_k) + t * (float(k) - float(prev_k))
        else:
            flip = None

        if flip is not None and math.isfinite(flip):
            dist = abs(float(flip) - s0)
            if best is None or best_dist is None or dist < best_dist:
                best = float(flip)
                best_dist = float(dist)

        prev_k, prev_v, prev_sign = float(k), float(v), sign

    return best


# ---------------------------------------------------------------------------
# Main live levels
# ---------------------------------------------------------------------------

def compute_live_levels(
    client: OratsClient,
    *,
    underlying: str,
    symbols: Optional[Tuple[str, ...]] = None,
    view: str = "weekly",
    now_dt: Optional[dt.datetime] = None,
    band_pct: float = 0.05,
    top_n: int = 5,
    cluster_steps: int = 2,
    include_heatmap: bool = True,
    heatmap_expiries: int = 30,
    heatmap_band_pct: Optional[float] = None,
    heatmap_mode: str = "net",
    heatmap_view: str = "composite",
    slope_window: int = 5,
    flip_adjacent_n: int = 5,
) -> Dict[str, Any]:
    """
    Compute LIVE dealer-gamma and OI wall/cluster levels (informational).

    view:
      - "weekly": prefer weekly Friday expiry
      - "nearest": prefer nearest expiry / 0DTE
    """
    from backend.spx_ic.ohlc import (
        prior_trading_day,
        fetch_hist_cores_range,
        fetch_atm_iv_pct,
    )

    now_et_val = _now_et(now_dt)
    today = now_et_val.date()

    under = str(underlying or "").strip().upper()
    if not under:
        return {
            "enabled": False,
            "view": "weekly" if str(view).lower().startswith("week") else "nearest",
            "symbolUsed": None,
            "expiry": None,
            "spot": None,
            "bandPct": float(band_pct),
            "weightingMode": None,
            "gammaFlipStrike": None,
            "dealerGamma": None,
            "oiClusters": None,
            "warnings": ["Missing underlying."],
            "notes": ["Live context unavailable (missing underlying)."],
        }

    symbols0 = tuple(
        str(s).strip().upper()
        for s in (symbols or (("SPXW", "SPX", "SPY") if under == "SPX" else (under,)))
        if str(s).strip()
    )
    if not symbols0:
        symbols0 = (under,)
    fields0 = "ticker,tradeDate,expirDate,expiry,expDate,exp_date,strike,spotPrice,stockPrice,gamma,callOpenInterest,putOpenInterest,callVolume,putVolume,callMidIv,putMidIv"

    exp_warn: List[str] = []
    strikes_cache_by_symbol: Dict[str, List[dict]] = {}
    exp_dates_by_symbol: Dict[str, List[str]] = {}

    for sym in symbols0:
        exp_rows: List[dict] = []
        exp_dates: List[str] = []
        try:
            exp_rows = client.live_expirations(ticker=sym).rows or []
        except Exception as e:
            exp_warn.append(f"Live expirations error for {sym}: {type(e).__name__}: {e}")
            exp_rows = []

        if exp_rows:
            for r in exp_rows:
                if not isinstance(r, dict):
                    continue
                d0 = str(r.get("expirDate") or r.get("expiry") or r.get("expDate") or r.get("exp_date") or "")[:10]
                if d0 and len(d0) >= 10:
                    exp_dates.append(d0)
        else:
            try:
                all_rows = client.live_strikes(ticker=sym, fields=fields0).rows or []
                all_rows = [r for r in all_rows if isinstance(r, dict)]
                strikes_cache_by_symbol[sym] = all_rows
                exp_dates = _infer_live_expiries_from_strikes(all_rows)
            except Exception as e:
                exp_warn.append(f"Live strikes fallback error for {sym}: {type(e).__name__}: {e}")
                exp_dates = []

        exp_dates_by_symbol[sym] = exp_dates

    def _pick_expiry_for_symbol(sym: str) -> Optional[str]:
        ds = exp_dates_by_symbol.get(sym) or []
        if str(view).lower().startswith("week"):
            return _pick_weekly_close_expiry_date(ds, today=today, now_dt=now_dt)
        return _pick_nearest_expiry_date(ds, today=today)

    used_symbol = None
    used_expiry = None
    used_exp_dates: List[str] = []
    chain_rows: List[dict] = []
    chain_warn: List[str] = []

    for sym in symbols0:
        ex = _pick_expiry_for_symbol(sym)
        if not ex:
            continue
        used_chain_sym, rows, warn = _live_chain_with_fallback(client, tickers=[sym], expiry=ex, fields=fields0)
        if (not rows) and sym in strikes_cache_by_symbol:
            rows = _filter_chain_by_expiry(strikes_cache_by_symbol.get(sym) or [], expiry=ex)
            if rows:
                warn = [*warn, "Live strikes-by-expiry empty; used full strikes filtered by expiry."]
        if rows:
            used_symbol = used_chain_sym or sym
            used_expiry = ex
            used_exp_dates = exp_dates_by_symbol.get(sym) or []
            chain_rows = rows
            chain_warn = warn
            break

    if not chain_rows:
        return {
            "enabled": False,
            "view": "weekly" if str(view).lower().startswith("week") else "nearest",
            "symbolUsed": None,
            "expiry": None,
            "spot": None,
            "bandPct": float(band_pct),
            "weightingMode": None,
            "gammaFlipStrike": None,
            "dealerGamma": None,
            "oiClusters": None,
            "warnings": exp_warn,
            "notes": ["Live context unavailable (no usable chain rows)."],
        }

    dg = compute_dealer_gamma_context(chain_rows, expiry=used_expiry, contract_multiplier=100, band_pct=float(band_pct), top_n=int(top_n))
    oi = compute_open_interest_clusters(chain_rows, expiry=used_expiry, band_pct=float(band_pct), top_n=int(top_n), cluster_steps=int(cluster_steps))
    spot = dg.get("spot")
    wmode = str(dg.get("weightingMode") or "oi")
    flip = None
    if spot is not None:
        flip = _compute_gamma_flip_strike(chain_rows, spot=float(spot), band_pct=float(band_pct), weighting_mode=wmode, contract_multiplier=100)

    gex_heatmap = None
    if bool(include_heatmap) and used_symbol:
        try:
            band_h = float(heatmap_band_pct) if (heatmap_band_pct is not None) else float(band_pct)

            all_rows = strikes_cache_by_symbol.get(used_symbol)
            if all_rows is None:
                all_rows = [r for r in (client.live_strikes(ticker=used_symbol, fields=fields0).rows or []) if isinstance(r, dict)]

            s0 = _pick_spot_from_live_rows(all_rows) or (float(spot) if spot is not None else None)
            if s0 is not None:
                exp_dtes = _select_expiries_by_dte(used_exp_dates, today=today, dte_max=45, cap=int(heatmap_expiries))
                exp_window = [e for (e, _) in exp_dtes]
                dte_by_exp = {e: int(dte) for (e, dte) in exp_dtes}

                raw = compute_spx_net_gex_heatmap(
                    all_rows,
                    expiries=exp_window,
                    spot=float(s0),
                    band_pct=float(band_h),
                    contract_multiplier=100,
                    strike_cap=180,
                )

                strikes = [float(x) for x in (raw.get("strikes") or [])]
                raw_rows = raw.get("netDollarGex") if isinstance(raw.get("netDollarGex"), list) else []
                raw_rows = [r if isinstance(r, list) else [] for r in raw_rows]

                slope_rows = [_apply_slope([_finite(x) for x in row], window=int(slope_window)) for row in raw_rows]

                iv_notes: List[str] = []
                iv_used_pct = None
                iv_trade = prior_trading_day(client, ticker=under, date=today) or today
                try:
                    fields_iv = "ticker,tradeDate,iv30,iv30d,iv30Day,iv"
                    core_rows = fetch_hist_cores_range(client, ticker=under, start=iv_trade - dt.timedelta(days=30), end=iv_trade, fields=fields_iv)
                    core_rows = [r for r in core_rows if isinstance(r, dict)]
                    core_rows.sort(key=lambda r: str(r.get("tradeDate") or "")[:10])
                    best_iv = None
                    for r in reversed(core_rows):
                        for k in ("iv30", "iv30d", "iv30Day", "iv"):
                            best_iv = _iv_to_pct(r.get(k))
                            if best_iv is not None:
                                break
                        if best_iv is not None:
                            break
                    iv_used_pct = best_iv
                except Exception:
                    iv_used_pct = None

                if iv_used_pct is None:
                    try:
                        iv_used_pct = fetch_atm_iv_pct(client, ticker=under, trade_date=iv_trade, dte_target=30)
                        if iv_used_pct is not None:
                            iv_notes.append("ATM IV proxy fell back to monies-implied vol50 (30DTE).")
                    except Exception:
                        iv_used_pct = None

                iv_dec = (float(iv_used_pct) / 100.0) if (iv_used_pct is not None and float(iv_used_pct) > 0) else None
                scale_denom = (float(s0) * float(iv_dec)) if (iv_dec is not None) else None
                if scale_denom is None:
                    iv_notes.append("ATM IV proxy unavailable; normalization disabled (raw color scaling).")

                base_weights = {"0_5": 1.0, "6_10": 0.6, "20_40": 0.25}
                half_life = 2.0
                expiries_used: List[dict] = []
                for e in exp_window:
                    dte = dte_by_exp.get(e)
                    b = _bucket_for_dte(int(dte)) if dte is not None else None
                    if b is None:
                        continue
                    w = float(base_weights.get(b, 0.0)) * _exp_decay_weight(dte=int(dte), half_life_dte=float(half_life))
                    expiries_used.append({"expiry": e, "dte": int(dte), "bucket": b, "weight": round(float(w), 8)})

                buckets_out: List[dict] = []
                bucket_keys = ["0_5", "6_10", "20_40"]
                for b in bucket_keys:
                    idxs: List[int] = []
                    ws: List[float] = []
                    dtes_for_bucket: List[int] = []
                    for i, e in enumerate(exp_window):
                        dte = dte_by_exp.get(e)
                        if dte is None:
                            continue
                        if _bucket_for_dte(int(dte)) != b:
                            continue
                        w = float(base_weights.get(b, 0.0)) * _exp_decay_weight(dte=int(dte), half_life_dte=float(half_life))
                        if w <= 0:
                            continue
                        idxs.append(i)
                        ws.append(float(w))
                        dtes_for_bucket.append(int(dte))

                    rows_sel = [raw_rows[i] for i in idxs]
                    net_row = _weighted_sum_rows(rows=rows_sel, weights=ws) if rows_sel else [None] * len(strikes)
                    slope_row = _apply_slope([_finite(x) for x in net_row], window=int(slope_window)) if net_row else [None] * len(strikes)

                    den = sum(ws) if ws else 0.0
                    eff_dte = (sum(float(w) * float(d) for (w, d) in zip(ws, dtes_for_bucket)) / den) if den > 1e-12 else None
                    em_pts = None
                    if iv_dec is not None and eff_dte is not None and float(eff_dte) > 0:
                        em_pts = float(s0) * float(iv_dec) * math.sqrt(float(eff_dte) / 252.0)

                    buckets_out.append(
                        {
                            "key": b,
                            "label": _bucket_label(b),
                            "effectiveDte": None if eff_dte is None else round(float(eff_dte), 4),
                            "expectedMovePts": None if em_pts is None else round(float(em_pts), 4),
                            "rowsUsed": len(rows_sel),
                            "netDollarGex": [None if x is None else round(float(x), 6) for x in net_row],
                            "slopeNetDollarGex": [None if x is None else round(float(x), 6) for x in slope_row],
                        }
                    )

                b0 = next((x for x in buckets_out if x.get("key") == "0_5"), None) or {}
                vals0 = [(_finite(x) if x is not None else None) for x in (b0.get("netDollarGex") or [])]
                em0 = _finite(b0.get("expectedMovePts"))
                down_strike = _find_accel_boundary_from_spot(strikes=strikes, vals=vals0, spot=float(s0), side="down", adjacent_n=int(flip_adjacent_n))
                up_strike = _find_accel_boundary_from_spot(strikes=strikes, vals=vals0, spot=float(s0), side="up", adjacent_n=int(flip_adjacent_n))

                down_pts = (float(s0) - float(down_strike)) if (down_strike is not None) else None
                up_pts = (float(up_strike) - float(s0)) if (up_strike is not None) else None
                down_em = (float(down_pts) / float(em0)) if (down_pts is not None and em0 is not None and em0 > 1e-9) else None
                up_em = (float(up_pts) / float(em0)) if (up_pts is not None and em0 is not None and em0 > 1e-9) else None

                stability = _classify_stability(
                    strikes=strikes,
                    vals0_5=vals0,
                    spot=float(s0),
                    em_pts=em0,
                    downside_em=down_em,
                    upside_em=up_em,
                    fragile_band_em=0.75,
                    asym_diff_em=0.5,
                )

                gex_heatmap = {
                    "enabled": True,
                    "display": {"view": str(heatmap_view or "composite"), "mode": str(heatmap_mode or "net")},
                    "spot": round(float(s0), 6),
                    "bandPct": float(band_h),
                    "weightingMode": raw.get("weightingMode"),
                    "contractMultiplier": int(raw.get("contractMultiplier") or 100),
                    "atmIvUsedPct": None if iv_used_pct is None else round(float(iv_used_pct), 4),
                    "scaleDenom": None if scale_denom is None else round(float(scale_denom), 8),
                    "raw": {
                        "expiries": raw.get("expiries") or [],
                        "dteByExpiry": dte_by_exp,
                        "strikes": raw.get("strikes") or [],
                        "netDollarGex": raw.get("netDollarGex") or [],
                        "slopeNetDollarGex": [[None if x is None else round(float(x), 6) for x in row] for row in slope_rows],
                    },
                    "composite": {
                        "halfLifeDte": float(half_life),
                        "baseWeights": base_weights,
                        "expiriesUsed": expiries_used,
                        "strikes": raw.get("strikes") or [],
                        "buckets": buckets_out,
                    },
                    "boundaries": {
                        "flipAdjacentN": int(flip_adjacent_n),
                        "downsideAccelerationBoundaryStrike": None if down_strike is None else round(float(down_strike), 2),
                        "upsideAccelerationBoundaryStrike": None if up_strike is None else round(float(up_strike), 2),
                    },
                    "metrics": {
                        "downsideDistancePts": None if down_pts is None else round(float(down_pts), 2),
                        "upsideDistancePts": None if up_pts is None else round(float(up_pts), 2),
                        "downsideDistanceEm": None if down_em is None else round(float(down_em), 4),
                        "upsideDistanceEm": None if up_em is None else round(float(up_em), 4),
                    },
                    "stability": stability,
                    "warnings": [*list(raw.get("warnings") or []), *iv_notes],
                    "notes": [
                        "Slope is computed on raw Net $GEX; normalization applies only at render time.",
                        *list(raw.get("notes") or []),
                    ],
                }
        except Exception as e:
            LOG.exception("Failed to compute SPX net $GEX heatmap")
            gex_heatmap = {
                "enabled": False,
                "error": f"{type(e).__name__}",
                "notes": ["Heatmap unavailable (best-effort)."],
            }

    notes_out = [
        "Live, informational only. Does not change backtest/odds.",
        "Best-effort: depends on entitlement, session timing, and chain coverage.",
    ]
    if under == "SPX":
        notes_out.append("SPXW/SPX may differ from SPY proxy intraday depending on entitlement and session.")

    return {
        "enabled": True,
        "view": "weekly" if str(view).lower().startswith("week") else "nearest",
        "symbolUsed": used_symbol,
        "expiry": str(used_expiry)[:10] if used_expiry else None,
        "spot": dg.get("spot"),
        "bandPct": float(band_pct),
        "weightingMode": wmode,
        "gammaFlipStrike": None if flip is None else round(float(flip), 2),
        "dealerGamma": dg,
        "oiClusters": oi,
        "gexHeatmap": gex_heatmap,
        "warnings": [*exp_warn, *list(chain_warn or []), *list(dg.get("warnings") or []), *list(oi.get("warnings") or [])],
        "notes": notes_out,
    }


def compute_spx_live_levels(
    client: OratsClient,
    *,
    view: str = "weekly",
    now_dt: Optional[dt.datetime] = None,
    band_pct: float = 0.05,
    top_n: int = 5,
    cluster_steps: int = 2,
    include_heatmap: bool = True,
    heatmap_expiries: int = 30,
    heatmap_band_pct: Optional[float] = None,
    heatmap_mode: str = "net",
    heatmap_view: str = "composite",
    slope_window: int = 5,
    flip_adjacent_n: int = 5,
) -> Dict[str, Any]:
    """
    Back-compat wrapper for older callers.
    """
    return compute_live_levels(
        client,
        underlying="SPX",
        symbols=("SPXW", "SPX"),
        view=view,
        now_dt=now_dt,
        band_pct=band_pct,
        top_n=top_n,
        cluster_steps=cluster_steps,
        include_heatmap=include_heatmap,
        heatmap_expiries=heatmap_expiries,
        heatmap_band_pct=heatmap_band_pct,
        heatmap_mode=heatmap_mode,
        heatmap_view=heatmap_view,
        slope_window=slope_window,
        flip_adjacent_n=flip_adjacent_n,
    )
