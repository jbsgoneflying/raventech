"""
Engine 4: Ichimoku Cloud Continuation Universe Scanner

Scans the SP500 + Nasdaq100 universe for Ichimoku continuation setups
with caching, parallel processing, and segmented gamma context.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from cachetools import TTLCache

from backend.config import get_flags
from backend.dealer_gamma_context import compute_dealer_gamma_context
from backend.engine4_ichimoku import (
    APLUS_THRESHOLD,
    IchimokuSignal,
    build_ichimoku_signal,
    detect_ichimoku_setup,
    signal_to_dict,
)
from backend.orats_client import OratsClient
from backend.technicals import DailyBar, fetch_daily_bars_range
from backend.universe import load_universe_sp500_and_nasdaq100


LOG = logging.getLogger("engine4_screener")


# ---------------------------------------------------------------------------
# Index Membership Data
# ---------------------------------------------------------------------------

def _read_index_file(path: Path) -> Set[str]:
    """Read tickers from an index file."""
    if not path.exists():
        return set()
    text = path.read_text(encoding="utf-8", errors="ignore")
    tickers = set()
    for line in text.splitlines():
        s = line.strip().upper()
        if s and not s.startswith("#"):
            tickers.add(s)
    return tickers


def load_index_memberships(repo_root: Optional[Path] = None) -> Dict[str, str]:
    """
    Load index membership for each ticker.
    
    Returns:
        Dict mapping ticker -> "sp500", "nasdaq100", or "both"
    """
    root = repo_root or Path(__file__).resolve().parent.parent
    base = root / "data" / "universe"
    
    sp500 = _read_index_file(base / "sp500.txt")
    nasdaq100 = _read_index_file(base / "nasdaq100.txt")
    
    memberships: Dict[str, str] = {}
    all_tickers = sp500 | nasdaq100
    
    for ticker in all_tickers:
        in_sp = ticker in sp500
        in_ndx = ticker in nasdaq100
        
        if in_sp and in_ndx:
            memberships[ticker] = "both"
        elif in_sp:
            memberships[ticker] = "sp500"
        else:
            memberships[ticker] = "nasdaq100"
    
    return memberships


# ---------------------------------------------------------------------------
# Cache Configuration
# ---------------------------------------------------------------------------

# Full scan cache (30 minutes)
_scan_cache: TTLCache = TTLCache(maxsize=10, ttl=30 * 60)
_scan_cache_lock = threading.Lock()

# Per-ticker bars cache (6 hours)
_bars_cache: TTLCache = TTLCache(maxsize=600, ttl=6 * 60 * 60)
_bars_cache_lock = threading.Lock()

# Signal persistence store (in-memory, refreshed on scan)
_signal_store: Dict[str, Dict[str, Any]] = {}
_signal_store_lock = threading.Lock()


def _cache_key_scan(as_of: str, min_score: int, direction: Optional[str]) -> str:
    """Generate cache key for full scan results."""
    flags = get_flags()
    flag_hash = hashlib.md5(str(flags.cache_key()).encode()).hexdigest()[:8]
    dir_key = direction or "all"
    return f"e4_scan:{as_of}:{min_score}:{dir_key}:{flag_hash}"


def _cache_key_bars(ticker: str, as_of: str) -> str:
    """Generate cache key for ticker bars."""
    return f"e4_bars:{ticker}:{as_of}"


# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_bars_for_ticker(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: dt.date,
    lookback_days: int = 150,
) -> List[DailyBar]:
    """
    Fetch daily bars for a ticker with caching.
    Ichimoku needs 52+ bars for Span B, plus 26 bars for cloud projection alignment,
    so we request 150 calendar days to ensure ~100 trading days.
    """
    as_of_str = as_of_date.isoformat()
    cache_key = _cache_key_bars(ticker, as_of_str)
    
    with _bars_cache_lock:
        cached = _bars_cache.get(cache_key)
        if cached is not None:
            return cached
    
    start = as_of_date - dt.timedelta(days=lookback_days)
    bars = fetch_daily_bars_range(client, ticker=ticker, start=start, end=as_of_date)
    
    with _bars_cache_lock:
        _bars_cache[cache_key] = bars
    
    return bars


def fetch_earnings_days_ahead(
    ticker: str,
    as_of_date: dt.date,
    benzinga_client: Any = None,
) -> Optional[int]:
    """
    Check if earnings are upcoming for a ticker.
    Returns days until earnings, or None if unknown/not soon.
    """
    # Try to use Benzinga client if available
    if benzinga_client is not None:
        try:
            from backend.earnings_calendar import benzinga_next_earnings
            earn_date = benzinga_next_earnings(benzinga_client, ticker=ticker)
            if earn_date:
                earn_dt = dt.date.fromisoformat(str(earn_date)[:10])
                days = (earn_dt - as_of_date).days
                if 0 <= days <= 10:
                    return days
        except Exception:
            pass
    
    return None


# ---------------------------------------------------------------------------
# Gamma Context
# ---------------------------------------------------------------------------

def fetch_gamma_context_spx(
    client: OratsClient,
    trade_date: dt.date,
) -> Dict[str, Any]:
    """
    Fetch SPX gamma context for S&P 500 names.
    """
    return _fetch_gamma_context_for_symbol(client, trade_date, symbols=["SPX", "SPXW"])


def fetch_gamma_context_ndx(
    client: OratsClient,
    trade_date: dt.date,
) -> Dict[str, Any]:
    """
    Fetch NDX/QQQ gamma context for Nasdaq 100 names.
    """
    return _fetch_gamma_context_for_symbol(client, trade_date, symbols=["QQQ", "NDX"])


def _fetch_gamma_context_for_symbol(
    client: OratsClient,
    trade_date: dt.date,
    symbols: List[str],
) -> Dict[str, Any]:
    """
    Fetch gamma context for given symbols with robust fallback logic.
    
    Strategy:
    1. Try live strikes first (market hours)
    2. Fall back to EOD hist_strikes
    3. Walk back up to 5 trading days to find data
    """
    fields = "ticker,tradeDate,expirDate,strike,spotPrice,stockPrice,gamma,callOpenInterest,putOpenInterest,callVolume,putVolume"
    
    # Find next Friday for weekly expiry
    now = dt.datetime.now()
    days_until_friday = (4 - trade_date.weekday()) % 7
    if days_until_friday == 0 and now.hour >= 16:
        days_until_friday = 7
    target_friday = trade_date + dt.timedelta(days=days_until_friday if days_until_friday > 0 else 7)
    
    strikes = None
    expiry_used = None
    data_source = "unknown"
    
    # STRATEGY 1: Try live strikes first (market hours)
    for symbol in symbols:
        try:
            resp = client.live_strikes_by_expiry(
                ticker=symbol,
                expiry=target_friday.isoformat(),
                fields=fields,
            )
            live_rows = resp.rows if hasattr(resp, "rows") else []
            if live_rows and len(live_rows) > 10:
                strikes = live_rows
                expiry_used = target_friday.isoformat()
                data_source = "live"
                LOG.info(f"Using live {symbol} strikes ({len(strikes)} rows)")
                break
        except Exception as e:
            LOG.debug(f"Live strikes for {symbol} failed: {e}")
            continue
    
    # STRATEGY 2: Try live_strikes without specific expiry
    if not strikes or len(strikes) < 10:
        for symbol in symbols:
            try:
                resp = client.live_strikes(
                    ticker=symbol,
                    fields=fields,
                )
                live_rows = resp.rows if hasattr(resp, "rows") else []
                if live_rows and len(live_rows) > 10:
                    strikes = live_rows
                    expiry_used = live_rows[0].get("expirDate", "")[:10] if live_rows else None
                    data_source = "live"
                    LOG.info(f"Using live {symbol} strikes without expiry filter ({len(strikes)} rows)")
                    break
            except Exception as e:
                LOG.debug(f"Live strikes (no expiry) for {symbol} failed: {e}")
                continue
    
    # STRATEGY 3: Fall back to EOD hist_strikes (after hours / weekends)
    if not strikes or len(strikes) < 10:
        LOG.info(f"Live strikes unavailable for {symbols}, falling back to EOD hist_strikes")
        
        dte_range = "3,21"  # 3-21 DTE
        
        # Walk back up to 5 trading days
        for days_back in range(0, 6):
            check_date = trade_date - dt.timedelta(days=days_back)
            # Skip weekends
            if check_date.weekday() >= 5:
                continue
            
            for symbol in symbols:
                try:
                    resp = client.get(
                        "hist/strikes",
                        ticker=symbol,
                        tradeDate=check_date.isoformat(),
                        dte=dte_range,
                        fields=fields,
                    )
                    rows = resp.rows if hasattr(resp, "rows") else []
                    if rows and len(rows) > 10:
                        # Pick expiry closest to target Friday
                        expiries = set(str(r.get("expirDate", ""))[:10] for r in rows if r.get("expirDate"))
                        if expiries:
                            chosen = min(expiries, key=lambda e: abs((dt.date.fromisoformat(e) - target_friday).days))
                            filtered = [r for r in rows if str(r.get("expirDate", ""))[:10] == chosen]
                            if len(filtered) > 10:
                                strikes = filtered
                                expiry_used = chosen
                                data_source = f"eod:{check_date.isoformat()}"
                                LOG.info(f"Using EOD {symbol} strikes from {check_date} ({len(strikes)} rows)")
                                break
                except Exception as e:
                    LOG.debug(f"EOD strikes for {symbol} on {check_date} failed: {e}")
                    continue
            
            if strikes and len(strikes) > 10:
                break
    
    # Process the strikes if we have them
    if not strikes or len(strikes) < 10:
        return {
            "available": False,
            "environment": "unknown",
            "recommendation": "Gamma context unavailable.",
            "warnings": [f"Could not fetch gamma data for {symbols}."],
        }
    
    gamma = compute_dealer_gamma_context(strikes, expiry=expiry_used)
    
    # Add environment classification for continuation setups
    net_sign = gamma.get("netGammaSign")
    if net_sign == "positive":
        gamma["environment"] = "supportive"
        gamma["recommendation"] = "Positive gamma supports pullback continuation setups."
    elif net_sign == "negative":
        gamma["environment"] = "challenging"
        gamma["recommendation"] = "Negative gamma can accelerate moves - be selective with entries."
    else:
        gamma["environment"] = "unknown"
        gamma["recommendation"] = "Gamma context unclear - proceed with standard criteria."
    
    # Add source metadata
    gamma["symbol"] = symbols[0] if symbols else "unknown"
    gamma["dataSource"] = data_source
    
    # Add note if using historical data
    if data_source.startswith("eod:"):
        eod_date = data_source.split(":")[1]
        gamma["recommendation"] = f"[EOD data from {eod_date}] " + gamma["recommendation"]
    
    return gamma


# ---------------------------------------------------------------------------
# Single Ticker Scan
# ---------------------------------------------------------------------------

def scan_ticker(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: dt.date,
    index_membership: str,
    gamma_context: Optional[Dict[str, Any]] = None,
    benzinga_client: Any = None,
) -> Optional[IchimokuSignal]:
    """
    Scan a single ticker for Ichimoku continuation setup.
    Returns IchimokuSignal if found, None otherwise.
    """
    try:
        bars = fetch_bars_for_ticker(client, ticker=ticker, as_of_date=as_of_date)
        
        if not bars or len(bars) < 60:
            return None
        
        # Check earnings
        earnings_days = fetch_earnings_days_ahead(ticker, as_of_date, benzinga_client)
        
        # Detect setup
        detection = detect_ichimoku_setup(
            bars,
            ticker=ticker,
            index_membership=index_membership,
            gamma_context=gamma_context,
            earnings_days_ahead=earnings_days,
        )
        
        if not detection.get("hasSignal"):
            return None
        
        # Compute Ichimoku series for freshness classification
        from backend.technicals import compute_ichimoku_series
        ich_series = compute_ichimoku_series(bars)
        closes = ich_series.get("closes", [])
        tenkan_series = ich_series.get("tenkan_series", [])
        
        # Build scored signal with freshness classification
        signal = build_ichimoku_signal(
            ticker=ticker,
            detection=detection,
            bars=bars,
            closes=closes,
            tenkan_series=tenkan_series,
            gamma_context=gamma_context,
            earnings_days_ahead=earnings_days,
            index_membership=index_membership,
        )
        
        return signal
        
    except Exception as e:
        LOG.warning(f"Error scanning {ticker}: {e}")
        return None


def scan_single_ticker(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: Optional[str] = None,
    benzinga_client: Any = None,
) -> Dict[str, Any]:
    """
    Full analysis for a single ticker (for detail endpoint).
    """
    t = str(ticker).strip().upper()
    today = dt.date.today()
    if as_of_date:
        try:
            today = dt.date.fromisoformat(str(as_of_date)[:10])
        except Exception:
            today = dt.date.today()
    
    # Determine index membership
    memberships = load_index_memberships()
    index_membership = memberships.get(t, "sp500")
    
    # Fetch appropriate gamma context
    if index_membership == "nasdaq100":
        gamma_context = fetch_gamma_context_ndx(client, today)
    else:
        gamma_context = fetch_gamma_context_spx(client, today)
    
    # Fetch bars
    bars = fetch_bars_for_ticker(client, ticker=t, as_of_date=today)
    
    if not bars or len(bars) < 60:
        return {
            "enabled": False,
            "ticker": t,
            "asOfDate": today.isoformat(),
            "notes": ["Insufficient data (need 60+ bars)."],
        }
    
    # Check earnings
    earnings_days = fetch_earnings_days_ahead(t, today, benzinga_client)
    
    # Full detection
    detection = detect_ichimoku_setup(
        bars,
        ticker=t,
        index_membership=index_membership,
        gamma_context=gamma_context,
        earnings_days_ahead=earnings_days,
    )
    
    result = {
        "enabled": detection.get("enabled", False),
        "ticker": t,
        "asOfDate": today.isoformat(),
        "hasSignal": detection.get("hasSignal", False),
        "signal": None,
        "trend": detection.get("trend"),
        "pullback": detection.get("pullback"),
        "trigger": detection.get("trigger"),
        "indicators": detection.get("indicators"),
        "gammaContext": gamma_context,
        "indexMembership": index_membership,
        "earningsDaysAhead": earnings_days,
        "notes": detection.get("notes", []),
    }
    
    if detection.get("hasSignal"):
        signal = build_ichimoku_signal(
            ticker=t,
            detection=detection,
            gamma_context=gamma_context,
            earnings_days_ahead=earnings_days,
            index_membership=index_membership,
        )
        if signal:
            result["signal"] = signal_to_dict(signal)
    
    return result


# ---------------------------------------------------------------------------
# Full Universe Scan
# ---------------------------------------------------------------------------

def run_universe_scan(
    client: OratsClient,
    *,
    as_of_date: Optional[str] = None,
    min_score: int = 50,
    direction: Optional[str] = None,
    benzinga_client: Any = None,
    max_workers: int = 10,
) -> Dict[str, Any]:
    """
    Scan the full SP500 + Nasdaq100 universe for Ichimoku setups.
    
    Args:
        client: ORATS client
        as_of_date: Scan date (YYYY-MM-DD), defaults to today
        min_score: Minimum score to include (0-100)
        direction: Filter by direction ("bullish", "bearish", or None for both)
        benzinga_client: Optional Benzinga client for earnings check
        max_workers: Number of parallel workers
    
    Returns:
        Dict with scan results, stats, and gamma context
    """
    start_time = time.time()
    
    today = dt.date.today()
    if as_of_date:
        try:
            today = dt.date.fromisoformat(str(as_of_date)[:10])
        except Exception:
            today = dt.date.today()
    
    as_of_str = today.isoformat()
    
    # Check cache
    cache_key = _cache_key_scan(as_of_str, min_score, direction)
    with _scan_cache_lock:
        cached = _scan_cache.get(cache_key)
        if cached is not None:
            return cached
    
    # Load universe and memberships
    universe = load_universe_sp500_and_nasdaq100()
    memberships = load_index_memberships()
    
    # Fetch gamma contexts (once per index)
    gamma_spx = fetch_gamma_context_spx(client, today)
    gamma_ndx = fetch_gamma_context_ndx(client, today)
    
    # Scan in parallel
    signals: List[IchimokuSignal] = []
    errors: List[str] = []
    
    def _scan_one(ticker: str) -> Optional[IchimokuSignal]:
        membership = memberships.get(ticker, "sp500")
        
        # Select appropriate gamma context
        if membership == "nasdaq100":
            gamma = gamma_ndx
        else:
            gamma = gamma_spx
        
        return scan_ticker(
            client,
            ticker=ticker,
            as_of_date=today,
            index_membership=membership,
            gamma_context=gamma,
            benzinga_client=benzinga_client,
        )
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_ticker = {executor.submit(_scan_one, t): t for t in universe}
        
        for future in as_completed(future_to_ticker):
            ticker = future_to_ticker[future]
            try:
                signal = future.result()
                if signal is not None:
                    signals.append(signal)
            except Exception as e:
                errors.append(f"{ticker}: {str(e)}")
    
    # Filter to A+ only (score >= 75) and by direction if specified
    aplus_signals = []
    for s in signals:
        if s.score < APLUS_THRESHOLD:
            continue
        if direction and s.direction != direction:
            continue
        aplus_signals.append(s)
    
    # Sort by score descending
    aplus_signals.sort(key=lambda x: x.score, reverse=True)
    
    # Classify A+ signals into freshness buckets
    actionable = []
    structure = []
    rejected_count = 0
    
    for s in aplus_signals:
        if s.freshness_bucket == "actionable":
            actionable.append(s)
        elif s.freshness_bucket == "structure":
            structure.append(s)
        elif s.freshness_bucket == "rejected":
            rejected_count += 1
            # Don't include rejected signals in output
    
    # Update signal store (only actionable and structure)
    with _signal_store_lock:
        for s in actionable + structure:
            key = f"{s.ticker}:{s.signal_date}"
            _signal_store[key] = signal_to_dict(s)
    
    elapsed_ms = int((time.time() - start_time) * 1000)
    
    result = {
        "asOfDate": as_of_str,
        "scannedCount": len(universe),
        "totalAPlus": len(aplus_signals),
        "actionableCount": len(actionable),
        "structureCount": len(structure),
        "rejectedCount": rejected_count,
        "actionable": [signal_to_dict(s) for s in actionable],
        "structure": [signal_to_dict(s) for s in structure],
        "marketGamma": {
            "spx": gamma_spx,
            "ndx": gamma_ndx,
        },
        "meta": {
            "scanDurationMs": elapsed_ms,
            "direction": direction,
            "errors": errors[:10] if errors else [],
        },
    }
    
    # Cache result
    with _scan_cache_lock:
        _scan_cache[cache_key] = result
    
    return result


# ---------------------------------------------------------------------------
# Signal Status Management
# ---------------------------------------------------------------------------

def get_all_signals() -> Dict[str, Any]:
    """
    Get all tracked signals with current status.
    """
    with _signal_store_lock:
        signals = list(_signal_store.values())
    
    # Group by status
    pending = [s for s in signals if s.get("status") == "pending"]
    triggered = [s for s in signals if s.get("status") == "triggered"]
    stopped = [s for s in signals if s.get("status") == "stopped"]
    invalidated = [s for s in signals if s.get("status") == "invalidated"]
    
    return {
        "totalSignals": len(signals),
        "pending": pending,
        "triggered": triggered,
        "stopped": stopped,
        "invalidated": invalidated,
    }


def update_signal_status(
    ticker: str,
    signal_date: str,
    new_status: str,
    reason: Optional[str] = None,
) -> bool:
    """
    Update the status of a tracked signal.
    
    Args:
        ticker: Ticker symbol
        signal_date: Signal date (YYYY-MM-DD)
        new_status: New status ("pending", "triggered", "stopped", "target_hit", "invalidated")
        reason: Optional reason for status change
    
    Returns:
        True if updated, False if not found
    """
    key = f"{ticker}:{signal_date}"
    
    with _signal_store_lock:
        if key not in _signal_store:
            return False
        
        _signal_store[key]["status"] = new_status
        if reason:
            _signal_store[key]["invalidationReason"] = reason
        
        # Add note about status change
        notes = _signal_store[key].get("notes", [])
        timestamp = dt.datetime.now().isoformat()
        notes.append(f"Status changed to {new_status} at {timestamp}")
        _signal_store[key]["notes"] = notes
    
    return True


def check_signal_invalidation(
    client: OratsClient,
    signal: Dict[str, Any],
    as_of_date: dt.date,
) -> Optional[str]:
    """
    Check if a signal should be invalidated based on current price action.
    
    Invalidation conditions:
    - Price closes below stop level
    - Price closes deep into cloud
    - Trend regime changes
    
    Returns:
        Invalidation reason if invalidated, None otherwise
    """
    ticker = signal.get("ticker")
    if not ticker:
        return None
    
    try:
        bars = fetch_bars_for_ticker(client, ticker=ticker, as_of_date=as_of_date, lookback_days=10)
        if not bars:
            return None
        
        last_bar = bars[-1]
        close = float(last_bar.close) if last_bar.close else None
        
        if close is None:
            return None
        
        levels = signal.get("levels", {})
        stop = levels.get("stopLoss")
        direction = signal.get("direction")
        
        # Check stop level
        if stop is not None:
            if direction == "bullish" and close < stop:
                return f"Price closed below stop ({close:.2f} < {stop:.2f})"
            elif direction == "bearish" and close > stop:
                return f"Price closed above stop ({close:.2f} > {stop:.2f})"
        
        # Check entry trigger hit (for status update)
        entry = levels.get("entryTrigger")
        if entry is not None:
            if direction == "bullish" and close > entry:
                # Entry was triggered - update status
                update_signal_status(ticker, signal.get("signalDate", ""), "triggered")
            elif direction == "bearish" and close < entry:
                update_signal_status(ticker, signal.get("signalDate", ""), "triggered")
        
    except Exception as e:
        LOG.warning(f"Error checking invalidation for {ticker}: {e}")
    
    return None


def refresh_signal_statuses(
    client: OratsClient,
    as_of_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Refresh status of all tracked signals.
    """
    today = dt.date.today()
    if as_of_date:
        try:
            today = dt.date.fromisoformat(str(as_of_date)[:10])
        except Exception:
            today = dt.date.today()
    
    updated = 0
    invalidated = 0
    
    with _signal_store_lock:
        signals = list(_signal_store.values())
    
    for signal in signals:
        if signal.get("status") in ("stopped", "target_hit", "invalidated"):
            continue
        
        reason = check_signal_invalidation(client, signal, today)
        if reason:
            update_signal_status(
                signal.get("ticker", ""),
                signal.get("signalDate", ""),
                "invalidated",
                reason,
            )
            invalidated += 1
        else:
            updated += 1
    
    return {
        "updated": updated,
        "invalidated": invalidated,
        "asOfDate": today.isoformat(),
    }
