"""Engine 8 – Pipeline Runner (Orchestrator).

Ties all Engine 8 modules together:

  1. Activation gate  →  system-derived trade outcome
  2. Post-event snapshot  →  parallel data fetch + LLM persistence
  3. Displacement classification  →  deterministic fields only
  4. Historical pattern layer  →  force PASS when sample < 15
  5. Regime overlay  →  explicit numeric score (0-100)
  6. Decision framework  →  CONTINUE / FADE / PASS

Caching:
  In-memory TTLCache keyed on ``(ticker, earnings_date, cache_key_engine8)``.

Follows the Engine 5 pipeline pattern (``engine5_pipeline.py``).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional

from cachetools import TTLCache

from backend.config import FeatureFlags, get_flags
from backend.engine8_activation import ActivationResult, check_activation
from backend.engine8_classifier import DisplacementProfile, classify_displacement
from backend.engine8_decision import ExtensionDecision, make_decision
from backend.engine8_historical import (
    HistoricalPatternResult,
    _build_event_row,
    _magnitude_bucket,
    _structure_bucket,
    compute_historical_patterns,
)
from backend.engine8_snapshot import (
    PostEventSnapshot, _to_float, _fmt_date, _compute_atr,
    build_post_event_snapshot, _resolve_pre_post_dates,
)

LOG = logging.getLogger(__name__)

_eval_cache: TTLCache = TTLCache(maxsize=256, ttl=30 * 60)
_eval_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _previous_trading_day(ref: dt.date) -> dt.date:
    d = ref - dt.timedelta(days=1)
    while d.weekday() >= 5:
        d -= dt.timedelta(days=1)
    return d


def _next_trading_day(ref: dt.date) -> dt.date:
    d = ref + dt.timedelta(days=1)
    while d.weekday() >= 5:
        d += dt.timedelta(days=1)
    return d


def _parse_date(s: str) -> dt.date:
    return dt.date.fromisoformat(str(s)[:10])


# ---------------------------------------------------------------------------
# Build historical event rows from ORATS + EODHD
# ---------------------------------------------------------------------------

def _build_all_event_rows(
    *,
    ticker: str,
    current_earnings_date: dt.date,
    orats_client: Any,
    price_svc: Any,
    flags: FeatureFlags,
) -> List[dict]:
    """Build historical event rows for the pattern matcher.

    Fetches earnings history from ORATS and daily bars from EODHD,
    then computes snapshot-equivalent fields for each past event.
    """
    try:
        resp = orats_client.hist_earnings(ticker)
        earnings_rows = resp.rows or []
    except Exception as e:
        LOG.warning("Engine 8 hist_earnings failed for %s: %s", ticker, e)
        return []

    earnings_rows = [
        r for r in earnings_rows
        if r.get("earnDate") and _parse_date(str(r["earnDate"])) < current_earnings_date
    ]
    earnings_rows.sort(key=lambda r: str(r.get("earnDate", "")), reverse=True)
    earnings_rows = earnings_rows[:flags.ENGINE8_LOOKBACK_EVENTS]

    if not earnings_rows:
        return []

    oldest_date = _parse_date(str(earnings_rows[-1]["earnDate"])) - dt.timedelta(days=40)
    newest_date = _parse_date(str(earnings_rows[0]["earnDate"])) + dt.timedelta(days=10)

    all_bars: List[dict] = []
    if price_svc is not None:
        try:
            bar_objs = price_svc.fetch_daily_bars(ticker, oldest_date, newest_date)
            all_bars = [
                {"date": _fmt_date(b.date), "open": b.open, "high": b.high,
                 "low": b.low, "close": b.close, "volume": b.volume}
                for b in bar_objs
            ]
        except Exception as e:
            LOG.warning("Engine 8 bar fetch failed for %s: %s", ticker, e)

    if not all_bars:
        return []

    bars_by_date = {str(b.get("date", ""))[:10]: b for b in all_bars}
    sorted_dates = sorted(bars_by_date.keys())

    event_rows: List[dict] = []

    from backend.earnings_logic import classify_timing

    for erow in earnings_rows:
        earn_date = _parse_date(str(erow["earnDate"]))

        annc_tod = erow.get("anncTod") or erow.get("annc_tod") or erow.get("anncTOD")
        timing = classify_timing(annc_tod)
        pre_d, post_d = _resolve_pre_post_dates(earn_date, timing)

        pre_bar_key = _fmt_date(pre_d)
        post_bar_key = _fmt_date(post_d)

        pre_bar = bars_by_date.get(pre_bar_key)
        post_bar = bars_by_date.get(post_bar_key)
        if pre_bar is None or post_bar is None:
            continue

        pre_close = _to_float(pre_bar.get("close") or pre_bar.get("adjusted_close"))
        if pre_close is None or pre_close <= 0:
            continue

        em = _to_float(erow.get("impErnMv"))
        expected_move_pct: Optional[float] = None
        if em is not None:
            expected_move_pct = abs(em) * 100.0 if abs(em) <= 1.0 else abs(em)

        lookback_start = _fmt_date(earn_date - dt.timedelta(days=25))
        atr_bars = [b for b in all_bars if lookback_start <= str(b.get("date", ""))[:10] <= pre_bar_key]

        post_idx = sorted_dates.index(post_bar_key) if post_bar_key in sorted_dates else -1
        if post_idx < 0:
            continue
        forward_bars = [
            bars_by_date[sorted_dates[i]]
            for i in range(post_idx + 1, min(post_idx + 6, len(sorted_dates)))
        ]

        row = _build_event_row(
            earnings_date=earn_date,
            pre_close=pre_close,
            post_bar=post_bar,
            expected_move_pct=expected_move_pct,
            bars_for_atr=atr_bars,
            forward_bars=forward_bars,
            flags=flags,
        )
        if row is not None:
            event_rows.append(row)

    return event_rows


# ---------------------------------------------------------------------------
# Fetch SPY context for regime + classification
# ---------------------------------------------------------------------------

def _fetch_spy_returns(price_svc: Any, today: dt.date) -> Dict[str, Optional[float]]:
    """Fetch SPY 5-day and 20-day returns for context classification."""
    result: Dict[str, Optional[float]] = {"spy_5d_return": None, "spy_20d_return": None}
    if price_svc is None:
        return result
    try:
        start = today - dt.timedelta(days=40)
        bars = price_svc.fetch_daily_bars("SPY", start, today)
        if not bars:
            return result
        bars.sort(key=lambda b: b.date)
        closes = [b.close for b in bars if b.close is not None]
        if len(closes) >= 6:
            result["spy_5d_return"] = ((closes[-1] - closes[-6]) / closes[-6]) * 100.0
        if len(closes) >= 21:
            result["spy_20d_return"] = ((closes[-1] - closes[-21]) / closes[-21]) * 100.0
    except Exception as e:
        LOG.debug("Engine 8 SPY fetch: %s", e)
    return result


def _fetch_ticker_trend_returns(
    price_svc: Any,
    ticker: str,
    today: dt.date,
) -> Dict[str, Optional[float]]:
    """Fetch ticker 5-day and 20-day returns for context classification."""
    result: Dict[str, Optional[float]] = {"trend_5d_return": None, "trend_20d_return": None}
    if price_svc is None:
        return result
    try:
        start = today - dt.timedelta(days=40)
        bars = price_svc.fetch_daily_bars(ticker, start, today)
        if not bars:
            return result
        bars.sort(key=lambda b: b.date)
        closes = [b.close for b in bars if b.close is not None]
        if len(closes) >= 6:
            result["trend_5d_return"] = ((closes[-1] - closes[-6]) / closes[-6]) * 100.0
        if len(closes) >= 21:
            result["trend_20d_return"] = ((closes[-1] - closes[-21]) / closes[-21]) * 100.0
    except Exception as e:
        LOG.debug("Engine 8 ticker trend fetch for %s: %s", ticker, e)
    return result


# ---------------------------------------------------------------------------
# Single-ticker evaluation
# ---------------------------------------------------------------------------

def evaluate_ticker(
    *,
    ticker: str,
    engine1_trade: Optional[dict],
    earnings_date: Optional[dt.date] = None,
    earnings_timing: str = "UNK",
    orats_client: Any = None,
    price_svc: Any = None,
    store: Any = None,
    flags: Optional[FeatureFlags] = None,
    today: Optional[dt.date] = None,
) -> Dict[str, Any]:
    """Run the full Engine 8 pipeline for a single ticker.

    Returns a dict containing activation, snapshot, profile, historical,
    and decision results.
    """
    if flags is None:
        flags = get_flags()
    if today is None:
        today = dt.date.today()

    cache_key = (ticker.upper(), str(earnings_date), earnings_timing, flags.cache_key_engine8())
    with _eval_lock:
        cached = _eval_cache.get(cache_key)
    if cached is not None:
        return cached

    # -- Fetch current price for activation -----------------------------------
    current_price: Optional[float] = None
    if price_svc is not None:
        try:
            bars = price_svc.fetch_daily_bars(ticker, today - dt.timedelta(days=5), today)
            if bars:
                bars.sort(key=lambda b: b.date, reverse=True)
                current_price = bars[0].close
        except Exception:
            pass

    # -- Check if post-event bar exists (timing-aware) ------------------------
    has_post_event_bar = False
    if earnings_date is not None and price_svc is not None:
        _, post_d = _resolve_pre_post_dates(earnings_date, earnings_timing)
        try:
            bars = price_svc.fetch_daily_bars(ticker, post_d, post_d + dt.timedelta(days=3))
            has_post_event_bar = len(bars) > 0
        except Exception:
            pass

    # -- Activation gate ------------------------------------------------------
    activation = check_activation(
        ticker=ticker,
        engine1_trade=engine1_trade,
        earnings_date=earnings_date,
        current_price=current_price,
        has_post_event_bar=has_post_event_bar,
        max_controlled_loss_pct=flags.ENGINE8_MAX_CONTROLLED_LOSS_PCT,
        today=today,
    )

    result: Dict[str, Any] = {
        "ticker": ticker,
        "earnings_date": _fmt_date(earnings_date) if earnings_date else None,
        "timing": earnings_timing,
        "activation": activation.to_dict(),
        "snapshot": None,
        "profile": None,
        "historical": None,
        "decision": None,
    }

    if not activation.activated:
        result["decision"] = ExtensionDecision(
            ticker=ticker,
            decision="PASS",
            pass_reason="activation_failed",
            derived_trade_outcome=activation.derived_trade_outcome,
        ).to_dict()
        with _eval_lock:
            _eval_cache[cache_key] = result
        return result

    # -- Build post-event snapshot (timing-aware) ------------------------------
    eodhd_client = getattr(price_svc, "_eodhd", None) if price_svc else None
    snapshot = build_post_event_snapshot(
        ticker=ticker,
        earnings_date=earnings_date,
        orats_client=orats_client,
        eodhd_client=eodhd_client,
        timing=earnings_timing,
        store=store,
        flags=flags,
    )
    result["snapshot"] = snapshot.to_dict()

    # -- Fetch context data ---------------------------------------------------
    spy_ctx = _fetch_spy_returns(price_svc, today)
    ticker_ctx = _fetch_ticker_trend_returns(price_svc, ticker, today)

    # -- Classify displacement ------------------------------------------------
    profile = classify_displacement(
        move_vs_em=snapshot.move_vs_em,
        atr_multiple=snapshot.atr_multiple,
        gap_structure=snapshot.gap_structure,
        direction=snapshot.direction,
        trend_5d_return=ticker_ctx.get("trend_5d_return"),
        trend_20d_return=ticker_ctx.get("trend_20d_return"),
        spy_5d_return=spy_ctx.get("spy_5d_return"),
        flags=flags,
    )
    result["profile"] = profile.to_dict()

    # -- Historical patterns --------------------------------------------------
    all_event_rows = _build_all_event_rows(
        ticker=ticker,
        current_earnings_date=earnings_date,
        orats_client=orats_client,
        price_svc=price_svc,
        flags=flags,
    )

    historical = compute_historical_patterns(
        ticker=ticker,
        current_magnitude_bucket=profile.magnitude_em_label,
        current_structure_bucket=profile.structure_label,
        current_direction=profile.direction or "UP",
        all_event_rows=all_event_rows,
        flags=flags,
    )
    result["historical"] = historical.to_dict()

    # -- Regime overlay -------------------------------------------------------
    regime_overlay: Dict[str, Any] = {"label": "Normal", "tradeGate": "OK", "guidance": {"tradeGate": "OK"}}
    if orats_client is not None:
        try:
            from backend.regime_overlay import compute_regime_overlay
            regime_overlay = compute_regime_overlay(
                orats_client,
                ticker,
                quarters={},
                n=20,
                years=5,
                k=1.0,
                today=today,
            )
        except Exception as e:
            LOG.warning("Engine 8 regime overlay failed for %s: %s", ticker, e)

    # -- Decision framework ---------------------------------------------------
    decision = make_decision(
        ticker=ticker,
        snapshot=snapshot,
        profile=profile,
        historical=historical,
        regime_overlay=regime_overlay,
        spy_5d_return=spy_ctx.get("spy_5d_return"),
        derived_trade_outcome=activation.derived_trade_outcome,
        flags=flags,
    )
    result["decision"] = decision.to_dict()

    with _eval_lock:
        _eval_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# Batch evaluation
# ---------------------------------------------------------------------------

def evaluate_batch(
    *,
    tickers_and_trades: List[Dict[str, Any]],
    orats_client: Any = None,
    price_svc: Any = None,
    store: Any = None,
    flags: Optional[FeatureFlags] = None,
    today: Optional[dt.date] = None,
) -> List[Dict[str, Any]]:
    """Evaluate multiple tickers in parallel.

    ``tickers_and_trades`` is a list of dicts, each with at least:
      - ``ticker``: str
      - ``engine1_trade``: dict (Engine 1 output)
      - ``earnings_date``: str (optional, YYYY-MM-DD)
    """
    if flags is None:
        flags = get_flags()
    if today is None:
        today = dt.date.today()

    results: List[Dict[str, Any]] = []

    def _eval_one(item: dict) -> Dict[str, Any]:
        ed_str = item.get("earnings_date")
        ed = _parse_date(ed_str) if ed_str else None
        return evaluate_ticker(
            ticker=str(item["ticker"]),
            engine1_trade=item.get("engine1_trade"),
            earnings_date=ed,
            orats_client=orats_client,
            price_svc=price_svc,
            store=store,
            flags=flags,
            today=today,
        )

    max_workers = min(flags.ENGINE8_MAX_WORKERS, len(tickers_and_trades) or 1)
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(_eval_one, item): item for item in tickers_and_trades}
        for f in as_completed(futures):
            try:
                results.append(f.result())
            except Exception as e:
                item = futures[f]
                LOG.warning("Engine 8 eval failed for %s: %s", item.get("ticker"), e)
                results.append({
                    "ticker": item.get("ticker"),
                    "error": str(e),
                    "decision": ExtensionDecision(
                        ticker=str(item.get("ticker", "")),
                        decision="PASS",
                        pass_reason="pipeline_error",
                    ).to_dict(),
                })

    return results
