from __future__ import annotations

import base64
import datetime as dt
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from backend.orats_client import OratsClient


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if not math.isfinite(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class DailyBar:
    trade_date: str
    open: Optional[float]
    high: Optional[float]
    low: Optional[float]
    close: Optional[float]
    volume: Optional[float]
    vwap: Optional[float]


def fetch_daily_bars_range(
    client: OratsClient,
    *,
    ticker: str,
    start: dt.date,
    end: dt.date,
) -> List[DailyBar]:
    """
    Fast path: ORATS /hist/dailies supports tradeDate ranges of the form:
      tradeDate=YYYY-MM-DD,YYYY-MM-DD

    We request a superset of field aliases and normalize best-effort.
    """
    if end < start:
        return []
    td = f"{_fmt_date(start)},{_fmt_date(end)}"
    # ORATS field names vary by entitlement; request common aliases.
    fields = ",".join(
        [
            "ticker",
            "tradeDate",
            "open",
            "opPx",
            "high",
            "hiPx",
            "low",
            "loPx",
            "close",
            "clsPx",
            "volume",
            "vol",
            "vwap",
        ]
    )
    try:
        resp = client.hist_dailies(ticker=ticker, trade_date=td, fields=fields)
        rows = resp.rows or []
    except Exception:
        rows = []

    out: List[DailyBar] = []
    for r in rows:
        if not isinstance(r, dict):
            continue
        d0 = str(r.get("tradeDate") or r.get("trade_date") or "")[:10]
        if not d0:
            continue
        o = _to_float(r.get("open") or r.get("opPx") or r.get("op_px"))
        h = _to_float(r.get("high") or r.get("hiPx") or r.get("hi") or r.get("hPx"))
        l = _to_float(r.get("low") or r.get("loPx") or r.get("lo") or r.get("lPx"))
        c = _to_float(r.get("close") or r.get("clsPx") or r.get("cls_px"))
        if c is None or c <= 0:
            continue
        vol = _to_float(r.get("volume") or r.get("vol") or r.get("totalVolume"))
        vwap = _to_float(r.get("vwap"))
        out.append(DailyBar(trade_date=d0, open=o, high=h, low=l, close=c, volume=vol, vwap=vwap))
    out.sort(key=lambda b: b.trade_date)
    return out


def fetch_live_price_optional(client: OratsClient, *, ticker: str) -> Optional[float]:
    """
    Best-effort live price (spotPrice preferred). If unavailable (closed market / entitlement),
    return None.
    """
    try:
        if not callable(getattr(client, "live_summaries", None)):
            return None
        resp = client.live_summaries(ticker=str(ticker).upper())
        rows = resp.rows or []
        row = next((x for x in rows if isinstance(x, dict)), None)
        if not row:
            return None
        spot = _to_float(row.get("spotPrice"))
        px = spot if (spot is not None and spot > 0) else _to_float(row.get("stockPrice"))
        return px if (px is not None and px > 0) else None
    except Exception:
        return None


def _ema_series(closes: List[float], span: int) -> List[float]:
    if not closes:
        return []
    s = int(span)
    if s <= 1:
        return list(closes)
    a = 2.0 / (float(s) + 1.0)
    out = [float(closes[0])]
    for i in range(1, len(closes)):
        out.append(a * float(closes[i]) + (1.0 - a) * out[-1])
    return out


def compute_ema_levels(closes: List[float], spans: List[int]) -> Dict[str, Optional[float]]:
    out: Dict[str, Optional[float]] = {}
    for s in spans:
        key = f"ema{int(s)}"
        if len(closes) < max(2, int(s)):
            out[key] = None
            continue
        series = _ema_series(closes, int(s))
        out[key] = float(series[-1]) if series else None
    return out


def _rolling_hl_mid(highs: List[float], lows: List[float], window: int) -> List[Optional[float]]:
    out: List[Optional[float]] = []
    w = int(window)
    for i in range(len(highs)):
        if i + 1 < w:
            out.append(None)
            continue
        hh = max(highs[i + 1 - w : i + 1])
        ll = min(lows[i + 1 - w : i + 1])
        out.append((float(hh) + float(ll)) / 2.0)
    return out


def compute_ichimoku_levels(bars: List[DailyBar]) -> Dict[str, Any]:
    """
    Ichimoku on daily bars.
    Returns both current conversion/base lines and a best-effort cloud view:
    - cloudNow uses SpanA/SpanB values shifted 26 days forward, aligned to 'today' (i.e., values computed 26 bars ago)
    - cloudFuture uses current (unshifted) SpanA/SpanB as the projected cloud 26 days ahead
    """
    # Require usable H/L/C
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    if not bars or any(v is None for v in highs[-1:] + lows[-1:] + closes[-1:]):
        return {"enabled": False, "notes": ["Insufficient OHLC for Ichimoku."]}

    hs = [float(x) for x in highs if x is not None]
    ls = [float(x) for x in lows if x is not None]
    cs = [float(x) for x in closes if x is not None]
    # If we dropped Nones, indices no longer align; require full alignment.
    if len(hs) != len(bars) or len(ls) != len(bars) or len(cs) != len(bars):
        return {"enabled": False, "notes": ["Missing high/low/close in bar series; cannot compute Ichimoku reliably."]}

    tenkan = _rolling_hl_mid(hs, ls, 9)
    kijun = _rolling_hl_mid(hs, ls, 26)
    span_b = _rolling_hl_mid(hs, ls, 52)
    span_a: List[Optional[float]] = []
    for i in range(len(bars)):
        if tenkan[i] is None or kijun[i] is None:
            span_a.append(None)
        else:
            span_a.append((float(tenkan[i]) + float(kijun[i])) / 2.0)

    # Chikou is close shifted back 26 (i.e. today's chikou value plotted at t-26).
    chikou = cs[-1] if len(cs) >= 1 else None
    chikou_anchor_date = None
    if len(bars) > 26:
        chikou_anchor_date = bars[-27].trade_date  # where chikou would be plotted

    # Cloud now: use values computed 26 bars ago, aligned to today.
    cloud_now = None
    if len(bars) > 26:
        i = len(bars) - 1 - 26
        a_now = span_a[i]
        b_now = span_b[i]
        if a_now is not None and b_now is not None:
            cloud_now = {"spanA": float(a_now), "spanB": float(b_now)}

    # Cloud future (projected): current spanA/spanB (unshifted) represent the forward-projected cloud.
    a_fut = span_a[-1]
    b_fut = span_b[-1]
    cloud_future = None
    if a_fut is not None and b_fut is not None:
        cloud_future = {"spanA": float(a_fut), "spanB": float(b_fut), "projectsToDate": _fmt_date(_parse_date(bars[-1].trade_date) + dt.timedelta(days=26))}

    def _cloud_obj(c: Optional[dict]) -> Optional[dict]:
        if not c:
            return None
        top = max(float(c["spanA"]), float(c["spanB"]))
        bot = min(float(c["spanA"]), float(c["spanB"]))
        return {**c, "cloudTop": top, "cloudBottom": bot, "cloudBias": ("bullish" if float(c["spanA"]) >= float(c["spanB"]) else "bearish")}

    return {
        "enabled": True,
        "tenkan": None if tenkan[-1] is None else float(tenkan[-1]),
        "kijun": None if kijun[-1] is None else float(kijun[-1]),
        "chikou": chikou,
        "chikouPlottedAtDate": chikou_anchor_date,
        "cloudNow": _cloud_obj(cloud_now),
        "cloudFuture": _cloud_obj(cloud_future),
        "notes": [
            "Ichimoku computed on daily bars.",
            "cloudNow is the cloud value aligned to the current date (computed 26 bars ago).",
            "cloudFuture is the projected cloud 26 days forward (computed today).",
        ],
    }


def compute_vwap_proxy(bars: List[DailyBar], window: int = 20) -> Dict[str, Any]:
    """
    VWAP proxy (daily-only).
    - If ORATS provides a per-day vwap field, we surface it.
    - Else if volume is present, we compute a rolling VWAP over the last `window` daily bars:
        sum(typicalPrice * volume) / sum(volume), where typicalPrice=(H+L+C)/3.
    """
    if not bars:
        return {"enabled": False, "notes": ["No bars available."]}
    last = bars[-1]
    if last.vwap is not None and last.vwap > 0:
        return {
            "enabled": True,
            "mode": "orats_daily_vwap",
            "value": float(last.vwap),
            "window": 1,
            "notes": ["Using ORATS-provided daily VWAP field (if present in entitlement)."],
        }

    # Rolling proxy if volume exists
    use = bars[-int(window) :] if window and len(bars) >= 1 else bars
    numer = 0.0
    denom = 0.0
    have_any_vol = False
    for b in use:
        if b.high is None or b.low is None or b.close is None:
            continue
        if b.volume is None or b.volume <= 0:
            continue
        have_any_vol = True
        tp = (float(b.high) + float(b.low) + float(b.close)) / 3.0
        numer += tp * float(b.volume)
        denom += float(b.volume)
    if have_any_vol and denom > 0:
        return {
            "enabled": True,
            "mode": "rolling_daily_typical_price_vwap",
            "value": float(numer / denom),
            "window": int(window),
            "notes": [
                "Daily VWAP proxy computed from daily OHLC and daily volume.",
                "This is not true intraday VWAP; it’s a rolling daily proxy.",
            ],
        }

    # Last-resort proxy: typical price of last bar
    if last.high is not None and last.low is not None and last.close is not None:
        tp = (float(last.high) + float(last.low) + float(last.close)) / 3.0
        return {
            "enabled": True,
            "mode": "daily_typical_price",
            "value": float(tp),
            "window": 1,
            "notes": [
                "Volume unavailable; using typical price (H+L+C)/3 as a VWAP-like proxy.",
                "This is not true VWAP.",
            ],
        }

    return {"enabled": False, "notes": ["VWAP proxy unavailable (missing OHLC/volume)."]}


def compute_distances(*, live_price: Optional[float], levels: Dict[str, Optional[float]]) -> Dict[str, Any]:
    if live_price is None or live_price <= 0:
        return {"enabled": False, "notes": ["Live price unavailable."]}
    out: Dict[str, Any] = {"enabled": True, "price": float(live_price), "levels": {}, "notes": []}
    for k, v in (levels or {}).items():
        if v is None or not isinstance(v, (int, float)) or not math.isfinite(float(v)):
            continue
        diff = float(live_price) - float(v)
        out["levels"][str(k)] = {
            "level": float(v),
            "diffPts": round(diff, 4),
            "diffPct": round((diff / float(live_price)) * 100.0, 4),
        }
    return out


def compute_technicals_payload(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: Optional[str] = None,
    lookback_days: int = 420,
) -> Dict[str, Any]:
    """
    Compute technicals on daily bars + a live overlay.\n
    - Indicators are computed on daily bars up to the last available daily close.\n
    - Live overlay uses ORATS live_summaries spot/stockPrice when available.\n
    """
    t = str(ticker).strip().upper()
    today = dt.date.today()
    if as_of_date:
        try:
            today = _parse_date(str(as_of_date)[:10])
        except Exception:
            today = dt.date.today()

    start = today - dt.timedelta(days=int(lookback_days))
    bars = fetch_daily_bars_range(client, ticker=t, start=start, end=today)
    if not bars:
        # Fall back a bit: sometimes today’s EOD is missing intraday; try last 30 days anchored to today anyway.
        start2 = today - dt.timedelta(days=60)
        bars = fetch_daily_bars_range(client, ticker=t, start=start2, end=today)

    if not bars:
        return {"enabled": False, "ticker": t, "notes": ["No daily bars available from ORATS."]}

    closes = [float(b.close) for b in bars if b.close is not None and b.close > 0]
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    ok_ohlc = bool(closes) and highs[-1] is not None and lows[-1] is not None

    ema = compute_ema_levels(closes, spans=[8, 21, 50, 100, 200]) if closes else {}
    ich = compute_ichimoku_levels(bars) if ok_ohlc else {"enabled": False, "notes": ["Insufficient OHLC for Ichimoku."]}
    vwap = compute_vwap_proxy(bars, window=20)

    live_px = fetch_live_price_optional(client, ticker=t)

    # Distances to key levels for quick “think around” use.
    level_map: Dict[str, Optional[float]] = {}
    level_map.update(ema)
    if isinstance(vwap, dict) and vwap.get("enabled") and vwap.get("value") is not None:
        level_map["vwapProxy"] = float(vwap["value"])
    if isinstance(ich, dict) and ich.get("enabled"):
        if isinstance(ich.get("tenkan"), (int, float)):
            level_map["tenkan"] = float(ich["tenkan"])
        if isinstance(ich.get("kijun"), (int, float)):
            level_map["kijun"] = float(ich["kijun"])
        cn = ich.get("cloudNow") if isinstance(ich.get("cloudNow"), dict) else None
        if cn and isinstance(cn.get("cloudTop"), (int, float)) and isinstance(cn.get("cloudBottom"), (int, float)):
            level_map["cloudTopNow"] = float(cn["cloudTop"])
            level_map["cloudBottomNow"] = float(cn["cloudBottom"])

    distances = compute_distances(live_price=live_px, levels=level_map)

    last_bar = bars[-1]
    return {
        "enabled": True,
        "ticker": t,
        "asOfDate": str(as_of_date or last_bar.trade_date)[:10],
        "barDateUsed": str(last_bar.trade_date)[:10],
        "lastDailyClose": None if last_bar.close is None else round(float(last_bar.close), 4),
        "livePrice": None if live_px is None else round(float(live_px), 4),
        "ema": {k: (None if v is None else round(float(v), 4)) for k, v in (ema or {}).items()},
        "ichimoku": ich,
        "vwapProxy": ({"enabled": False} if not isinstance(vwap, dict) else {**vwap, "value": (None if vwap.get("value") is None else round(float(vwap["value"]), 4))}),
        "distances": distances,
        "notes": [
            "Indicators are computed on daily bars (EOD).",
            "Live overlay uses ORATS Live summaries when available (may reflect afterhours/last known).",
        ],
    }


def encode_image_to_data_url(*, content: bytes, content_type: str) -> str:
    b64 = base64.b64encode(content).decode("utf-8")
    ct = str(content_type or "application/octet-stream")
    return f"data:{ct};base64,{b64}"


