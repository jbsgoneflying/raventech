"""Engine 1 — Live Review v2 orchestrator.

Three desk-driven phase-specific check-ins for an OPEN earnings IC trade:

    pre_event   "entry → close on T-1: anything material changed since entry?"
    pre_open    "close T-1 → open T-0:  what does AH/PM, news, futures say?"
    post_open   "open T-0 → expiry:     gap is in. exit / hold / cut?"

Each phase pulls a phase-tuned evidence packet (current vs entry deltas,
news, regime, macro flags, historical analogues, AH/PM gap, AND a full
E15-style replay of the desk's strikes against the analogue pool) and
feeds it into a phase-aware LLM verdict.

Parallel I/O via threads; each layer has a per-layer timeout and degrades
to ``{available: false, error: ...}`` rather than blowing up the whole
review. Caching is keyed on
``(trade_id, phase, current_spot bucketed to $0.25)`` with a 5-minute TTL
so repeat clicks within a desk session are instant.

Public entry point: :func:`run_live_review`.
"""
from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutTimeoutError
from typing import Any, Dict, List, Optional, Tuple

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cache (5-min TTL keyed on trade_id + phase + spot bucket)
# ---------------------------------------------------------------------------

_CACHE_TTL_S = 300
_cache: Dict[Tuple[str, str, float], Tuple[float, Dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _spot_bucket(spot: float) -> float:
    """Bucket spot to nearest $0.25 so tiny tape ticks don't bust the cache."""
    if spot is None or spot <= 0:
        return 0.0
    return round(float(spot) * 4) / 4.0


def _cache_get(key: Tuple[str, str, float]) -> Optional[Dict[str, Any]]:
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if not hit:
            return None
        ts, payload = hit
        if now - ts > _CACHE_TTL_S:
            _cache.pop(key, None)
            return None
        return payload


def _cache_set(key: Tuple[str, str, float], payload: Dict[str, Any]) -> None:
    with _cache_lock:
        _cache[key] = (time.time(), payload)


# ---------------------------------------------------------------------------
# Phase detection
# ---------------------------------------------------------------------------

PHASE_PRE_EVENT = "pre_event"
PHASE_PRE_OPEN = "pre_open"
PHASE_POST_OPEN = "post_open"
VALID_PHASES = (PHASE_PRE_EVENT, PHASE_PRE_OPEN, PHASE_POST_OPEN)


def _now_et() -> dt.datetime:
    """Best-effort America/New_York 'wall clock' for phase math.

    Avoids the heavy zoneinfo import path on platforms where tzdata may be
    missing — falls back to UTC-5 (EST) which is good enough for the
    pre/post-open buckets we care about.
    """
    try:
        from zoneinfo import ZoneInfo  # type: ignore
        return dt.datetime.now(ZoneInfo("America/New_York"))
    except Exception:
        return dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=5)


def auto_detect_phase(*, earnings_date: str, earnings_timing: str, now: Optional[dt.datetime] = None) -> str:
    """Auto-pick the most likely phase given event date/timing + clock.

    Boundaries (all in ET):
      - earnings_date is the EVENT day (not entry day).
      - For BMO: earnings prints before 9:30 → 'pre_open' = T-day pre-9:30,
        'post_open' = T-day after 9:30, 'pre_event' = anything before today.
      - For AMC: earnings prints after 16:00 → 'pre_event' = before T-day's
        16:00, 'pre_open' = T-day 16:00 → T+1-day 9:30,
        'post_open' = T+1-day after 9:30.
      - UNK: treat as BMO (more conservative for the desk).
    """
    nowet = now or _now_et()
    today = nowet.date()
    timing = (earnings_timing or "").upper().strip() or "UNK"

    try:
        ed = dt.date.fromisoformat((earnings_date or "")[:10])
    except Exception:
        # No earnings date — desk can still pick a phase; default pre_event.
        return PHASE_PRE_EVENT

    open_975 = dt.time(9, 30)
    close_400 = dt.time(16, 0)

    if timing == "AMC":
        # Pre-event window ends at T-day 16:00.
        if today < ed:
            return PHASE_PRE_EVENT
        if today == ed and nowet.time() < close_400:
            return PHASE_PRE_EVENT
        # After T-day 16:00 → AH/PM gap available; pre_open until T+1 9:30.
        next_session = ed + dt.timedelta(days=1)
        # Skip weekends for the post-open boundary.
        while next_session.weekday() > 4:
            next_session += dt.timedelta(days=1)
        if today < next_session:
            return PHASE_PRE_OPEN
        if today == next_session and nowet.time() < open_975:
            return PHASE_PRE_OPEN
        return PHASE_POST_OPEN

    # BMO / UNK
    if today < ed:
        return PHASE_PRE_EVENT
    if today == ed and nowet.time() < open_975:
        return PHASE_PRE_OPEN
    return PHASE_POST_OPEN


def normalize_phase(phase: Any, *, fallback: str) -> str:
    if not phase:
        return fallback
    p = str(phase).strip().lower().replace("-", "_")
    if p in VALID_PHASES:
        return p
    # Tolerate "preEvent" / "PRE_OPEN" / etc.
    p2 = p.replace("preevent", "pre_event").replace("preopen", "pre_open").replace("postopen", "post_open")
    return p2 if p2 in VALID_PHASES else fallback


# ---------------------------------------------------------------------------
# Trade record extraction helpers
# ---------------------------------------------------------------------------

def _extract_trade_fields(trade: Dict[str, Any]) -> Dict[str, Any]:
    entry = trade.get("entry") or {}
    ctx = trade.get("entryContext") or {}
    snap = trade.get("marketSnapshot") or {}

    def _f(v: Any) -> float:
        try:
            return float(v) if v is not None and v != "" else 0.0
        except (TypeError, ValueError):
            return 0.0

    return {
        "ticker": str(trade.get("ticker") or "").upper(),
        "shortPut": _f(entry.get("shortPutStrike")),
        "longPut": _f(entry.get("longPutStrike")),
        "shortCall": _f(entry.get("shortCallStrike")),
        "longCall": _f(entry.get("longCallStrike")),
        "entryCredit": _f(entry.get("entryCredit")),
        "spotAtEntry": _f(entry.get("spotAtEntry")),
        "ivAtEntry": _f(entry.get("impliedVolEntry") or ctx.get("impliedVolEntry") or snap.get("iv30dMean")),
        "emPctAtEntry": _f(entry.get("impliedMovePct")),
        "emMultiple": _f(entry.get("emMultiple")),
        "wingWidth": _f(entry.get("wingWidth")),
        "earningsDate": str(entry.get("earningsDate") or "")[:10],
        "earningsTiming": str(entry.get("earningsTiming") or "UNK").upper(),
        "expiry": str(entry.get("expiry") or entry.get("expiryDate") or "")[:10],
        "entryDate": str(entry.get("entryDate") or (trade.get("loggedAt") or "")[:10]),
        "regimeAtEntry": (snap.get("regimeLabel") or ctx.get("regimeLabel") or ctx.get("regimeBucket") or ""),
        "vrpAtEntry": ctx.get("vrpScore"),
        "loggedAt": trade.get("loggedAt"),
    }


# ---------------------------------------------------------------------------
# Evidence layers
# ---------------------------------------------------------------------------

def _derive_status_chip(spot: float, short_put: float, short_call: float) -> Tuple[str, Optional[float], Optional[float], Optional[float]]:
    put_dist = None
    call_dist = None
    nearest = None
    if spot > 0 and short_put > 0:
        put_dist = round((spot - short_put) / spot * 100.0, 2)
    if spot > 0 and short_call > 0:
        call_dist = round((short_call - spot) / spot * 100.0, 2)
    dists = [d for d in (put_dist, call_dist) if d is not None]
    if dists:
        nearest = min(dists)
    chip = "unknown"
    if nearest is not None:
        if nearest < 0:
            chip = "breached"
        elif nearest < 0.5:
            chip = "short_strike_challenged"
        elif nearest < 1.5:
            chip = "caution"
        else:
            chip = "on_track"
    return chip, put_dist, call_dist, nearest


def _layer_spot_iv(ticker: str, fields: Dict[str, Any], override_spot: float, override_vix: Any) -> Dict[str, Any]:
    """Pull current spot + ticker IV in one round-trip when possible."""
    out: Dict[str, Any] = {"available": True}
    spot_val = float(override_spot or 0.0)
    iv_val: Optional[float] = None

    # Spot via session-aware resolver (ORATS + EODHD cross-check).
    if spot_val <= 0:
        try:
            from backend.deps import get_client_optional
            from backend.technicals import fetch_live_price_context_optional
            orats = get_client_optional()
            if orats and ticker:
                px = fetch_live_price_context_optional(client=orats, ticker=ticker)
                spot_val = float((px or {}).get("price") or 0.0)
        except Exception as e:
            out["spotError"] = f"{type(e).__name__}: {e}"

    # Ticker IV30 via ORATS live_summaries (+ falls back to entry snapshot's
    # vix proxy when spot resolved but IV didn't).
    try:
        from backend.deps import get_client_optional
        orats = get_client_optional()
        if orats and ticker:
            resp = orats.live_summaries(ticker=ticker)
            rows = resp.rows or []
            if rows:
                iv_val = rows[0].get("iv30dMean") or rows[0].get("ivMean")
    except Exception as e:
        out["ivError"] = f"{type(e).__name__}: {e}"

    if override_vix is not None:
        try:
            iv_val = float(override_vix)
        except (TypeError, ValueError):
            pass

    iv_at_entry = float(fields.get("ivAtEntry") or 0.0)
    crush_pct: Optional[float] = None
    if iv_at_entry > 0 and iv_val is not None and float(iv_val) > 0:
        crush_pct = round((iv_at_entry - float(iv_val)) / iv_at_entry * 100.0, 1)

    chip, put_dist, call_dist, nearest = _derive_status_chip(
        spot_val, float(fields.get("shortPut") or 0.0), float(fields.get("shortCall") or 0.0)
    )

    out.update({
        "spot": {
            "atEntry": fields.get("spotAtEntry"),
            "now": spot_val,
            "putDistPct": put_dist,
            "callDistPct": call_dist,
            "nearestShortPct": nearest,
        },
        "iv": {
            "atEntry": iv_at_entry or None,
            "now": (float(iv_val) if iv_val is not None else None),
            "crushSoFarPct": crush_pct,
        },
        "statusChip": chip,
    })
    return out


def _layer_regime(fields: Dict[str, Any]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": True, "atEntry": fields.get("regimeAtEntry") or None}
    try:
        from backend.market_intel import regime_snapshot
        snap = regime_snapshot()
        d = snap.to_dict() if hasattr(snap, "to_dict") else dict(snap)
        out["now"] = d.get("label") or d.get("regimeLabel")
        out["score"] = d.get("score")
        out["volState"] = d.get("vol_state") or d.get("volState")
        out["probabilities"] = d.get("probabilities")
        if out["atEntry"] and out["now"]:
            out["drift"] = "same" if str(out["atEntry"]).lower() == str(out["now"]).lower() else "shift"
    except Exception as e:
        out["available"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _layer_news(ticker: str, fields: Dict[str, Any], phase: str) -> Dict[str, Any]:
    """Pull last 24-48h headlines via Benzinga.

    Window:
      - pre_event:  since the trade was logged (typically 1-2d)
      - pre_open:   since T-1 16:00 ET (overnight)
      - post_open:  since T-0 9:30 ET (intraday catalysts)
    """
    out: Dict[str, Any] = {"available": True, "headlines": []}
    if not ticker:
        out["available"] = False
        out["error"] = "no ticker"
        return out
    try:
        from backend.benzinga_client import BenzingaClient
        bz = BenzingaClient.from_env_optional()
        if bz is None:
            out["available"] = False
            out["error"] = "BENZINGA_API_KEY not set"
            return out

        today = dt.date.today()
        if phase == PHASE_PRE_OPEN or phase == PHASE_POST_OPEN:
            date_from = (today - dt.timedelta(days=2)).isoformat()
        else:
            # pre_event: since the trade was logged (cap at 5 days back).
            try:
                logged = dt.date.fromisoformat((fields.get("loggedAt") or "")[:10])
                date_from = max(logged, today - dt.timedelta(days=5)).isoformat()
            except Exception:
                date_from = (today - dt.timedelta(days=2)).isoformat()
        date_to = today.isoformat()

        resp = bz.news(
            tickers=ticker,
            date_from=date_from,
            date_to=date_to,
            page_size=25,
            display_output="headline",
            sort="created:desc",
        )
        rows = list(resp.rows or [])

        keyword_high = (
            "downgrade", "downgraded", "guidance cut", "lowered", "miss", "misses",
            "lawsuit", "subpoena", "recall", "investigation", "fraud", "warning",
            "ceo step", "resign", "halts", "halted", "delay",
        )
        keyword_medium = (
            "upgrade", "raised", "beats", "beat estimates", "guidance raise", "buyback",
            "dividend", "preview", "estimates", "analyst",
        )

        def _classify(headline: str) -> str:
            h = (headline or "").lower()
            if any(kw in h for kw in keyword_high):
                return "high"
            if any(kw in h for kw in keyword_medium):
                return "medium"
            return "low"

        headlines: List[Dict[str, Any]] = []
        high_n = med_n = low_n = 0
        for r in rows[:25]:
            title = str(r.get("title") or r.get("headline") or "")
            url = str(r.get("url") or "")
            ts = str(r.get("created") or r.get("createdAt") or r.get("updated") or "")
            pri = _classify(title)
            if pri == "high":
                high_n += 1
            elif pri == "medium":
                med_n += 1
            else:
                low_n += 1
            headlines.append({"title": title, "url": url, "ts": ts, "priority": pri})
        # Sort high first, then medium, then low (preserve recency within bucket).
        headlines.sort(key=lambda h: ({"high": 0, "medium": 1, "low": 2}[h["priority"]], 0))
        out["headlines"] = headlines[:8]
        out["counts"] = {"high": high_n, "medium": med_n, "low": low_n, "total": len(rows)}
        out["window"] = {"from": date_from, "to": date_to}
    except Exception as e:
        out["available"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _layer_macro() -> Dict[str, Any]:
    out: Dict[str, Any] = {"available": True, "events": [], "flags": []}
    try:
        from backend.benzinga_client import BenzingaClient
        from backend.macro_events import macro_events_by_date
        bz = BenzingaClient.from_env_optional()
        if bz is None:
            out["available"] = False
            out["error"] = "BENZINGA_API_KEY not set"
            return out
        today = dt.date.today()
        events = macro_events_by_date(bz=bz, start=today, end=today)
        # macro_events_by_date returns a Dict[date, List[event]] OR similar.
        flat: List[Dict[str, Any]] = []
        if isinstance(events, dict):
            for d, lst in events.items():
                for ev in (lst or []):
                    flat.append({"date": str(d), **(ev or {})})
        elif isinstance(events, list):
            flat = list(events or [])
        big_kw = ("FOMC", "CPI", "PPI", "NFP", "Nonfarm", "Powell", "Fed Chair")
        flags: List[str] = []
        for ev in flat:
            name = str(ev.get("event") or ev.get("name") or "")
            for kw in big_kw:
                if kw.lower() in name.lower():
                    flags.append(kw)
                    break
        out["events"] = flat[:10]
        out["flags"] = sorted(set(flags))
    except Exception as e:
        out["available"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _layer_analogues(ticker: str, fields: Dict[str, Any]) -> Dict[str, Any]:
    """Historical breach-rate ladder for this ticker conditioned on the
    trade's earnings event. Uses ``compute_breach_stats``."""
    out: Dict[str, Any] = {"available": True}
    try:
        from backend.deps import get_client_optional
        from backend.earnings_logic import compute_breach_stats
        client = get_client_optional()
        if client is None:
            out["available"] = False
            out["error"] = "ORATS client unavailable"
            return out
        next_event_override = None
        if fields.get("earningsDate"):
            next_event_override = {
                "earnDate": fields["earningsDate"],
                "anncTod": fields.get("earningsTiming") or "UNK",
            }
        stats = compute_breach_stats(
            client,
            ticker=ticker,
            n=20, years=5,
            next_event_override=next_event_override,
        )
        em_breach = stats.get("emBreach") or {}
        summary = stats.get("summary") or {}
        # em_breach can be float-map ("1.0":x, "1.5":y, "2.0":z) or dict-of-dicts.
        def _rate(key: str) -> Optional[float]:
            v = em_breach.get(key)
            if v is None:
                return None
            if isinstance(v, dict):
                v = v.get("breachRatePct") or v.get("breachPct")
            try:
                return float(v)
            except (TypeError, ValueError):
                return None
        em_mult = float(fields.get("emMultiple") or 0.0)
        ladder = {
            "1.0x": _rate("1.0"),
            "1.5x": _rate("1.5"),
            "2.0x": _rate("2.0"),
        }
        # Closest rate at this trade's emMultiple.
        rate_at_em: Optional[float] = None
        if em_mult > 0:
            buckets = [(1.0, ladder["1.0x"]), (1.5, ladder["1.5x"]), (2.0, ladder["2.0x"])]
            buckets = [(k, v) for (k, v) in buckets if v is not None]
            if buckets:
                rate_at_em = min(buckets, key=lambda kv: abs(kv[0] - em_mult))[1]
        out["nEvents"] = summary.get("eventsUsed") or summary.get("events_used") or len(stats.get("events") or [])
        out["ladder"] = ladder
        out["rateAtEmPct"] = rate_at_em
        out["emMultiple"] = em_mult or None
        out["upBreachRatePct"] = summary.get("upBreachRatePct")
        out["downBreachRatePct"] = summary.get("downBreachRatePct")
        out["tailBias"] = summary.get("tailBias")
    except Exception as e:
        out["available"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


def _layer_replay(fields: Dict[str, Any], current_spot: float) -> Dict[str, Any]:
    """E15-style replay of the desk's strikes through the analogue pool."""
    out: Dict[str, Any] = {"available": True}
    try:
        from backend.deps import get_client_optional
        from backend.engine15.simulator import run_for_open_trade
        client = get_client_optional()
        if client is None:
            out["available"] = False
            out["error"] = "ORATS client unavailable"
            return out
        proj = run_for_open_trade(fields, current_spot=current_spot, client=client)
        out.update(proj)
    except Exception as e:
        LOG.exception("e1_live_review: replay failed")
        out["available"] = False
        out["error"] = f"{type(e).__name__}: {e}"
    return out


# ---------------------------------------------------------------------------
# Action ladder + rule-based pre-verdict
# ---------------------------------------------------------------------------

def _score_action_ladder(*, fields: Dict[str, Any], evidence: Dict[str, Any], phase: str) -> Tuple[List[Dict[str, Any]], str, float]:
    """Build the desk's HOLD/CUT/ADJUST ladder + a rule-based pre-verdict.

    Returns (ladder, preVerdict, preConfidence). The LLM gets the
    pre-verdict as a hint and is asked to confirm or override.
    """
    spot_layer = (evidence.get("spot") or {})
    nearest = spot_layer.get("nearestShortPct")
    chip = evidence.get("statusChip") or "unknown"

    replay = (evidence.get("replay") or {})
    p10 = replay.get("p10PnlPct")
    p50 = replay.get("p50PnlPct")
    p90 = replay.get("p90PnlPct")
    full_collect_rate = replay.get("fullCollectRate")

    analogues = (evidence.get("analogues") or {})
    rate_at_em = analogues.get("rateAtEmPct")

    news = (evidence.get("news") or {})
    high_news = ((news.get("counts") or {}).get("high") or 0)

    # --- Rule-based pre-verdict ---
    pre = "HOLD"
    conf = 0.6
    if chip == "breached":
        pre, conf = "CUT", 0.85
    elif p10 is not None and p10 <= -150.0:
        pre, conf = "CUT", 0.78
    elif chip == "short_strike_challenged" and (rate_at_em or 0) > 25.0:
        pre, conf = "ADJUST", 0.7
    elif chip == "short_strike_challenged":
        pre, conf = "ADJUST", 0.62
    elif chip == "on_track" and (full_collect_rate is not None and full_collect_rate >= 0.6) and (p50 is not None and p50 >= 25.0):
        pre, conf = "HOLD", 0.78
    elif chip == "caution" and (rate_at_em or 0) > 35.0:
        pre, conf = "ADJUST", 0.6
    if high_news >= 2 and pre == "HOLD":
        # Multiple high-priority headlines → bump caution one notch.
        pre, conf = "ADJUST", max(conf, 0.6)

    # --- Action ladder (3 actions, phase-tuned third action) ---
    third_label, third_action = "ROLL_PUT", "Roll the challenged short out/down"
    if phase == PHASE_POST_OPEN:
        third_label, third_action = "TRIM", "Trim partial size for guaranteed credit"
    elif (nearest is not None and spot_layer.get("callDistPct") is not None and
          spot_layer.get("putDistPct") is not None and
          spot_layer.get("callDistPct") < spot_layer.get("putDistPct")):
        third_label, third_action = "ROLL_CALL", "Roll the challenged short call out/up"

    def _pct(v: Optional[float], fallback: float) -> float:
        try:
            return round(float(v), 1) if v is not None else fallback
        except Exception:
            return fallback

    ladder = [
        {"action": "HOLD", "label": "Hold through expiry",
         "expectedPnlPct": _pct(p50, 0.0),
         "p10PnlPct": _pct(p10, 0.0),
         "p90PnlPct": _pct(p90, 0.0),
         "probWin": (full_collect_rate if full_collect_rate is not None else None)},
        {"action": "CUT_NOW", "label": "Cut now (close at mid)",
         "expectedPnlPct": _pct(p50, 0.0) if p50 is not None and p50 < 50 else 50.0,
         "probWin": 1.0,
         "rationale": "Lock partial credit; eliminate gap risk."},
        {"action": third_label, "label": third_action,
         "expectedPnlPct": None, "probWin": None,
         "rationale": "Trade defended structure for residual credit."},
    ]
    return ladder, pre, conf


# ---------------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------------

def run_live_review(
    *,
    trade: Dict[str, Any],
    phase_request: Optional[str] = None,
    current_spot_override: float = 0.0,
    current_vix_override: Any = None,
    notes: Optional[str] = None,
    force_refresh: bool = False,
) -> Dict[str, Any]:
    """Assemble a full v2 Live Review payload for the given open trade.

    Returns a dict shaped for both the v1 contract (top-level
    ``statusChip``, ``currentSpot``, ``currentVix``, ``daysToEarnings``,
    ``llmAssessment``) AND the v2 schema (``phase``, ``evidence``,
    ``recommendation``).
    """
    fields = _extract_trade_fields(trade)
    ticker = fields["ticker"]
    trade_id = str(trade.get("tradeId") or "")

    auto_phase = auto_detect_phase(
        earnings_date=fields["earningsDate"],
        earnings_timing=fields["earningsTiming"],
    )
    phase = normalize_phase(phase_request, fallback=auto_phase)

    # --- Spot first (cheap; everything else may key off it) ---
    spot_iv = _layer_spot_iv(
        ticker=ticker,
        fields=fields,
        override_spot=float(current_spot_override or 0.0),
        override_vix=current_vix_override,
    )
    current_spot = float((spot_iv.get("spot") or {}).get("now") or 0.0)
    current_vix = (spot_iv.get("iv") or {}).get("now")

    # --- Cache lookup keyed on trade + phase + bucketed spot ---
    cache_key = (trade_id, phase, _spot_bucket(current_spot))
    if not force_refresh:
        hit = _cache_get(cache_key)
        if hit is not None:
            hit2 = dict(hit)
            hit2["cached"] = True
            return hit2

    # --- Parallel evidence assembly ---
    layers: Dict[str, Any] = {}
    layer_specs: List[Tuple[str, Any, float]] = [
        ("regime", lambda: _layer_regime(fields), 8.0),
        ("news", lambda: _layer_news(ticker, fields, phase), 8.0),
        ("macro", lambda: _layer_macro(), 6.0),
        ("analogues", lambda: _layer_analogues(ticker, fields), 30.0),
        # Replay is the heavy one — give it a generous timeout per phase budget.
        ("replay", lambda: _layer_replay(fields, current_spot), 75.0),
    ]
    with ThreadPoolExecutor(max_workers=5) as ex:
        futs = {name: ex.submit(fn) for (name, fn, _to) in layer_specs}
        for (name, _fn, to) in layer_specs:
            try:
                layers[name] = futs[name].result(timeout=to)
            except FutTimeoutError:
                layers[name] = {"available": False, "error": f"timeout > {to:.0f}s"}
            except Exception as e:
                layers[name] = {"available": False, "error": f"{type(e).__name__}: {e}"}

    evidence: Dict[str, Any] = {
        "spot": spot_iv.get("spot"),
        "iv": spot_iv.get("iv"),
        "statusChip": spot_iv.get("statusChip"),
        "regime": layers.get("regime"),
        "news": layers.get("news"),
        "macro": layers.get("macro"),
        "analogues": layers.get("analogues"),
        "replay": layers.get("replay"),
    }

    # --- Days-to-earnings ---
    days_to_earnings: Optional[int] = None
    if fields.get("earningsDate"):
        try:
            ed = dt.date.fromisoformat(fields["earningsDate"])
            days_to_earnings = (ed - dt.date.today()).days
        except Exception:
            days_to_earnings = None

    # --- Action ladder + pre-verdict ---
    ladder, pre_verdict, pre_conf = _score_action_ladder(
        fields=fields, evidence=evidence, phase=phase,
    )

    # --- LLM phase-tuned narrative ---
    llm_assessment = None
    try:
        from backend.e1_earnings_advisor import generate_live_review_v2
        llm_assessment = generate_live_review_v2(
            phase=phase,
            ticker=ticker,
            fields=fields,
            evidence=evidence,
            days_to_earnings=days_to_earnings,
            pre_verdict=pre_verdict,
            pre_confidence=pre_conf,
        )
    except Exception as e:
        LOG.warning("e1_live_review: LLM v2 advisor failed: %s", e)
        llm_assessment = None

    verdict = (llm_assessment or {}).get("verdict") or pre_verdict
    confidence = (llm_assessment or {}).get("confidence")
    if confidence is None:
        confidence = pre_conf

    recommendation = {
        "verdict": str(verdict).upper(),
        "confidence": float(confidence) if confidence is not None else None,
        "narrative": (llm_assessment or {}).get("narrative"),
        "keyPoints": (llm_assessment or {}).get("keyPoints") or [],
        "riskFactors": (llm_assessment or {}).get("riskFactors") or [],
        "deskNote": (llm_assessment or {}).get("deskNote"),
        "preVerdict": pre_verdict,
        "preConfidence": pre_conf,
        "actionLadder": ladder,
    }

    review = {
        # v1 fields preserved for back-compat
        "statusChip": evidence.get("statusChip") or "unknown",
        "currentSpot": current_spot,
        "currentVix": current_vix,
        "daysToEarnings": days_to_earnings,
        "putDistPct": (evidence.get("spot") or {}).get("putDistPct"),
        "callDistPct": (evidence.get("spot") or {}).get("callDistPct"),
        "nearestShortPct": (evidence.get("spot") or {}).get("nearestShortPct"),
        "llmAssessment": llm_assessment,
        "userNotes": notes,
        # v2 fields
        "phase": phase,
        "phaseAuto": auto_phase,
        "phaseMismatch": (phase != auto_phase),
        "evidence": evidence,
        "recommendation": recommendation,
        "version": "v2",
    }

    payload = {"tradeId": trade_id, "review": review}
    _cache_set(cache_key, payload)
    return payload
