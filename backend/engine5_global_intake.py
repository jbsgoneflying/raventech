"""Engine 5 – Global Market Intake Module.

Normalizes raw EODHD bars into GlobalAssetBar records with:
- Local currency returns
- USD-converted returns
- Rolling z-scores (20d, 60d)
- Yield curve snapshots
"""

from __future__ import annotations

import json
import math
import os
import statistics
from dataclasses import asdict, dataclass
from typing import Any, Dict, List, Optional, Tuple


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class GlobalAssetBar:
    symbol: str
    asset_class: str          # equity_index | fx | commodity | yield | macro
    region: str               # europe | asia | australia | us | global
    date: str                 # YYYY-MM-DD
    close: float
    close_usd: Optional[float] = None
    return_1d_local: Optional[float] = None
    return_1d_usd: Optional[float] = None
    z_score_20d: Optional[float] = None
    z_score_60d: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> "GlobalAssetBar":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


@dataclass
class YieldSnapshot:
    date: str
    us_2y: float
    us_10y: float
    us_2s10s_slope: float
    de_10y: Optional[float] = None
    jp_10y: Optional[float] = None
    us_real_10y: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)


# ---------------------------------------------------------------------------
# Universe loader
# ---------------------------------------------------------------------------

_UNIVERSE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "data", "universe", "global_assets.json"
)


def load_universe(path: Optional[str] = None) -> dict:
    """Load the global asset universe JSON."""
    p = path or _UNIVERSE_PATH
    with open(p, "r") as f:
        return json.load(f)


def all_eod_symbols(universe: dict) -> List[dict]:
    """Return flat list of all symbols that need daily EOD fetches."""
    out: List[dict] = []
    for entry in universe.get("equity_indices", []):
        out.append({"symbol": entry["symbol"], "asset_class": "equity_index", "region": entry.get("region", ""), "currency": entry.get("currency", "USD")})
    for entry in universe.get("fx", []):
        out.append({"symbol": entry["symbol"], "asset_class": "fx", "region": "global", "currency": "USD"})
    for entry in universe.get("commodities", []):
        out.append({"symbol": entry["symbol"], "asset_class": "commodity", "region": "global", "currency": "USD"})
    for entry in universe.get("sovereign_yields", []):
        out.append({"symbol": entry["symbol"], "fallback": entry.get("fallback"), "asset_class": "yield", "region": entry.get("region", ""), "currency": "USD"})
    # Volatility indices with a direct EODHD ticker
    for entry in universe.get("volatility_indices", []):
        sym = entry.get("symbol")
        if sym:
            out.append({
                "symbol": sym,
                "fallback": entry.get("fallback_realized_from"),
                "asset_class": "volatility_index",
                "region": entry.get("region", ""),
                "currency": "EUR",
            })
    return out


# ---------------------------------------------------------------------------
# Math helpers
# ---------------------------------------------------------------------------


def _to_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        return f if math.isfinite(f) else None
    except (TypeError, ValueError):
        return None


def _log_return(prev: float, curr: float) -> Optional[float]:
    if prev and prev > 0 and curr and curr > 0:
        return math.log(curr / prev)
    return None


def _z_score(value: float, series: List[float], min_n: int = 5) -> Optional[float]:
    """Compute z-score of value against a series."""
    vals = [v for v in series if v is not None and math.isfinite(v)]
    if len(vals) < min_n:
        return None
    mu = statistics.mean(vals)
    try:
        sd = statistics.stdev(vals)
    except statistics.StatisticsError:
        return None
    if sd < 1e-12:
        return None
    return (value - mu) / sd


# ---------------------------------------------------------------------------
# FX conversion helpers
# ---------------------------------------------------------------------------

# Map currency -> FX pair symbol and direction for USD conversion
_FX_CONVERSION = {
    "EUR": ("EURUSD.FOREX", "multiply"),   # EUR * EURUSD = USD
    "GBP": ("GBPUSD.FOREX", "multiply"),   # GBP * GBPUSD = USD (note: GBPUSD might not be in our universe, but EURUSD proxy)
    "JPY": ("USDJPY.FOREX", "divide"),     # JPY / USDJPY = USD
    "AUD": ("AUDUSD.FOREX", "multiply"),   # AUD * AUDUSD = USD
    "HKD": ("USDHKD.FOREX", "divide"),     # HKD / USDHKD = USD (approx fixed peg)
    "CNY": ("USDCNY.FOREX", "divide"),     # CNY / USDCNY = USD
    "USD": (None, None),                    # No conversion needed
}


def _convert_to_usd(
    close_local: float,
    currency: str,
    fx_rates: Dict[str, float],
) -> Optional[float]:
    """Convert a local currency close to USD using available FX rates."""
    if currency == "USD":
        return close_local
    info = _FX_CONVERSION.get(currency)
    if not info or info[0] is None:
        return None
    pair, direction = info
    rate = fx_rates.get(pair)
    if rate is None or rate <= 0:
        # Try simpler lookup by base pair name
        return None
    if direction == "multiply":
        return close_local * rate
    elif direction == "divide":
        return close_local / rate if rate > 0 else None
    return None


# ---------------------------------------------------------------------------
# Core normalization
# ---------------------------------------------------------------------------


def normalize_bars(
    raw_bars: Dict[str, List[dict]],
    fx_rates: Dict[str, float],
    history: Dict[str, List[dict]],
    universe: dict,
) -> List[GlobalAssetBar]:
    """Normalize raw EODHD bars into GlobalAssetBar records.

    Args:
        raw_bars: {symbol: [{"date": ..., "close": ..., "adjusted_close": ...}, ...]}
        fx_rates: {fx_symbol: close_rate} e.g. {"EURUSD.FOREX": 1.0832}
        history: {symbol: [{"date": ..., "close": ..., "return_1d_local": ...}, ...]}
                 Prior 60+ days from Redis durable history.
        universe: Loaded global_assets.json

    Returns:
        List of GlobalAssetBar for today's date.
    """
    symbol_meta = {}
    for entry in all_eod_symbols(universe):
        symbol_meta[entry["symbol"]] = entry

    results: List[GlobalAssetBar] = []

    for symbol, bars in raw_bars.items():
        if not bars:
            continue

        meta = symbol_meta.get(symbol, {})
        asset_class = meta.get("asset_class", "equity_index")
        region = meta.get("region", "")
        currency = meta.get("currency", "USD")

        # Sort bars by date ascending
        bars_sorted = sorted(bars, key=lambda b: str(b.get("date", "")))
        latest = bars_sorted[-1]
        date_str = str(latest.get("date", ""))[:10]
        close = _to_float(latest.get("adjusted_close")) or _to_float(latest.get("close"))
        if close is None:
            continue

        # USD conversion
        close_usd = _convert_to_usd(close, currency, fx_rates) if asset_class != "fx" else close

        # Get historical closes for return and z-score calculation
        hist_bars = history.get(symbol, [])
        hist_closes = [_to_float(b.get("close")) for b in sorted(hist_bars, key=lambda b: str(b.get("date", "")))]
        hist_closes = [c for c in hist_closes if c is not None]

        # 1-day return
        prev_close = hist_closes[-1] if hist_closes else None
        return_1d_local = _log_return(prev_close, close) if prev_close else None

        # USD return
        return_1d_usd: Optional[float] = None
        if return_1d_local is not None and currency == "USD":
            return_1d_usd = return_1d_local
        elif return_1d_local is not None and close_usd is not None:
            # Approximate: local return + fx return (if we had prev fx).
            # For simplicity, use local return as proxy (FX contribution is second-order for daily)
            return_1d_usd = return_1d_local

        # Build return history for z-scores
        hist_returns: List[float] = []
        for b in sorted(hist_bars, key=lambda b: str(b.get("date", ""))):
            r = _to_float(b.get("return_1d_local"))
            if r is not None:
                hist_returns.append(r)

        z_20d = _z_score(return_1d_local, hist_returns[-20:]) if return_1d_local is not None and len(hist_returns) >= 15 else None
        z_60d = _z_score(return_1d_local, hist_returns[-60:]) if return_1d_local is not None and len(hist_returns) >= 40 else None

        bar = GlobalAssetBar(
            symbol=symbol,
            asset_class=asset_class,
            region=region,
            date=date_str,
            close=round(close, 6),
            close_usd=round(close_usd, 6) if close_usd is not None else None,
            return_1d_local=round(return_1d_local, 6) if return_1d_local is not None else None,
            return_1d_usd=round(return_1d_usd, 6) if return_1d_usd is not None else None,
            z_score_20d=round(z_20d, 4) if z_20d is not None else None,
            z_score_60d=round(z_60d, 4) if z_60d is not None else None,
        )
        results.append(bar)

    return results


def normalize_bars_bulk(
    raw_bars: Dict[str, List[dict]],
    fx_rates: Dict[str, float],
    existing_history: Dict[str, List[dict]],
    universe: dict,
) -> Tuple[List[GlobalAssetBar], Dict[str, List[dict]]]:
    """Normalize ALL raw bars day-by-day, building up return history as we go.

    Used for cold-start backfill: processes 60-90 days of bars sequentially
    so that returns, z-scores, and correlations can be computed from day 1.

    Args:
        raw_bars: {symbol: [{"date": ..., "close": ..., "adjusted_close": ...}, ...]}
                  Multiple days of bars per symbol.
        fx_rates: {fx_symbol: close_rate} -- latest FX rates (used for all days as approx).
        existing_history: Pre-existing history from Redis (may be empty).
        universe: Loaded global_assets.json.

    Returns:
        (all_bars, updated_history)
        - all_bars: Complete list of GlobalAssetBar across all days.
        - updated_history: {symbol: [bar_dict, ...]} ready to write to Redis.
    """
    symbol_meta = {}
    for entry in all_eod_symbols(universe):
        symbol_meta[entry["symbol"]] = entry

    # Collect all unique dates across all symbols, sorted ascending
    all_dates: set = set()
    for bars in raw_bars.values():
        for b in bars:
            d = str(b.get("date", ""))[:10]
            if d:
                all_dates.add(d)
    sorted_dates = sorted(all_dates)

    # Index raw bars by (symbol, date) for fast lookup
    bar_index: Dict[Tuple[str, str], dict] = {}
    for symbol, bars in raw_bars.items():
        for b in bars:
            d = str(b.get("date", ""))[:10]
            if d:
                bar_index[(symbol, d)] = b

    # Build running history per symbol from existing history
    running_history: Dict[str, List[dict]] = {}
    for sym in raw_bars.keys():
        running_history[sym] = list(existing_history.get(sym, []))

    all_bars: List[GlobalAssetBar] = []

    # Process day by day
    for date_str in sorted_dates:
        for symbol in raw_bars.keys():
            raw_bar = bar_index.get((symbol, date_str))
            if raw_bar is None:
                continue

            meta = symbol_meta.get(symbol, {})
            asset_class = meta.get("asset_class", "equity_index")
            region = meta.get("region", "")
            currency = meta.get("currency", "USD")

            close = _to_float(raw_bar.get("adjusted_close")) or _to_float(raw_bar.get("close"))
            if close is None:
                continue

            close_usd = _convert_to_usd(close, currency, fx_rates) if asset_class != "fx" else close

            # Compute return from running history
            hist = running_history.get(symbol, [])
            hist_closes = [_to_float(b.get("close")) for b in hist]
            hist_closes = [c for c in hist_closes if c is not None]

            prev_close = hist_closes[-1] if hist_closes else None
            return_1d_local = _log_return(prev_close, close) if prev_close else None

            return_1d_usd: Optional[float] = None
            if return_1d_local is not None:
                return_1d_usd = return_1d_local  # Approx (FX is second-order daily)

            # Z-scores from running history
            hist_returns: List[float] = []
            for b in hist:
                r = _to_float(b.get("return_1d_local"))
                if r is not None:
                    hist_returns.append(r)

            z_20d = _z_score(return_1d_local, hist_returns[-20:]) if return_1d_local is not None and len(hist_returns) >= 15 else None
            z_60d = _z_score(return_1d_local, hist_returns[-60:]) if return_1d_local is not None and len(hist_returns) >= 40 else None

            bar = GlobalAssetBar(
                symbol=symbol,
                asset_class=asset_class,
                region=region,
                date=date_str,
                close=round(close, 6),
                close_usd=round(close_usd, 6) if close_usd is not None else None,
                return_1d_local=round(return_1d_local, 6) if return_1d_local is not None else None,
                return_1d_usd=round(return_1d_usd, 6) if return_1d_usd is not None else None,
                z_score_20d=round(z_20d, 4) if z_20d is not None else None,
                z_score_60d=round(z_60d, 4) if z_60d is not None else None,
            )
            all_bars.append(bar)

            # Append to running history (dedup by date)
            bar_dict = bar.to_dict()
            hist = [b for b in running_history.get(symbol, []) if b.get("date") != date_str]
            hist.append(bar_dict)
            hist.sort(key=lambda b: str(b.get("date", "")))
            running_history[symbol] = hist

    return all_bars, running_history


# ---------------------------------------------------------------------------
# Yield curve extraction
# ---------------------------------------------------------------------------


def build_yield_snapshot(
    ust_rows: List[dict],
    de_10y_bars: List[dict],
    jp_10y_bars: List[dict],
    real_yield_rows: Optional[List[dict]] = None,
) -> Optional[YieldSnapshot]:
    """Build a YieldSnapshot from UST API rows and sovereign yield bars.

    Args:
        ust_rows: Rows from get_ust_yield_rates() for the latest date.
        de_10y_bars: EOD bars for DE10Y.GBOND (or fallback).
        jp_10y_bars: EOD bars for JP10Y.GBOND (or fallback).
        real_yield_rows: Optional rows from get_ust_real_yield_rates().
    """
    if not ust_rows:
        return None

    # Group UST rows by date, take latest
    by_date: Dict[str, Dict[str, float]] = {}
    for row in ust_rows:
        d = str(row.get("date", ""))[:10]
        tenor = str(row.get("tenor", ""))
        rate = _to_float(row.get("rate"))
        if d and tenor and rate is not None:
            if d not in by_date:
                by_date[d] = {}
            by_date[d][tenor] = rate

    if not by_date:
        return None

    latest_date = max(by_date.keys())
    tenors = by_date[latest_date]

    us_2y = tenors.get("2Y")
    us_10y = tenors.get("10Y")
    if us_2y is None or us_10y is None:
        return None

    # Non-US yields
    de_10y: Optional[float] = None
    if de_10y_bars:
        latest_de = sorted(de_10y_bars, key=lambda b: str(b.get("date", "")))[-1]
        de_10y = _to_float(latest_de.get("adjusted_close")) or _to_float(latest_de.get("close"))

    jp_10y: Optional[float] = None
    if jp_10y_bars:
        latest_jp = sorted(jp_10y_bars, key=lambda b: str(b.get("date", "")))[-1]
        jp_10y = _to_float(latest_jp.get("adjusted_close")) or _to_float(latest_jp.get("close"))

    # Real yields
    us_real_10y: Optional[float] = None
    if real_yield_rows:
        for row in sorted(real_yield_rows, key=lambda r: str(r.get("date", "")), reverse=True):
            if str(row.get("tenor", "")) == "10Y":
                us_real_10y = _to_float(row.get("rate"))
                break

    return YieldSnapshot(
        date=latest_date,
        us_2y=round(us_2y, 4),
        us_10y=round(us_10y, 4),
        us_2s10s_slope=round(us_10y - us_2y, 4),
        de_10y=round(de_10y, 4) if de_10y is not None else None,
        jp_10y=round(jp_10y, 4) if jp_10y is not None else None,
        us_real_10y=round(us_real_10y, 4) if us_real_10y is not None else None,
    )


# ---------------------------------------------------------------------------
# Global summary builder
# ---------------------------------------------------------------------------


def build_global_summary(bars: List[GlobalAssetBar]) -> Dict[str, Any]:
    """Build a human-readable global summary dict from today's bars."""
    by_region: Dict[str, List[dict]] = {}
    for b in bars:
        region = b.region or "other"
        if region not in by_region:
            by_region[region] = []
        by_region[region].append(b.to_dict())

    return {
        "date": bars[0].date if bars else None,
        "assetCount": len(bars),
        "byRegion": by_region,
    }
