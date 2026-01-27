"""
Engine 3: Red Dog Reversal Trading System

A mean reversion scanner that identifies failed breakdown/breakout setups
with A+ quality scoring for the SP100 + Nasdaq100 universe.

Based on the Bottoming Tail (BT) / Topping Tail (TT) patterns from Pristine Trading,
popularized by Scott Redler as the "Red Dog Reversal."
"""

from __future__ import annotations

import datetime as dt
import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from backend.technicals import DailyBar


# ---------------------------------------------------------------------------
# Constants & Scoring Weights
# ---------------------------------------------------------------------------

# A+ threshold: setups scoring >= 75 are considered high-quality
APLUS_THRESHOLD = 75

# Scoring weights (total possible = 100)
SCORE_RSI_EXTREME = 25          # RSI <= 30 (bullish) or >= 70 (bearish)
SCORE_STOCHASTICS_EXTREME = 15  # Stochastics <= 20 (bullish) or >= 80 (bearish)
SCORE_SMA20_DEVIATION = 20      # Price >9% away from SMA20
SCORE_VOLUME_SURGE = 15         # Volume > 1.5x 20-day average
SCORE_CLOSE_POSITION = 15       # Strong close in favorable zone
SCORE_SR_CONFLUENCE = 10        # Support/resistance confluence

# Thresholds
RSI_OVERSOLD = 30.0
RSI_OVERBOUGHT = 70.0
STOCH_OVERSOLD = 20.0
STOCH_OVERBOUGHT = 80.0
SMA20_DEVIATION_PCT = 9.0       # >9% from SMA20 for extreme
VOLUME_SURGE_MULT = 1.5         # 1.5x average volume
CLOSE_POSITION_STRONG_BULLISH = 0.70  # Close in top 30% of range
CLOSE_POSITION_STRONG_BEARISH = 0.30  # Close in bottom 30% of range


@dataclass(frozen=True)
class RedDogSignal:
    """Red Dog Reversal signal with A+ scoring."""
    ticker: str
    signal_date: str
    direction: str  # "bullish" or "bearish"
    
    # Pattern details
    low_a: float           # Prior day's low (bullish) or high (bearish)
    low_b: float           # Intraday extreme (stop level)
    close: float           # Reversal day close
    close_position: float  # 0-1, where close is within day's range
    
    # Entry/exit levels
    entry_trigger: float   # Buy stop above high (bullish) or sell stop below low (bearish)
    stop_loss: float       # At Low B / High B
    target_1: float        # 1R target
    target_2: float        # 2R target
    target_sma20: float    # Mean reversion target
    
    # Risk metrics
    risk_dollars: float    # Entry - Stop
    reward_1r: float       # Target 1 - Entry
    
    # Quality scoring
    score: int             # 0-100 composite score
    grade: str             # "A+", "A", "B", "C"
    
    # Component scores (for transparency)
    rsi_score: int
    stochastics_score: int
    sma20_deviation_score: int
    volume_score: int
    close_position_score: int
    sr_confluence_score: int
    
    # Indicator values
    rsi: Optional[float]
    stochastics: Optional[float]
    sma20: Optional[float]
    sma20_deviation_pct: Optional[float]
    volume_ratio: Optional[float]
    atr14: Optional[float]
    
    # Metadata
    strength: str          # "strong" or "standard"
    notes: List[str]


def _compute_sma(values: List[float], period: int) -> Optional[float]:
    """Compute simple moving average."""
    if len(values) < period:
        return None
    return sum(values[-period:]) / period


def _compute_stochastics(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> Optional[float]:
    """
    Compute %K of Stochastics oscillator.
    %K = (Close - Lowest Low) / (Highest High - Lowest Low) * 100
    """
    if len(highs) < period or len(lows) < period or len(closes) < period:
        return None
    
    recent_highs = highs[-period:]
    recent_lows = lows[-period:]
    highest = max(recent_highs)
    lowest = min(recent_lows)
    current_close = closes[-1]
    
    denom = highest - lowest
    if denom <= 0:
        return 50.0  # Default to neutral if no range
    
    k = ((current_close - lowest) / denom) * 100.0
    return max(0.0, min(100.0, k))


def _compute_atr(
    highs: List[float],
    lows: List[float],
    closes: List[float],
    period: int = 14,
) -> Optional[float]:
    """Compute Average True Range."""
    if len(highs) < period + 1 or len(lows) < period + 1 or len(closes) < period + 1:
        return None
    
    tr_values: List[float] = []
    for i in range(len(closes) - period, len(closes)):
        if i < 1:
            continue
        h = highs[i]
        l = lows[i]
        pc = closes[i - 1]
        tr = max(h - l, abs(h - pc), abs(l - pc))
        tr_values.append(tr)
    
    if not tr_values:
        return None
    return sum(tr_values) / len(tr_values)


def _compute_rsi(closes: List[float], period: int = 14) -> Optional[float]:
    """Compute RSI using Wilder smoothing."""
    if len(closes) < period + 1:
        return None
    
    gains: List[float] = []
    losses: List[float] = []
    
    for i in range(1, len(closes)):
        change = closes[i] - closes[i - 1]
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    
    if len(gains) < period:
        return None
    
    # Initial averages
    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    
    # Wilder smoothing for remaining
    for i in range(period, len(gains)):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
    
    if avg_loss <= 0:
        return 100.0
    
    rs = avg_gain / avg_loss
    rsi = 100.0 - (100.0 / (1.0 + rs))
    return rsi


def detect_red_dog_enhanced(
    bars: List[DailyBar],
    *,
    ticker: str = "",
) -> Dict[str, Any]:
    """
    Enhanced Red Dog detection with full indicator calculation.
    
    Returns a dict with:
    - enabled: bool
    - bullish: bool
    - bearish: bool  
    - pattern: dict with detailed signal info if pattern found
    - indicators: dict with RSI, Stochastics, SMA20, etc.
    """
    result: Dict[str, Any] = {
        "enabled": False,
        "bullish": False,
        "bearish": False,
        "pattern": None,
        "indicators": {},
        "notes": [],
    }
    
    if not bars or len(bars) < 21:
        result["notes"].append("Insufficient bars for Red Dog detection (need 21+).")
        return result
    
    # Extract OHLCV series
    closes = [float(b.close) for b in bars if b.close is not None and b.close > 0]
    highs = [float(b.high) for b in bars if b.high is not None]
    lows = [float(b.low) for b in bars if b.low is not None]
    volumes = [float(b.volume) for b in bars if b.volume is not None and b.volume > 0]
    
    if len(closes) < 21 or len(highs) < 2 or len(lows) < 2:
        result["notes"].append("Insufficient OHLC data.")
        return result
    
    result["enabled"] = True
    
    # Get last two bars for pattern detection
    b1 = bars[-1]  # Today (reversal day)
    b0 = bars[-2]  # Yesterday
    
    if any(v is None for v in (b1.high, b1.low, b1.close, b1.open, b0.high, b0.low)):
        result["notes"].append("Missing OHLC on recent bars.")
        return result
    
    h1, l1, c1, o1 = float(b1.high), float(b1.low), float(b1.close), float(b1.open)
    h0, l0 = float(b0.high), float(b0.low)
    
    # Calculate indicators
    rsi = _compute_rsi(closes, period=14)
    stochastics = _compute_stochastics(highs, lows, closes, period=14)
    sma20 = _compute_sma(closes, period=20)
    atr14 = _compute_atr(highs, lows, closes, period=14)
    
    # Volume ratio
    volume_ratio: Optional[float] = None
    if len(volumes) >= 20 and b1.volume is not None and b1.volume > 0:
        avg_vol = sum(volumes[-20:]) / 20
        if avg_vol > 0:
            volume_ratio = float(b1.volume) / avg_vol
    
    # SMA20 deviation
    sma20_deviation_pct: Optional[float] = None
    if sma20 is not None and sma20 > 0:
        sma20_deviation_pct = ((c1 - sma20) / sma20) * 100.0
    
    result["indicators"] = {
        "rsi": round(rsi, 2) if rsi is not None else None,
        "stochastics": round(stochastics, 2) if stochastics is not None else None,
        "sma20": round(sma20, 4) if sma20 is not None else None,
        "sma20DeviationPct": round(sma20_deviation_pct, 2) if sma20_deviation_pct is not None else None,
        "volumeRatio": round(volume_ratio, 2) if volume_ratio is not None else None,
        "atr14": round(atr14, 4) if atr14 is not None else None,
    }
    
    # Calculate day range and close position
    day_range = max(1e-9, h1 - l1)
    close_position = (c1 - l1) / day_range  # 0 = at low, 1 = at high
    
    # -----------------------------------------------------------------------
    # Bullish Red Dog: today's low < prior low AND today's close > prior low
    # -----------------------------------------------------------------------
    bullish = (l1 < l0) and (c1 > l0)
    
    # -----------------------------------------------------------------------
    # Bearish Red Dog: today's high > prior high AND today's close < prior high
    # -----------------------------------------------------------------------
    bearish = (h1 > h0) and (c1 < h0)
    
    result["bullish"] = bool(bullish)
    result["bearish"] = bool(bearish)
    
    if not bullish and not bearish:
        return result
    
    # Build pattern details
    direction = "bullish" if bullish else "bearish"
    
    if bullish:
        low_a = l0        # Prior day's low
        low_b = l1        # Today's low (stop level)
        entry_trigger = h1 + 0.01  # Buy stop above today's high
        stop_loss = l1 - max(0.10, (atr14 or 0) * 0.25)  # Stop below Low B with buffer
        risk = entry_trigger - stop_loss
        target_1 = entry_trigger + risk      # 1R
        target_2 = entry_trigger + (2 * risk)  # 2R
        target_sma20 = sma20 if sma20 is not None else entry_trigger + risk
    else:  # bearish
        low_a = h0        # Prior day's high (High A)
        low_b = h1        # Today's high (stop level, High B)
        entry_trigger = l1 - 0.01  # Sell stop below today's low
        stop_loss = h1 + max(0.10, (atr14 or 0) * 0.25)  # Stop above High B with buffer
        risk = stop_loss - entry_trigger
        target_1 = entry_trigger - risk      # 1R
        target_2 = entry_trigger - (2 * risk)  # 2R
        target_sma20 = sma20 if sma20 is not None else entry_trigger - risk
    
    result["pattern"] = {
        "direction": direction,
        "signalDate": str(b1.trade_date)[:10],
        "lowA": round(low_a, 4),
        "lowB": round(low_b, 4),
        "close": round(c1, 4),
        "closePosition": round(close_position, 4),
        "entryTrigger": round(entry_trigger, 4),
        "stopLoss": round(stop_loss, 4),
        "riskDollars": round(risk, 4),
        "target1": round(target_1, 4),
        "target2": round(target_2, 4),
        "targetSma20": round(target_sma20, 4),
    }
    
    return result


def score_red_dog_setup(
    *,
    direction: str,
    rsi: Optional[float],
    stochastics: Optional[float],
    sma20_deviation_pct: Optional[float],
    volume_ratio: Optional[float],
    close_position: float,
    near_support_resistance: bool = False,
) -> Tuple[int, Dict[str, int], str]:
    """
    Score a Red Dog setup from 0-100.
    
    Returns:
        (total_score, component_scores, grade)
    """
    scores: Dict[str, int] = {
        "rsi": 0,
        "stochastics": 0,
        "sma20Deviation": 0,
        "volume": 0,
        "closePosition": 0,
        "srConfluence": 0,
    }
    
    is_bullish = direction == "bullish"
    
    # RSI extreme
    if rsi is not None:
        if is_bullish and rsi <= RSI_OVERSOLD:
            scores["rsi"] = SCORE_RSI_EXTREME
        elif not is_bullish and rsi >= RSI_OVERBOUGHT:
            scores["rsi"] = SCORE_RSI_EXTREME
    
    # Stochastics extreme
    if stochastics is not None:
        if is_bullish and stochastics <= STOCH_OVERSOLD:
            scores["stochastics"] = SCORE_STOCHASTICS_EXTREME
        elif not is_bullish and stochastics >= STOCH_OVERBOUGHT:
            scores["stochastics"] = SCORE_STOCHASTICS_EXTREME
    
    # SMA20 deviation (price extreme)
    if sma20_deviation_pct is not None:
        deviation = abs(sma20_deviation_pct)
        if is_bullish and sma20_deviation_pct < 0 and deviation >= SMA20_DEVIATION_PCT:
            scores["sma20Deviation"] = SCORE_SMA20_DEVIATION
        elif not is_bullish and sma20_deviation_pct > 0 and deviation >= SMA20_DEVIATION_PCT:
            scores["sma20Deviation"] = SCORE_SMA20_DEVIATION
    
    # Volume surge
    if volume_ratio is not None and volume_ratio >= VOLUME_SURGE_MULT:
        scores["volume"] = SCORE_VOLUME_SURGE
    
    # Close position (strong reversal close)
    if is_bullish and close_position >= CLOSE_POSITION_STRONG_BULLISH:
        scores["closePosition"] = SCORE_CLOSE_POSITION
    elif not is_bullish and close_position <= CLOSE_POSITION_STRONG_BEARISH:
        scores["closePosition"] = SCORE_CLOSE_POSITION
    
    # Support/resistance confluence
    if near_support_resistance:
        scores["srConfluence"] = SCORE_SR_CONFLUENCE
    
    total = sum(scores.values())
    
    # Determine grade
    if total >= APLUS_THRESHOLD:
        grade = "A+"
    elif total >= 60:
        grade = "A"
    elif total >= 45:
        grade = "B"
    else:
        grade = "C"
    
    return total, scores, grade


def build_red_dog_signal(
    *,
    ticker: str,
    detection: Dict[str, Any],
    near_support_resistance: bool = False,
) -> Optional[RedDogSignal]:
    """
    Build a complete RedDogSignal from detection results.
    """
    if not detection.get("enabled"):
        return None
    
    if not detection.get("bullish") and not detection.get("bearish"):
        return None
    
    pattern = detection.get("pattern")
    indicators = detection.get("indicators") or {}
    
    if not pattern:
        return None
    
    direction = pattern.get("direction", "")
    
    # Score the setup
    total_score, component_scores, grade = score_red_dog_setup(
        direction=direction,
        rsi=indicators.get("rsi"),
        stochastics=indicators.get("stochastics"),
        sma20_deviation_pct=indicators.get("sma20DeviationPct"),
        volume_ratio=indicators.get("volumeRatio"),
        close_position=pattern.get("closePosition", 0.5),
        near_support_resistance=near_support_resistance,
    )
    
    # Determine strength
    close_pos = pattern.get("closePosition", 0.5)
    if direction == "bullish":
        strength = "strong" if close_pos >= 0.70 else "standard"
    else:
        strength = "strong" if close_pos <= 0.30 else "standard"
    
    # Build notes
    notes: List[str] = []
    if grade == "A+":
        notes.append("A+ setup: multiple confirmation factors aligned.")
    if indicators.get("rsi") is not None:
        rsi_val = indicators["rsi"]
        if rsi_val <= 30:
            notes.append(f"RSI oversold at {rsi_val:.1f}")
        elif rsi_val >= 70:
            notes.append(f"RSI overbought at {rsi_val:.1f}")
    if indicators.get("volumeRatio") is not None and indicators["volumeRatio"] >= 1.5:
        notes.append(f"Volume surge: {indicators['volumeRatio']:.1f}x average")
    
    return RedDogSignal(
        ticker=ticker,
        signal_date=pattern.get("signalDate", ""),
        direction=direction,
        low_a=pattern.get("lowA", 0),
        low_b=pattern.get("lowB", 0),
        close=pattern.get("close", 0),
        close_position=close_pos,
        entry_trigger=pattern.get("entryTrigger", 0),
        stop_loss=pattern.get("stopLoss", 0),
        target_1=pattern.get("target1", 0),
        target_2=pattern.get("target2", 0),
        target_sma20=pattern.get("targetSma20", 0),
        risk_dollars=pattern.get("riskDollars", 0),
        reward_1r=abs(pattern.get("target1", 0) - pattern.get("entryTrigger", 0)),
        score=total_score,
        grade=grade,
        rsi_score=component_scores.get("rsi", 0),
        stochastics_score=component_scores.get("stochastics", 0),
        sma20_deviation_score=component_scores.get("sma20Deviation", 0),
        volume_score=component_scores.get("volume", 0),
        close_position_score=component_scores.get("closePosition", 0),
        sr_confluence_score=component_scores.get("srConfluence", 0),
        rsi=indicators.get("rsi"),
        stochastics=indicators.get("stochastics"),
        sma20=indicators.get("sma20"),
        sma20_deviation_pct=indicators.get("sma20DeviationPct"),
        volume_ratio=indicators.get("volumeRatio"),
        atr14=indicators.get("atr14"),
        strength=strength,
        notes=notes,
    )


def signal_to_dict(signal: RedDogSignal) -> Dict[str, Any]:
    """Convert RedDogSignal to API-friendly dict."""
    return {
        "ticker": signal.ticker,
        "signalDate": signal.signal_date,
        "direction": signal.direction,
        "pattern": {
            "lowA": signal.low_a,
            "lowB": signal.low_b,
            "close": signal.close,
            "closePosition": signal.close_position,
        },
        "levels": {
            "entryTrigger": signal.entry_trigger,
            "stopLoss": signal.stop_loss,
            "target1": signal.target_1,
            "target2": signal.target_2,
            "targetSma20": signal.target_sma20,
            "riskDollars": signal.risk_dollars,
            "reward1R": signal.reward_1r,
        },
        "quality": {
            "score": signal.score,
            "grade": signal.grade,
            "strength": signal.strength,
            "components": {
                "rsi": signal.rsi_score,
                "stochastics": signal.stochastics_score,
                "sma20Deviation": signal.sma20_deviation_score,
                "volume": signal.volume_score,
                "closePosition": signal.close_position_score,
                "srConfluence": signal.sr_confluence_score,
            },
        },
        "indicators": {
            "rsi": signal.rsi,
            "stochastics": signal.stochastics,
            "sma20": signal.sma20,
            "sma20DeviationPct": signal.sma20_deviation_pct,
            "volumeRatio": signal.volume_ratio,
            "atr14": signal.atr14,
        },
        "notes": signal.notes,
    }
