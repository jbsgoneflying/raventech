"""
Engine 3: Red Dog Reversal Universe Scanner

Scans the SP100 + Nasdaq100 universe for Red Dog Reversal setups
with caching and parallel processing.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from cachetools import TTLCache

from backend.config import get_flags
from backend.orats_client import OratsClient
from backend.technicals import DailyBar, fetch_daily_bars_range
from backend.universe import load_universe_sp500_and_nasdaq100
from backend.engine3_red_dog import (
    APLUS_THRESHOLD,
    RedDogSignal,
    build_red_dog_signal,
    detect_red_dog_enhanced,
    signal_to_dict,
)


LOG = logging.getLogger("engine3_screener")


# ---------------------------------------------------------------------------
# Cache Configuration
# ---------------------------------------------------------------------------

# Full scan cache (30 minutes - refreshes mid-day and EOD)
_scan_cache: TTLCache = TTLCache(maxsize=10, ttl=30 * 60)
_scan_cache_lock = threading.Lock()

# Per-ticker bars cache (6 hours - daily data doesn't change much)
_bars_cache: TTLCache = TTLCache(maxsize=500, ttl=6 * 60 * 60)
_bars_cache_lock = threading.Lock()


def _cache_key_scan(as_of: str, min_score: int, direction: Optional[str]) -> str:
    """Generate cache key for full scan results."""
    flags = get_flags()
    flag_hash = hashlib.md5(str(flags.cache_key()).encode()).hexdigest()[:8]
    dir_key = direction or "all"
    return f"e3_scan:{as_of}:{min_score}:{dir_key}:{flag_hash}"


def _cache_key_bars(ticker: str, as_of: str) -> str:
    """Generate cache key for ticker bars."""
    return f"e3_bars:{ticker}:{as_of}"


# ---------------------------------------------------------------------------
# Data Fetching
# ---------------------------------------------------------------------------

def fetch_bars_for_ticker(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: dt.date,
    lookback_days: int = 60,
) -> List[DailyBar]:
    """
    Fetch daily bars for a ticker with caching.
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


def scan_ticker(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: dt.date,
) -> Optional[RedDogSignal]:
    """
    Scan a single ticker for Red Dog setup.
    Returns RedDogSignal if found, None otherwise.
    """
    try:
        bars = fetch_bars_for_ticker(client, ticker=ticker, as_of_date=as_of_date)
        
        if not bars or len(bars) < 21:
            return None
        
        detection = detect_red_dog_enhanced(bars, ticker=ticker)
        
        if not detection.get("bullish") and not detection.get("bearish"):
            return None
        
        signal = build_red_dog_signal(
            ticker=ticker,
            detection=detection,
            near_support_resistance=False,  # TODO: Add S/R detection
        )
        
        return signal
        
    except Exception as e:
        LOG.warning(f"Error scanning {ticker}: {e}")
        return None


# ---------------------------------------------------------------------------
# Universe Scanning
# ---------------------------------------------------------------------------

@dataclass
class ScanResult:
    """Results from a full universe scan."""
    as_of_date: str
    scanned_count: int
    setups_found: int
    a_plus: List[Dict[str, Any]]      # Score >= 75
    standard: List[Dict[str, Any]]    # Score 50-74
    below_threshold: List[Dict[str, Any]]  # Score < 50
    errors: List[str]
    scan_duration_ms: int
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "asOfDate": self.as_of_date,
            "scannedCount": self.scanned_count,
            "setupsFound": self.setups_found,
            "aPlus": self.a_plus,
            "standard": self.standard,
            "belowThreshold": self.below_threshold,
            "watchlist": sorted(
                self.a_plus + self.standard,
                key=lambda x: x.get("quality", {}).get("score", 0),
                reverse=True,
            ),
            "meta": {
                "scanDurationMs": self.scan_duration_ms,
                "errorCount": len(self.errors),
            },
        }


def compute_engine3_scan(
    client: OratsClient,
    *,
    as_of_date: Optional[str] = None,
    min_score: int = 50,
    direction: Optional[str] = None,  # "bullish", "bearish", or None for both
    max_workers: int = 10,
    use_cache: bool = True,
) -> Dict[str, Any]:
    """
    Scan the full universe for Red Dog setups.
    
    Args:
        client: ORATS client
        as_of_date: Date to scan (default: today)
        min_score: Minimum score to include in results (default: 50)
        direction: Filter by direction ("bullish", "bearish", or None)
        max_workers: Parallel workers for scanning
        use_cache: Whether to use cached results
    
    Returns:
        ScanResult as dict
    """
    # Parse date
    if as_of_date:
        try:
            scan_date = dt.date.fromisoformat(str(as_of_date)[:10])
        except ValueError:
            scan_date = dt.date.today()
    else:
        scan_date = dt.date.today()
    
    as_of_str = scan_date.isoformat()
    
    # Check cache
    if use_cache:
        cache_key = _cache_key_scan(as_of_str, min_score, direction)
        with _scan_cache_lock:
            cached = _scan_cache.get(cache_key)
            if cached is not None:
                LOG.info(f"Engine 3 scan cache hit for {as_of_str}")
                return cached
    
    start_time = time.time()
    
    # Load universe
    universe = load_universe_sp500_and_nasdaq100()
    LOG.info(f"Engine 3 scanning {len(universe)} tickers for {as_of_str}")
    
    # Scan in parallel
    signals: List[RedDogSignal] = []
    errors: List[str] = []
    
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {
            executor.submit(scan_ticker, client, ticker=ticker, as_of_date=scan_date): ticker
            for ticker in universe
        }
        
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                signal = future.result()
                if signal is not None:
                    # Apply direction filter
                    if direction:
                        if direction.lower() != signal.direction:
                            continue
                    
                    # Apply score filter
                    if signal.score >= min_score:
                        signals.append(signal)
                        
            except Exception as e:
                errors.append(f"{ticker}: {str(e)}")
    
    # Sort by score descending
    signals.sort(key=lambda s: s.score, reverse=True)
    
    # Categorize
    a_plus = [signal_to_dict(s) for s in signals if s.score >= APLUS_THRESHOLD]
    standard = [signal_to_dict(s) for s in signals if 50 <= s.score < APLUS_THRESHOLD]
    below_threshold = [signal_to_dict(s) for s in signals if s.score < 50]
    
    duration_ms = int((time.time() - start_time) * 1000)
    
    result = ScanResult(
        as_of_date=as_of_str,
        scanned_count=len(universe),
        setups_found=len(signals),
        a_plus=a_plus,
        standard=standard,
        below_threshold=below_threshold,
        errors=errors[:10],  # Limit error reporting
        scan_duration_ms=duration_ms,
    )
    
    result_dict = result.to_dict()
    
    # Cache result
    if use_cache:
        cache_key = _cache_key_scan(as_of_str, min_score, direction)
        with _scan_cache_lock:
            _scan_cache[cache_key] = result_dict
    
    LOG.info(
        f"Engine 3 scan complete: {len(signals)} setups found "
        f"({len(a_plus)} A+, {len(standard)} standard) in {duration_ms}ms"
    )
    
    return result_dict


def compute_single_ticker_scan(
    client: OratsClient,
    *,
    ticker: str,
    as_of_date: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Scan a single ticker for Red Dog setup with full details.
    """
    # Parse date
    if as_of_date:
        try:
            scan_date = dt.date.fromisoformat(str(as_of_date)[:10])
        except ValueError:
            scan_date = dt.date.today()
    else:
        scan_date = dt.date.today()
    
    ticker = ticker.upper().strip()
    
    try:
        bars = fetch_bars_for_ticker(
            client,
            ticker=ticker,
            as_of_date=scan_date,
            lookback_days=60,
        )
        
        if not bars or len(bars) < 21:
            return {
                "ticker": ticker,
                "asOfDate": scan_date.isoformat(),
                "enabled": False,
                "signal": None,
                "notes": ["Insufficient bar history."],
            }
        
        detection = detect_red_dog_enhanced(bars, ticker=ticker)
        signal = build_red_dog_signal(
            ticker=ticker,
            detection=detection,
            near_support_resistance=False,
        )
        
        return {
            "ticker": ticker,
            "asOfDate": scan_date.isoformat(),
            "enabled": True,
            "hasSignal": signal is not None,
            "signal": signal_to_dict(signal) if signal else None,
            "indicators": detection.get("indicators", {}),
            "notes": detection.get("notes", []),
        }
        
    except Exception as e:
        LOG.error(f"Error scanning {ticker}: {e}")
        return {
            "ticker": ticker,
            "asOfDate": scan_date.isoformat(),
            "enabled": False,
            "signal": None,
            "notes": [f"Error: {str(e)}"],
        }


# ---------------------------------------------------------------------------
# Watchlist Generation
# ---------------------------------------------------------------------------

def generate_watchlist(
    scan_result: Dict[str, Any],
    *,
    max_items: int = 20,
    include_standard: bool = True,
) -> List[Dict[str, Any]]:
    """
    Generate a prioritized watchlist from scan results.
    
    Priority order:
    1. A+ bullish (high score first)
    2. A+ bearish (high score first)
    3. Standard (if included)
    """
    watchlist: List[Dict[str, Any]] = []
    
    a_plus = scan_result.get("aPlus", [])
    standard = scan_result.get("standard", []) if include_standard else []
    
    # Combine and sort by score
    all_signals = a_plus + standard
    all_signals.sort(key=lambda x: x.get("quality", {}).get("score", 0), reverse=True)
    
    return all_signals[:max_items]


def format_watchlist_summary(watchlist: List[Dict[str, Any]]) -> str:
    """
    Format watchlist for display/logging.
    """
    if not watchlist:
        return "No Red Dog setups found."
    
    lines = ["Red Dog Reversal Watchlist", "=" * 40]
    
    for item in watchlist:
        ticker = item.get("ticker", "???")
        direction = item.get("direction", "?")[0].upper()
        score = item.get("quality", {}).get("score", 0)
        grade = item.get("quality", {}).get("grade", "?")
        entry = item.get("levels", {}).get("entryTrigger", 0)
        stop = item.get("levels", {}).get("stopLoss", 0)
        
        lines.append(
            f"{ticker:6} {direction} | Score: {score:3} ({grade:2}) | "
            f"Entry: ${entry:.2f} | Stop: ${stop:.2f}"
        )
    
    return "\n".join(lines)
