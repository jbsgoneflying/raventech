"""Engine 13 — Gap Regime Scanner.

After a large SPX gap move, analyses historical gap analogues, options
microstructure, technicals, and VIX behaviour to produce scenario
probabilities (continuation / consolidation / reversion) and feeds an
LLM desk note advising HOLD / ROLL / ADJUST for short premium positions.
"""
from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)

_CLAMP = lambda lo, hi, v: max(lo, min(hi, v))


# ---------------------------------------------------------------------------
# Layer 1 — Gap Characterisation
# ---------------------------------------------------------------------------

def _compute_gap_pct(bars: List[Any], live_price: Optional[float] = None) -> Optional[float]:
    """Return today's open-to-prior-close gap %."""
    if len(bars) < 2:
        return None
    prev = bars[-2]
    curr = bars[-1]
    prev_close = getattr(prev, "close", None) or (prev.get("close") if isinstance(prev, dict) else None)
    curr_open = getattr(curr, "open", None) or (curr.get("open") if isinstance(curr, dict) else None)
    if prev_close is None or curr_open is None or prev_close == 0:
        return None
    return (float(curr_open) - float(prev_close)) / float(prev_close) * 100.0


def _gap_percentile(gap_pct: float, all_gaps: List[float]) -> float:
    """Where does *gap_pct* rank in the historical gap distribution? (0-100)"""
    if not all_gaps:
        return 50.0
    count_below = sum(1 for g in all_gaps if abs(g) < abs(gap_pct))
    return round(count_below / len(all_gaps) * 100.0, 1)


def characterise_gap(
    bars: List[Any],
    *,
    live_price: Optional[float] = None,
    catalyst_tag: str = "unknown",
) -> Dict[str, Any]:
    """Layer 1: characterise today's gap."""
    gap_pct = _compute_gap_pct(bars, live_price)
    if gap_pct is None:
        return {"enabled": False, "notes": ["Insufficient bar data for gap calculation"]}

    prev_close = None
    if len(bars) >= 2:
        b = bars[-2]
        prev_close = float(getattr(b, "close", None) or (b.get("close") if isinstance(b, dict) else None) or 0)

    curr_open = None
    if bars:
        b = bars[-1]
        curr_open = float(getattr(b, "open", None) or (b.get("open") if isinstance(b, dict) else None) or 0)

    all_gaps = _compute_all_gaps(bars)
    pct_rank = _gap_percentile(gap_pct, all_gaps)
    direction = "up" if gap_pct > 0 else "down"

    gap_fill_pct: Optional[float] = None
    if live_price is not None and prev_close and curr_open and gap_pct != 0:
        if direction == "up":
            total_gap = curr_open - prev_close
            retraced = max(0.0, curr_open - live_price)
            gap_fill_pct = round(min(100.0, retraced / total_gap * 100.0) if total_gap > 0 else 0.0, 1)
        else:
            total_gap = prev_close - curr_open
            retraced = max(0.0, live_price - curr_open)
            gap_fill_pct = round(min(100.0, retraced / total_gap * 100.0) if total_gap > 0 else 0.0, 1)

    return {
        "enabled": True,
        "gapPct": round(gap_pct, 3),
        "absGapPct": round(abs(gap_pct), 3),
        "direction": direction,
        "percentileRank": pct_rank,
        "prevClose": round(prev_close, 2) if prev_close else None,
        "todayOpen": round(curr_open, 2) if curr_open else None,
        "livePrice": round(live_price, 2) if live_price else None,
        "gapFillPct": gap_fill_pct,
        "catalystTag": catalyst_tag,
    }


def _compute_all_gaps(bars: List[Any]) -> List[float]:
    """Compute all daily gap %s from a bar series."""
    gaps = []
    for i in range(1, len(bars)):
        prev = bars[i - 1]
        curr = bars[i]
        pc = getattr(prev, "close", None) or (prev.get("close") if isinstance(prev, dict) else None)
        co = getattr(curr, "open", None) or (curr.get("open") if isinstance(curr, dict) else None)
        if pc and co and float(pc) > 0:
            gaps.append((float(co) - float(pc)) / float(pc) * 100.0)
    return gaps


# ---------------------------------------------------------------------------
# Layer 2 — Historical Gap Analogues
# ---------------------------------------------------------------------------

def _bar_close(bar: Any) -> Optional[float]:
    v = getattr(bar, "close", None) or (bar.get("close") if isinstance(bar, dict) else None)
    return float(v) if v is not None else None


def _bar_open(bar: Any) -> Optional[float]:
    v = getattr(bar, "open", None) or (bar.get("open") if isinstance(bar, dict) else None)
    return float(v) if v is not None else None


def _bar_date(bar: Any) -> Optional[str]:
    v = getattr(bar, "trade_date", None) or (bar.get("trade_date") if isinstance(bar, dict) else None)
    return str(v)[:10] if v else None


def compute_historical_gap_analogues(
    bars: List[Any],
    *,
    gap_threshold_pct: float = 1.5,
    direction_filter: Optional[str] = None,
    max_analogues: int = 50,
) -> Dict[str, Any]:
    """Layer 2: find historical gap events and their forward outcomes."""
    if len(bars) < 30:
        return {"enabled": False, "notes": ["Need at least 30 bars for gap analysis"]}

    gap_events: List[Dict[str, Any]] = []
    for i in range(1, len(bars) - 5):
        prev_c = _bar_close(bars[i - 1])
        curr_o = _bar_open(bars[i])
        if not prev_c or not curr_o or prev_c == 0:
            continue
        gap = (curr_o - prev_c) / prev_c * 100.0
        if abs(gap) < gap_threshold_pct:
            continue
        if direction_filter == "up" and gap <= 0:
            continue
        if direction_filter == "down" and gap >= 0:
            continue

        fwd = {}
        close_d0 = _bar_close(bars[i])
        for d in (1, 2, 3, 5):
            idx = i + d
            if idx < len(bars) and close_d0:
                fc = _bar_close(bars[idx])
                if fc:
                    fwd[f"d{d}"] = round((fc - close_d0) / close_d0 * 100.0, 3)

        outcome = "consolidation"
        d5 = fwd.get("d5")
        if d5 is not None:
            if gap > 0:
                if d5 > 0.5:
                    outcome = "continuation"
                elif d5 < -0.5:
                    outcome = "reversion"
            else:
                if d5 < -0.5:
                    outcome = "continuation"
                elif d5 > 0.5:
                    outcome = "reversion"

        intraday_fill = None
        if close_d0 and curr_o and prev_c and gap != 0:
            if gap > 0:
                fill = max(0.0, curr_o - close_d0) / max(1e-9, curr_o - prev_c)
                intraday_fill = round(min(100.0, fill * 100.0), 1)
            else:
                fill = max(0.0, close_d0 - curr_o) / max(1e-9, prev_c - curr_o)
                intraday_fill = round(min(100.0, fill * 100.0), 1)

        gap_events.append({
            "date": _bar_date(bars[i]),
            "gapPct": round(gap, 3),
            "forwardReturns": fwd,
            "outcome": outcome,
            "intradayGapFill": intraday_fill,
        })

    gap_events.sort(key=lambda e: abs(e["gapPct"]), reverse=True)
    gap_events = gap_events[:max_analogues]

    n = len(gap_events)
    if n == 0:
        return {"enabled": True, "count": 0, "events": [], "stats": None}

    d1_returns = [e["forwardReturns"]["d1"] for e in gap_events if "d1" in e["forwardReturns"]]
    d3_returns = [e["forwardReturns"]["d3"] for e in gap_events if "d3" in e["forwardReturns"]]
    d5_returns = [e["forwardReturns"]["d5"] for e in gap_events if "d5" in e["forwardReturns"]]

    def _stats(values: List[float]) -> Optional[Dict[str, float]]:
        if not values:
            return None
        s = sorted(values)
        n_v = len(s)
        return {
            "median": round(statistics.median(s), 3),
            "mean": round(statistics.mean(s), 3),
            "p25": round(s[max(0, n_v // 4 - 1)], 3),
            "p75": round(s[min(n_v - 1, 3 * n_v // 4)], 3),
            "min": round(s[0], 3),
            "max": round(s[-1], 3),
            "n": n_v,
        }

    outcomes = [e["outcome"] for e in gap_events]
    n_cont = outcomes.count("continuation")
    n_rev = outcomes.count("reversion")
    n_cons = outcomes.count("consolidation")
    fills = [e["intradayGapFill"] for e in gap_events if e["intradayGapFill"] is not None]

    return {
        "enabled": True,
        "count": n,
        "thresholdPct": gap_threshold_pct,
        "directionFilter": direction_filter,
        "events": gap_events[:15],
        "stats": {
            "d1": _stats(d1_returns),
            "d3": _stats(d3_returns),
            "d5": _stats(d5_returns),
        },
        "outcomeDistribution": {
            "continuation": round(n_cont / n * 100.0, 1) if n else 0,
            "consolidation": round(n_cons / n * 100.0, 1) if n else 0,
            "reversion": round(n_rev / n * 100.0, 1) if n else 0,
        },
        "medianIntradayGapFill": round(statistics.median(fills), 1) if fills else None,
    }


# ---------------------------------------------------------------------------
# Layer 3 — Options Microstructure
# ---------------------------------------------------------------------------

_STRIKES_FIELDS = (
    "ticker,tradeDate,expirDate,expiry,expDate,exp_date,strike,"
    "spotPrice,stockPrice,gamma,theta,vega,"
    "callOpenInterest,putOpenInterest,callVolume,putVolume,"
    "callMidIv,putMidIv,callDelta,putDelta"
)


def _infer_nearest_expiry(rows: List[Dict]) -> Optional[str]:
    """Pick the nearest expiry with decent OI from a raw strikes payload."""
    from collections import Counter
    exp_counter: Counter = Counter()
    for r in rows:
        if not isinstance(r, dict):
            continue
        exp = str(r.get("expirDate") or r.get("expiry") or r.get("expDate")
                  or r.get("exp_date") or "")[:10]
        if exp:
            exp_counter[exp] += 1
    if not exp_counter:
        return None
    today_str = dt.date.today().isoformat()
    future = sorted(e for e in exp_counter if e >= today_str)
    return future[0] if future else None


def compute_options_microstructure(
    orats: Any,
    *,
    ticker: str = "SPX",
    benzinga: Any = None,
) -> Dict[str, Any]:
    """Layer 3: dealer gamma, skew, term structure, OI clusters, unusual flow."""
    result: Dict[str, Any] = {
        "dealerGamma": None,
        "skew": None,
        "termStructure": None,
        "oiClusters": None,
        "unusualFlow": None,
    }
    if orats is None:
        return result

    spx_symbols = ("SPXW", "SPX", "SPY") if ticker in ("SPX", "SPXW") else (ticker,)

    # Dealer gamma + OI clusters from live strikes
    all_strikes: List[Dict] = []
    try:
        for sym in spx_symbols:
            try:
                resp = orats.live_strikes(ticker=sym, fields=_STRIKES_FIELDS)
                rows = [r for r in (resp.rows if resp else []) if isinstance(r, dict)]
                if rows:
                    all_strikes.extend(rows)
                    break  # use first symbol that returns data
            except Exception:
                pass

        if all_strikes:
            expiry = _infer_nearest_expiry(all_strikes)
            chain_rows = all_strikes
            if expiry:
                chain_rows = [
                    r for r in all_strikes
                    if str(r.get("expirDate") or r.get("expiry") or r.get("expDate")
                           or r.get("exp_date") or "")[:10] == expiry
                ] or all_strikes

            from backend.dealer_gamma_context import compute_dealer_gamma_context
            result["dealerGamma"] = compute_dealer_gamma_context(
                chain_rows, expiry=expiry, contract_multiplier=100,
                band_pct=0.05, top_n=5,
            )

            from backend.oi_clusters import compute_open_interest_clusters
            result["oiClusters"] = compute_open_interest_clusters(
                chain_rows, expiry=expiry, band_pct=0.05, top_n=5,
            )
    except Exception as exc:
        LOG.debug("Engine13 dealer gamma / OI: %s", exc)

    # Skew + term structure from vol surface
    try:
        from backend.vol_surface_engine import compute_vol_surface
        vs = compute_vol_surface(orats, ticker=ticker if ticker != "SPXW" else "SPX")
        vs_dict = vs.to_dict()
        result["skew"] = {
            "skew25d": vs_dict.get("skew_25d"),
            "putCallRatio": vs_dict.get("put_call_ratio"),
            "label": vs_dict.get("skew_label"),
            "atmIv": vs_dict.get("atm_iv"),
        }
        result["termStructure"] = {
            "slope": vs_dict.get("term_structure_slope"),
            "label": vs_dict.get("term_structure_label"),
            "slices": [
                {
                    "dte": s.get("dte"),
                    "atmIv": s.get("atm_iv"),
                    "skew25d": s.get("skew_25d"),
                }
                for s in (vs_dict.get("slices") or [])
            ],
        }
    except Exception as exc:
        LOG.debug("Engine13 vol surface: %s", exc)

    # Unusual option activity — prefer Benzinga signals, fall back to ORATS volume proxy
    if benzinga is not None:
        try:
            today = dt.date.today().isoformat()
            resp = benzinga.signal_option_activity(tickers="SPY,SPX", date=today, pagesize=50)
            rows = resp.rows if resp else []
            calls = [r for r in rows if str(r.get("put_call", "")).upper() == "CALL"]
            puts = [r for r in rows if str(r.get("put_call", "")).upper() == "PUT"]
            sweeps = [r for r in rows if str(r.get("aggressor_ind", "")).lower() in ("buy", "sell")]
            result["unusualFlow"] = {
                "totalSignals": len(rows),
                "calls": len(calls),
                "puts": len(puts),
                "sweeps": len(sweeps),
                "netSentiment": "bullish" if len(calls) > len(puts) * 1.3 else (
                    "bearish" if len(puts) > len(calls) * 1.3 else "mixed"
                ),
                "topItems": [
                    {
                        "ticker": r.get("ticker"),
                        "putCall": r.get("put_call"),
                        "strike": r.get("strike_price"),
                        "expiry": str(r.get("date_expiration", ""))[:10],
                        "sentiment": r.get("sentiment"),
                        "aggressorInd": r.get("aggressor_ind"),
                        "cost": r.get("cost_basis"),
                    }
                    for r in rows[:10]
                ],
            }
        except Exception as exc:
            LOG.debug("Engine13 unusual flow (Benzinga): %s", exc)

    # ORATS volume proxy when Benzinga is unavailable or returned nothing
    if result["unusualFlow"] is None and all_strikes:
        try:
            call_vol = sum(int(r.get("callVolume") or 0) for r in all_strikes)
            put_vol = sum(int(r.get("putVolume") or 0) for r in all_strikes)
            call_oi = sum(int(r.get("callOpenInterest") or 0) for r in all_strikes)
            put_oi = sum(int(r.get("putOpenInterest") or 0) for r in all_strikes)
            total_vol = call_vol + put_vol
            pc_vol_ratio = round(put_vol / max(1, call_vol), 3)
            pc_oi_ratio = round(put_oi / max(1, call_oi), 3)

            if call_vol > put_vol * 1.3:
                sentiment = "bullish"
            elif put_vol > call_vol * 1.3:
                sentiment = "bearish"
            else:
                sentiment = "mixed"

            result["unusualFlow"] = {
                "totalSignals": total_vol,
                "calls": call_vol,
                "puts": put_vol,
                "sweeps": 0,
                "netSentiment": sentiment,
                "pcVolumeRatio": pc_vol_ratio,
                "pcOiRatio": pc_oi_ratio,
                "callOi": call_oi,
                "putOi": put_oi,
                "_source": "orats_volume_proxy",
            }
        except Exception as exc:
            LOG.debug("Engine13 unusual flow (ORATS proxy): %s", exc)

    return result


# ---------------------------------------------------------------------------
# Layer 4 — Technical Context (delegates to technicals.py)
# ---------------------------------------------------------------------------

def compute_technical_context(orats: Any, *, ticker: str = "SPX") -> Dict[str, Any]:
    """Layer 4: EMA, Bollinger, RSI, MACD via shared technicals module."""
    try:
        from backend.technicals import compute_technicals_payload
        return compute_technicals_payload(orats, ticker=ticker)
    except Exception as exc:
        LOG.debug("Engine13 technicals: %s", exc)
        return {"enabled": False, "notes": [f"Technicals unavailable: {exc}"]}


# ---------------------------------------------------------------------------
# Layer 5 — VIX Behaviour
# ---------------------------------------------------------------------------

def compute_vix_behaviour(
    vix_bars: List[Any],
    *,
    live_vix: Optional[float] = None,
) -> Dict[str, Any]:
    """Layer 5: VIX change, snapback detection, percentile."""
    if len(vix_bars) < 20:
        return {"enabled": False, "notes": ["Insufficient VIX history"]}

    closes = [float(_bar_close(b)) for b in vix_bars if _bar_close(b) is not None]
    if len(closes) < 20:
        return {"enabled": False, "notes": ["Insufficient VIX closes"]}

    prev_close = closes[-1]
    year_closes = closes[-252:] if len(closes) >= 252 else closes
    vix_now = live_vix if live_vix is not None else prev_close

    change_pct = (vix_now - prev_close) / prev_close * 100.0 if prev_close else 0
    pct_rank = sum(1 for c in year_closes if c < vix_now) / len(year_closes) * 100.0

    ma_20 = statistics.mean(closes[-20:])
    above_ma = vix_now > ma_20

    snapback = False
    snapback_note = None
    if live_vix is not None and change_pct < -5:
        snapback = True
        snapback_note = f"VIX dropped {abs(change_pct):.1f}% but may snapback — watch for recovery above {ma_20:.1f}"
    elif live_vix is not None and change_pct > 5 and prev_close < ma_20:
        snapback_note = f"VIX spiked above 20d MA ({ma_20:.1f}) — stress returning"

    return {
        "enabled": True,
        "vixNow": round(vix_now, 2),
        "prevClose": round(prev_close, 2),
        "changePct": round(change_pct, 2),
        "percentileRank": round(pct_rank, 1),
        "ma20": round(ma_20, 2),
        "aboveMa20": above_ma,
        "snapback": snapback,
        "snapbackNote": snapback_note,
    }


# ---------------------------------------------------------------------------
# Layer 6 — Scenario Probability Engine
# ---------------------------------------------------------------------------

def compute_scenario_probabilities(
    *,
    gap_info: Dict[str, Any],
    historical: Dict[str, Any],
    options: Dict[str, Any],
    vix: Dict[str, Any],
    technicals: Dict[str, Any],
) -> Dict[str, Any]:
    """Layer 6: weighted scenario probabilities combining all layers."""
    if not gap_info.get("enabled"):
        return {"enabled": False, "notes": ["No gap detected"]}

    gap_pct = gap_info.get("gapPct", 0)
    direction = gap_info.get("direction", "up")

    # Base rates from historical analogues
    dist = (historical.get("outcomeDistribution") or {})
    base_cont = dist.get("continuation", 35.0) / 100.0
    base_cons = dist.get("consolidation", 35.0) / 100.0
    base_rev = dist.get("reversion", 30.0) / 100.0

    # Start with historical base rates
    p_cont = base_cont
    p_rev = base_rev
    p_cons = base_cons

    # --- Modifier: dealer gamma ---
    gamma_ctx = options.get("dealerGamma") or {}
    gamma_sign = gamma_ctx.get("netGammaSign", "unknown")
    gamma_bucket = gamma_ctx.get("magnitudeBucket", "low")
    if gamma_sign == "positive":
        # Positive gamma = dealers stabilise = nudge toward consolidation
        shift = 0.05 if gamma_bucket == "low" else 0.08 if gamma_bucket == "medium" else 0.12
        p_cons += shift
        p_cont -= shift * 0.5
        p_rev -= shift * 0.5
    elif gamma_sign == "negative":
        # Negative gamma = dealers amplify = nudge toward extremes (cont or rev)
        shift = 0.04 if gamma_bucket == "low" else 0.07 if gamma_bucket == "medium" else 0.10
        p_cont += shift * 0.6
        p_rev += shift * 0.4
        p_cons -= shift

    # --- Modifier: skew ---
    skew = options.get("skew") or {}
    skew_label = skew.get("label", "")
    if "extreme_put_skew" in skew_label:
        p_rev += 0.06
        p_cont -= 0.06
    elif "elevated_put_skew" in skew_label:
        p_rev += 0.03
        p_cont -= 0.03
    elif "call_skew" in skew_label:
        p_cont += 0.04
        p_rev -= 0.04

    # --- Modifier: IV term structure ---
    ts = options.get("termStructure") or {}
    ts_label = ts.get("label", "")
    if ts_label == "backwardation":
        p_rev += 0.04
        p_cont -= 0.04
    elif ts_label == "contango":
        p_cont += 0.03
        p_cons += 0.02
        p_rev -= 0.05

    # --- Modifier: VIX behaviour ---
    if vix.get("enabled"):
        vix_change = vix.get("changePct", 0)
        if vix.get("snapback"):
            p_cons += 0.04
            p_rev += 0.02
            p_cont -= 0.06
        elif vix_change < -15:
            # Big VIX crush on gap-up = market conviction
            p_cont += 0.06
            p_rev -= 0.04
            p_cons -= 0.02
        elif abs(vix_change) < 3:
            p_cons += 0.03

    # --- Modifier: RSI ---
    tech_rsi = (technicals.get("rsi") or {})
    rsi_value = tech_rsi.get("value")
    if rsi_value is not None:
        if rsi_value > 75:
            p_rev += 0.05
            p_cont -= 0.05
        elif rsi_value > 70:
            p_rev += 0.03
            p_cont -= 0.03
        elif rsi_value < 30:
            p_rev += 0.04
            p_cont -= 0.04

    # --- Modifier: unusual flow ---
    flow = options.get("unusualFlow") or {}
    net_sentiment = flow.get("netSentiment", "mixed")
    if net_sentiment == "bullish" and direction == "up":
        p_cont += 0.03
        p_rev -= 0.03
    elif net_sentiment == "bearish" and direction == "up":
        p_rev += 0.03
        p_cont -= 0.03

    # --- Modifier: gap magnitude (extreme gaps tend to revert more) ---
    abs_gap = abs(gap_pct)
    if abs_gap > 3.0:
        p_rev += 0.06
        p_cont -= 0.03
        p_cons -= 0.03
    elif abs_gap > 2.0:
        p_rev += 0.03
        p_cont -= 0.02
        p_cons -= 0.01

    # Normalise to sum to 1.0
    total = max(1e-9, p_cont + p_cons + p_rev)
    p_cont = _CLAMP(0.02, 0.96, p_cont / total)
    p_cons = _CLAMP(0.02, 0.96, p_cons / total)
    p_rev = _CLAMP(0.02, 0.96, p_rev / total)
    total = p_cont + p_cons + p_rev
    p_cont /= total
    p_cons /= total
    p_rev /= total

    # Confidence: higher when one scenario dominates
    max_p = max(p_cont, p_cons, p_rev)
    confidence = _CLAMP(20, 95, int(max_p * 120))

    # Expected D+5 range from historical stats
    d5_stats = (historical.get("stats") or {}).get("d5")
    expected_range = None
    if d5_stats:
        expected_range = {
            "p25": d5_stats.get("p25"),
            "median": d5_stats.get("median"),
            "p75": d5_stats.get("p75"),
        }

    modifiers = []
    if gamma_sign != "unknown":
        modifiers.append(f"Dealer gamma {gamma_sign} ({gamma_bucket})")
    if skew_label:
        modifiers.append(f"Skew: {skew_label}")
    if ts_label:
        modifiers.append(f"Term structure: {ts_label}")
    if vix.get("snapback"):
        modifiers.append("VIX snapback detected")
    if rsi_value is not None and rsi_value > 70:
        modifiers.append(f"RSI overbought ({rsi_value:.0f})")
    if net_sentiment != "mixed":
        modifiers.append(f"Unusual flow: {net_sentiment}")

    return {
        "enabled": True,
        "continuation": round(p_cont * 100, 1),
        "consolidation": round(p_cons * 100, 1),
        "reversion": round(p_rev * 100, 1),
        "confidence": confidence,
        "dominantScenario": (
            "continuation" if p_cont >= p_cons and p_cont >= p_rev else
            "consolidation" if p_cons >= p_rev else
            "reversion"
        ),
        "expectedRangeD5": expected_range,
        "modifiers": modifiers,
    }


# ---------------------------------------------------------------------------
# Orchestrator — full scan payload
# ---------------------------------------------------------------------------

def compute_gap_regime_scan(
    *,
    orats: Any = None,
    benzinga: Any = None,
    eodhd: Any = None,
    price_service: Any = None,
    flags: Any = None,
    gap_threshold_pct: float = 1.5,
) -> Dict[str, Any]:
    """Build the full Engine 13 scan payload."""
    today = dt.date.today()
    lookback_start = today - dt.timedelta(days=365 * 5 + 60)
    oil_lookback = today - dt.timedelta(days=10)

    # --- Fetch data in parallel ---
    spx_bars: List[Any] = []
    vix_bars: List[Any] = []
    oil_bars: List[Any] = []
    headlines: List[str] = []
    live_spx: Optional[float] = None
    live_vix: Optional[float] = None

    def _fetch_spx():
        nonlocal spx_bars, live_spx
        if price_service:
            try:
                spx_bars = price_service.fetch_daily_bars("SPX", lookback_start, today)
            except Exception as exc:
                LOG.warning("E13 SPX bars via PriceService: %s", exc)
        if not spx_bars and orats:
            try:
                resp = orats.hist_dailies("SPX", trade_date=f"{lookback_start},{today}",
                                          fields="ticker,tradeDate,open,high,low,close,volume")
                spx_bars = resp.rows if resp and resp.rows else []
            except Exception as exc:
                LOG.warning("E13 SPX bars via ORATS: %s", exc)
        if price_service:
            try:
                live_spx = price_service.fetch_live_price("SPX")
            except Exception:
                pass

    def _fetch_vix():
        nonlocal vix_bars, live_vix
        if price_service:
            try:
                vix_bars = price_service.fetch_daily_bars("VIX", lookback_start, today)
            except Exception as exc:
                LOG.warning("E13 VIX bars: %s", exc)
        if eodhd and not vix_bars:
            try:
                resp = eodhd.get_eod("VIX.INDX", from_date=str(lookback_start), to_date=str(today))
                vix_bars = resp.rows if resp and resp.rows else []
            except Exception:
                pass
        if eodhd:
            try:
                from backend.routers.engine12_vix_fade import _fetch_live_vix
                live_vix = _fetch_live_vix(eodhd)
            except Exception:
                pass

    def _fetch_oil():
        nonlocal oil_bars
        if eodhd:
            try:
                resp = eodhd.get_eod("USO.US", from_date=str(oil_lookback), to_date=str(today))
                oil_bars = resp.rows if resp and resp.rows else []
            except Exception as exc:
                LOG.debug("E13 oil (USO) bars: %s", exc)

    def _fetch_headlines():
        nonlocal headlines
        if benzinga:
            try:
                resp = benzinga.news(
                    tickers="SPY,SPX",
                    date_from=str(today - dt.timedelta(days=1)),
                    date_to=str(today),
                    page_size=50,
                )
                rows = resp.rows if resp else []
                for r in rows:
                    title = r.get("title") or r.get("headline") or ""
                    if title:
                        headlines.append(title)
            except Exception as exc:
                LOG.debug("E13 headlines: %s", exc)
        if not headlines and eodhd:
            try:
                resp = eodhd.get_news(ticker="SPY.US", from_date=str(today - dt.timedelta(days=1)),
                                      to_date=str(today), limit=30)
                for r in (resp.rows if resp else []):
                    title = r.get("title") or ""
                    if title:
                        headlines.append(title)
            except Exception as exc:
                LOG.debug("E13 EODHD headlines: %s", exc)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(_fetch_spx),
            pool.submit(_fetch_vix),
            pool.submit(_fetch_oil),
            pool.submit(_fetch_headlines),
        ]
        for f in as_completed(futures):
            try:
                f.result()
            except Exception:
                pass

    # --- Layer 1: Gap characterisation ---
    catalyst_tag = "unknown"
    try:
        from backend.daily_market_state import load_dms
        from backend.redis_store import get_store_optional
        store = get_store_optional()
        if store:
            dms = load_dms(today.isoformat(), store)
            if dms:
                themes = dms.to_dict().get("news_themes") or []
                if themes:
                    top = max(themes, key=lambda t: t.get("adjusted_intensity", 0))
                    catalyst_tag = top.get("label") or top.get("theme") or "unknown"
    except Exception:
        pass

    gap_info = characterise_gap(spx_bars, live_price=live_spx, catalyst_tag=catalyst_tag)

    # --- Layer 2: Historical analogues ---
    direction_filter = gap_info.get("direction") if gap_info.get("enabled") else None
    historical = compute_historical_gap_analogues(
        spx_bars,
        gap_threshold_pct=gap_threshold_pct,
        direction_filter=direction_filter,
        max_analogues=50,
    )

    # Also pull Engine 12 geopolitical shock analogues
    geo_analogues = None
    try:
        from backend.engine12_spike_detector import find_similar_events, load_shock_db
        shock_db = load_shock_db()
        if shock_db and gap_info.get("enabled"):
            vix_change = 0
            if live_vix and vix_bars:
                vc = _bar_close(vix_bars[-1])
                if vc:
                    vix_change = (live_vix - float(vc)) / float(vc) * 100.0
            geo_analogues = find_similar_events(
                vix_spike_pct=abs(vix_change),
                spx_gap_pct=gap_info.get("gapPct", 0),
                oil_gap_pct=0,
                shock_db=shock_db,
                top_n=5,
            )
    except Exception as exc:
        LOG.debug("Engine13 geo analogues: %s", exc)

    # --- Layer 3: Options microstructure ---
    options = compute_options_microstructure(orats, ticker="SPX", benzinga=benzinga)

    # --- Layer 4: Technicals ---
    technicals = compute_technical_context(orats, ticker="SPX")

    # --- Layer 5: VIX ---
    vix_behaviour = compute_vix_behaviour(vix_bars, live_vix=live_vix)

    # --- Layer 6: Scenario probabilities ---
    scenarios = compute_scenario_probabilities(
        gap_info=gap_info,
        historical=historical,
        options=options,
        vix=vix_behaviour,
        technicals=technicals,
    )

    # --- Layer 7: Catalyst Fragility Score ---
    fragility: Dict[str, Any] = {"enabled": False}
    try:
        from backend.config import get_flags as _get_flags
        _fl = flags or _get_flags()
        if getattr(_fl, "ENGINE13_FRAGILITY_ENABLED", True):
            from backend.engine13_fragility import compute_catalyst_fragility

            frag_weights = {
                "optionsConviction": getattr(_fl, "ENGINE13_FRAGILITY_W_OPTIONS", 0.30),
                "crossAssetConfirmation": getattr(_fl, "ENGINE13_FRAGILITY_W_CROSS_ASSET", 0.25),
                "historicalDurability": getattr(_fl, "ENGINE13_FRAGILITY_W_HISTORICAL", 0.20),
                "headlineMomentum": getattr(_fl, "ENGINE13_FRAGILITY_W_HEADLINE", 0.15),
                "priceActionQuality": getattr(_fl, "ENGINE13_FRAGILITY_W_PRICE_ACTION", 0.10),
            }

            theme_snapshot = None
            try:
                from backend.daily_market_state import load_dms as _load_dms
                from backend.redis_store import get_store_optional as _get_store
                _st = _get_store()
                if _st:
                    _dms = _load_dms(today.isoformat(), _st)
                    if _dms:
                        d = _dms.to_dict()
                        themes = d.get("news_themes") or []
                        if themes:
                            theme_snapshot = {"readings": themes}
            except Exception:
                pass

            fragility = compute_catalyst_fragility(
                gap_info=gap_info,
                options=options,
                vix=vix_behaviour,
                historical=historical,
                geo_analogues=geo_analogues,
                oil_bars=oil_bars,
                headlines=headlines,
                theme_snapshot=theme_snapshot,
                weights=frag_weights,
            )
    except Exception as exc:
        LOG.debug("Engine13 fragility: %s", exc)

    return {
        "asOfDate": today.isoformat(),
        "ticker": "SPX",
        "gap": gap_info,
        "historicalAnalogues": historical,
        "geopoliticalAnalogues": geo_analogues,
        "optionsMicrostructure": options,
        "technicals": technicals,
        "vixBehaviour": vix_behaviour,
        "scenarios": scenarios,
        "catalystFragility": fragility,
        "notes": [],
    }
