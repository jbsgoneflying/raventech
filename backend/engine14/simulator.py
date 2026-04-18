"""Engine 14 — IC scenario simulator.

Given a user's prospective short iron condor, this module:

  1) Enumerates historical analogue weekly windows (regime-matched).
  2) Maps the user's four strikes into each analogue's strike space
     preserving EM-distance.
  3) Replays the position day-by-day using cached ORATS chains,
     producing a MTM time series per analogue.
  4) Classifies each analogue outcome and aggregates a distribution
     + MTM percentile timeline + optimal exit rule.

The payload shape matches the plan-doc:
  analoguesUsed, outcomeDistribution, mtmTimeline, expectedValue,
  exitRulesOptimization, conditioningNotes, matchedAnalogues.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.config import FeatureFlags, get_flags
from backend.engine14 import chain_cache
from backend.engine14.analogue_matcher import (
    AnalogueWindow,
    MatchCriteria,
    REGIME_BUCKETS,
    build_analogue_universe,
    filter_analogues,
    map_user_strikes_to_analogue,
)
from backend.engine14.chain_replay import expiry_payoff, reprice_ic
from backend.engine14.conditioning import apply_modifiers_to_distribution, compute_conditioning
from backend.engine14.exit_rules import optimize_exit_rules
from backend.spx_ic.ohlc import fetch_dailies_ohlc_range, iv_to_em1sigma_pct

LOG = logging.getLogger("engine14.simulator")

OUTCOMES = ("earlyTarget", "fullCollect", "whiteKnuckle", "stopOut", "breach")


# ---- Request / response schemas ----

@dataclass(frozen=True)
class IcScenarioRequest:
    underlying: str
    entry_date: str
    expiry: str
    short_put: float
    long_put: float
    short_call: float
    long_call: float
    credit_received: float
    profit_target_pct: float = 50.0
    stop_loss_pct: float = 200.0
    season_mode: str = "none"            # none|quarter|month|summer|opex
    season_value: Optional[str] = None

    def dte_calendar(self) -> int:
        try:
            a = dt.date.fromisoformat(self.entry_date)
            b = dt.date.fromisoformat(self.expiry)
            return max(1, (b - a).days)
        except Exception:
            return 7

    def strike_tuple(self) -> Tuple[float, float, float, float]:
        return (float(self.short_put), float(self.long_put),
                float(self.short_call), float(self.long_call))

    def wing_width(self) -> float:
        put_w = abs(float(self.short_put) - float(self.long_put))
        call_w = abs(float(self.long_call) - float(self.short_call))
        return float(min(put_w, call_w)) if (put_w > 0 and call_w > 0) else float(max(put_w, call_w))


# ---- Per-analogue simulation ----

@dataclass
class AnaloguePath:
    entry_date: str
    expiry_date: str
    dte_sessions: int
    mapped_strikes: Tuple[float, float, float, float]
    daily_pnl_pct: List[Tuple[int, float]]  # (dte_remaining, pnl_pct_of_credit) from entry→expiry
    outcome: str
    exit_day: int
    exit_pnl_pct: float
    max_adverse_excursion_pct: float
    breached: bool
    notes: List[str] = field(default_factory=list)


def _daily_chain_for(ticker: str, trade_date: str, expiry: str):
    return chain_cache.fetch_chain_slice(ticker=ticker, trade_date=trade_date, expiry=expiry)


def _simulate_single_analogue(
    *,
    ticker: str,
    window: AnalogueWindow,
    user_strikes: Tuple[float, float, float, float],
    user_spot: float,
    user_em_pct: float,
    entry_credit: float,
    profit_target_pct: float,
    stop_loss_pct: float,
    closes_by_date: Dict[str, float],
    snap_max_pts: float,
) -> Optional[AnaloguePath]:
    try:
        mapped = map_user_strikes_to_analogue(
            user_spot=float(user_spot),
            user_em_pct=float(user_em_pct),
            analogue_spot=float(window.entry_close),
            analogue_em_pct=float(window.entry_em_pct),
            user_strikes=user_strikes,
        )
    except Exception as e:
        LOG.debug("strike mapping failed for %s: %s", window.entry_date, e)
        return None

    sp_k, lp_k, sc_k, lc_k = mapped
    # Sanity: puts should be below spot, calls above.
    if not (lp_k <= sp_k < window.entry_close < sc_k <= lc_k):
        # Don't reject outright — user may pass unusual strikes — but note it.
        pass

    # Enumerate trade-dates in [entry, expiry].
    try:
        e_date = dt.date.fromisoformat(window.entry_date)
        x_date = dt.date.fromisoformat(window.expiry_date)
    except Exception:
        return None

    trade_days: List[str] = []
    d = e_date
    while d <= x_date:
        ds = d.isoformat()
        if ds in closes_by_date:
            trade_days.append(ds)
        d += dt.timedelta(days=1)
    if len(trade_days) < 2:
        return None

    daily: List[Tuple[int, float]] = []
    mae = 0.0
    exit_day: Optional[int] = None
    exit_pnl: Optional[float] = None
    outcome: Optional[str] = None
    notes: List[str] = []

    for i, td in enumerate(trade_days):
        dte_remaining = len(trade_days) - 1 - i
        chain = _daily_chain_for(ticker, td, window.expiry_date)
        pnl_pct: Optional[float] = None

        if chain:
            priced = reprice_ic(
                chain=chain,
                short_put_strike=sp_k,
                long_put_strike=lp_k,
                short_call_strike=sc_k,
                long_call_strike=lc_k,
                entry_credit=float(entry_credit),
                snap_max_pts=float(snap_max_pts),
            )
            if priced is not None:
                pnl_pct = float(priced.pnl_pct_of_credit)

        # Fallback: terminal-day intrinsic payoff when chain missing at T=0.
        if pnl_pct is None and i == len(trade_days) - 1:
            spot = closes_by_date.get(td)
            if spot is not None:
                pnl_val = expiry_payoff(
                    expiry_spot=float(spot),
                    short_put_strike=sp_k,
                    long_put_strike=lp_k,
                    short_call_strike=sc_k,
                    long_call_strike=lc_k,
                    entry_credit=float(entry_credit),
                )
                pnl_pct = 100.0 * pnl_val / float(entry_credit)
                notes.append("expiry pnl computed from intrinsic payoff (chain missing)")

        if pnl_pct is None:
            # Skip days we can't price — don't abort the whole path.
            continue

        daily.append((int(dte_remaining), float(pnl_pct)))
        if pnl_pct < mae:
            mae = float(pnl_pct)

        if exit_day is None:
            # Check exit rules on EOD mark.
            if pnl_pct >= float(profit_target_pct):
                exit_day = i
                exit_pnl = pnl_pct
                outcome = "earlyTarget"
            elif pnl_pct <= -float(stop_loss_pct):
                exit_day = i
                exit_pnl = pnl_pct
                outcome = "stopOut"

    if not daily:
        return None

    # If never exited, close at expiry-day mark.
    if exit_day is None:
        final = daily[-1][1]
        exit_day = len(daily) - 1
        exit_pnl = float(final)
        if final >= 95.0:
            outcome = "fullCollect"
        elif final < -float(stop_loss_pct):
            # Gapped past stop on final day
            outcome = "stopOut"
        elif final < 0.0:
            outcome = "whiteKnuckle"
        else:
            outcome = "fullCollect"  # >0 but <95% -> still "kept" the trade, call it fullCollect-ish
            notes.append("partial credit kept (final pnl < 95% but positive)")

    # Detect breach (expiry close beyond short strike).
    breached = False
    last_td = trade_days[-1]
    last_close = closes_by_date.get(last_td)
    if last_close is not None:
        if last_close < sp_k or last_close > sc_k:
            breached = True

    # Escalate outcome to "breach" if expiry breached AND realized pnl was strongly negative.
    if breached and (exit_pnl is not None and exit_pnl <= -50.0):
        outcome = "breach"

    return AnaloguePath(
        entry_date=window.entry_date,
        expiry_date=window.expiry_date,
        dte_sessions=int(window.dte_sessions),
        mapped_strikes=mapped,
        daily_pnl_pct=daily,
        outcome=str(outcome),
        exit_day=int(exit_day),
        exit_pnl_pct=float(exit_pnl if exit_pnl is not None else 0.0),
        max_adverse_excursion_pct=float(mae),
        breached=bool(breached),
        notes=notes,
    )


# ---- Aggregation ----

def _percentiles(values: List[float], ps: List[float]) -> List[float]:
    if not values:
        return [0.0 for _ in ps]
    s = sorted(values)
    out: List[float] = []
    n = len(s)
    for p in ps:
        if n == 1:
            out.append(s[0])
            continue
        k = (n - 1) * float(p)
        lo = int(math.floor(k))
        hi = int(math.ceil(k))
        if lo == hi:
            out.append(s[lo])
        else:
            frac = k - lo
            out.append(s[lo] * (1.0 - frac) + s[hi] * frac)
    return out


def _build_mtm_timeline(paths: List[AnaloguePath]) -> List[Dict[str, Any]]:
    by_dte: Dict[int, List[float]] = {}
    breach_hits: Dict[int, int] = {}
    stop_hits: Dict[int, int] = {}
    for p in paths:
        seen_stop = False
        for dte_r, pnl in p.daily_pnl_pct:
            by_dte.setdefault(int(dte_r), []).append(float(pnl))
            if pnl <= -200.0 and not seen_stop:
                stop_hits[int(dte_r)] = stop_hits.get(int(dte_r), 0) + 1
                seen_stop = True
        if p.breached:
            final_dte = p.daily_pnl_pct[-1][0] if p.daily_pnl_pct else 0
            breach_hits[int(final_dte)] = breach_hits.get(int(final_dte), 0) + 1

    rows: List[Dict[str, Any]] = []
    n = max(1, len(paths))
    for dte_r in sorted(by_dte.keys(), reverse=True):  # entry (high dte) → expiry (0)
        vals = by_dte[dte_r]
        p10, p50, p90 = _percentiles(vals, [0.1, 0.5, 0.9])
        rows.append({
            "dte": int(dte_r),
            "p10": round(float(p10), 1),
            "p50": round(float(p50), 1),
            "p90": round(float(p90), 1),
            "n": len(vals),
            "pBreach": round(breach_hits.get(int(dte_r), 0) / n, 3),
            "pStopHit": round(stop_hits.get(int(dte_r), 0) / n, 3),
        })
    return rows


def _summarize_outcomes(paths: List[AnaloguePath]) -> Dict[str, Any]:
    total = max(1, len(paths))
    out: Dict[str, Dict[str, Any]] = {o: {"n": 0, "pnl": [], "days": [], "mae": []} for o in OUTCOMES}
    for p in paths:
        bkt = out.get(p.outcome) or out["whiteKnuckle"]
        bkt["n"] += 1
        bkt["pnl"].append(float(p.exit_pnl_pct))
        bkt["days"].append(int(p.exit_day))
        bkt["mae"].append(float(p.max_adverse_excursion_pct))
    summary: Dict[str, Any] = {}
    for k, v in out.items():
        n = int(v["n"])
        summary[k] = {
            "pct": round(100.0 * n / total, 1),
            "n": n,
            "avgPnlPct": round(statistics.mean(v["pnl"]), 1) if v["pnl"] else 0.0,
            "avgDays": round(statistics.mean(v["days"]), 2) if v["days"] else 0.0,
            "maxAdverseExcursionPct": round(min(v["mae"]), 1) if v["mae"] else 0.0,
        }
    return summary


# ---- Entry-state inference ----

def _infer_user_em_pct(req: IcScenarioRequest, closes_by_date: Dict[str, float]) -> Tuple[float, float, str]:
    """Estimate the user's entry spot + 1-sigma EM% for the requested expiry.

    Preference order:
      1) ATM IV from the *cached* chain at entry_date (option-market implied).
      2) Midpoint of short strikes as spot proxy + realized-vol-derived EM.
      3) Hard fallback: spot=(shortPut+shortCall)/2, EM=1.5% * sqrt(dte/7).
    """
    spot = closes_by_date.get(req.entry_date)
    notes = "live"
    if spot is None:
        spot = (float(req.short_put) + float(req.short_call)) / 2.0
        notes = "spot proxied from strike midpoint"

    chain = chain_cache.fetch_chain_slice(
        ticker=req.underlying, trade_date=req.entry_date, expiry=req.expiry
    )
    if chain:
        best = min(chain, key=lambda r: abs(float(r.strike) - float(spot)))
        iv = best.call_iv if best.call_iv is not None else best.put_iv
        if iv is not None and iv > 0:
            em = iv_to_em1sigma_pct(iv_pct=float(iv) * 100.0, dte_calendar_days=req.dte_calendar())
            return float(spot), float(em), "IV from cached chain"

    # Conservative fallback when we have no entry-day chain (e.g., for a future-dated
    # expiry before market open). Use a generic 15% annualized IV proxy.
    em = iv_to_em1sigma_pct(iv_pct=15.0, dte_calendar_days=req.dte_calendar())
    return float(spot), float(em), "fallback IV=15% annualized"


def _entry_regime_bucket(user_em_pct: float, req: IcScenarioRequest) -> str:
    """Rough regime bucket for the user's entry using just IV/EM magnitude.

    We convert the implied 1-sigma % into an annualized IV estimate and
    assign buckets using the Engine 2 labels. This is intentionally a
    conservative proxy — Phase 2 will swap in true Engine 2 regime.
    """
    try:
        dte_c = req.dte_calendar()
        iv_ann = float(user_em_pct) / math.sqrt(max(1, int(dte_c)) / 365.0)
    except Exception:
        iv_ann = 18.0
    if iv_ann <= 12.0:
        return "LOW"
    if iv_ann <= 18.0:
        return "MODERATE"
    if iv_ann <= 28.0:
        return "ELEVATED"
    return "NO_TRADE"


# ---- Public entrypoint ----

def run_scenario(
    request: IcScenarioRequest,
    *,
    client,
    flags: Optional[FeatureFlags] = None,
    benzinga_client: Any = None,
    store: Any = None,
) -> Dict[str, Any]:
    flags = flags or get_flags()
    ticker = str(request.underlying or "SPX").upper()
    if ticker != "SPX":
        raise ValueError("Engine 14 Phase 1 supports SPX only.")

    # 1. Load SPX closes to build analogue windows.
    today = dt.date.today()
    lookback_start = today - dt.timedelta(days=int(flags.ENGINE14_LOOKBACK_YEARS) * 370)
    bars = fetch_dailies_ohlc_range(client, ticker=ticker, start=lookback_start, end=today)
    closes_sorted: List[Tuple[str, float]] = [
        (b.trade_date, float(b.close)) for b in bars if b.close is not None
    ]
    closes_sorted.sort(key=lambda x: x[0])
    closes_by_date = {d: c for d, c in closes_sorted}
    if len(closes_sorted) < 180:
        return _empty_payload(
            request=request,
            reason="Insufficient SPX history loaded (need at least ~9 months of bars).",
        )

    # 2. Infer user's entry state.
    user_spot, user_em_pct, em_source = _infer_user_em_pct(request, closes_by_date)
    user_regime = _entry_regime_bucket(user_em_pct, request)

    # 3. Build + filter analogue universe.
    entry_dow = 0
    target_dte_calendar = 4
    try:
        entry_dow = dt.date.fromisoformat(request.entry_date).weekday()
        if entry_dow > 4:
            entry_dow = 2  # Wed fallback
        target_dte_calendar = max(
            1,
            (dt.date.fromisoformat(request.expiry) - dt.date.fromisoformat(request.entry_date)).days,
        )
    except Exception:
        pass

    # Count the user's trading-day sessions using the same close series
    # used to build analogue windows — keeps the dte_sessions filter
    # apples-to-apples with what the enumerator emits.
    target_dte_sessions = _count_trading_sessions(
        request.entry_date, request.expiry, closes_by_date
    )

    universe = build_analogue_universe(
        ticker=ticker,
        closes_sorted=closes_sorted,
        entry_dow=entry_dow,
        target_dte_calendar_days=target_dte_calendar,
        max_windows=260,
    )
    criteria = MatchCriteria(
        target_regime=user_regime,
        target_dte_sessions=target_dte_sessions,
        regime_bucket_tol=float(flags.ENGINE14_REGIME_BUCKET_TOL),
        season_mode=str(request.season_mode or "none"),
        season_value=request.season_value,
    )
    candidates = filter_analogues(universe, criteria=criteria, flags=flags)
    if len(candidates) < int(flags.ENGINE14_MIN_ANALOGUES):
        # Relax DTE filter as a second-pass attempt (still require regime match).
        relaxed = [w for w in universe
                   if abs(int(w.dte_sessions) - int(criteria.target_dte_sessions)) <= 3
                   and w.regime_bucket in (user_regime, _neighbor_bucket(user_regime))]
        if len(relaxed) >= int(flags.ENGINE14_MIN_ANALOGUES):
            candidates = relaxed
        else:
            return _empty_payload(
                request=request,
                reason=f"Not enough analogue windows (found {len(candidates)}, need ≥{flags.ENGINE14_MIN_ANALOGUES}). "
                       f"Try broader strikes, or run the backfill script to extend the chain cache.",
                analogues_considered=len(universe),
            )

    # 4. Replay each analogue.
    user_strikes = request.strike_tuple()
    paths: List[AnaloguePath] = []
    for w in candidates:
        p = _simulate_single_analogue(
            ticker=ticker,
            window=w,
            user_strikes=user_strikes,
            user_spot=user_spot,
            user_em_pct=user_em_pct,
            entry_credit=float(request.credit_received),
            profit_target_pct=float(request.profit_target_pct),
            stop_loss_pct=float(request.stop_loss_pct),
            closes_by_date=closes_by_date,
            snap_max_pts=float(flags.ENGINE14_STRIKE_SNAP_MAX_PTS),
        )
        if p is not None:
            paths.append(p)
    if len(paths) < int(flags.ENGINE14_MIN_ANALOGUES):
        return _empty_payload(
            request=request,
            reason=f"Replay yielded only {len(paths)} priceable analogues (need ≥{flags.ENGINE14_MIN_ANALOGUES}). "
                   f"Chain cache may be sparse around this regime.",
            analogues_considered=len(candidates),
        )

    # 5. Aggregate.
    outcome_summary = _summarize_outcomes(paths)
    timeline = _build_mtm_timeline(paths)
    final_pnls = [p.exit_pnl_pct for p in paths]
    mean_pnl = statistics.mean(final_pnls)
    median_pnl = statistics.median(final_pnls)
    sd_pnl = statistics.stdev(final_pnls) if len(final_pnls) > 1 else 1.0
    sharpe = (mean_pnl / sd_pnl) if sd_pnl > 1e-9 else 0.0

    exit_opt = optimize_exit_rules(
        paths=paths,
        default_profit_target_pct=float(request.profit_target_pct),
        default_stop_loss_pct=float(request.stop_loss_pct),
    )

    # Sample recent analogues for the UI table.
    paths_by_entry = sorted(paths, key=lambda p: p.entry_date, reverse=True)
    sample_n = min(int(flags.ENGINE14_MAX_ANALOGUES), len(paths_by_entry))
    matched_analogues = [
        {
            "entryDate": p.entry_date,
            "expiryDate": p.expiry_date,
            "outcome": p.outcome,
            "exitDay": p.exit_day,
            "pnlPct": round(float(p.exit_pnl_pct), 1),
            "mae": round(float(p.max_adverse_excursion_pct), 1),
            "mappedStrikes": {
                "shortPut": p.mapped_strikes[0],
                "longPut": p.mapped_strikes[1],
                "shortCall": p.mapped_strikes[2],
                "longCall": p.mapped_strikes[3],
            },
            "breached": bool(p.breached),
        }
        for p in paths_by_entry[:sample_n]
    ]

    notes = [
        f"Analogue pool: {len(paths)} windows (regime={user_regime}, tol=±{int(flags.ENGINE14_REGIME_BUCKET_TOL)}pts)",
        f"Entry EM inferred via {em_source}; 1σ={user_em_pct:.2f}%",
        "Strike mapping preserves EM-distance across historical spot levels.",
    ]

    # Phase 2: forward-looking conditioning modifiers.
    # These NEVER mutate the empirical outcomeDistribution; we emit them
    # alongside and produce an explicit adjustedOutcomeDistribution view.
    conditioning: Dict[str, Any] = {}
    adjusted_distribution: Dict[str, Any] = {}
    if getattr(flags, "ENGINE14_ENABLE_CONDITIONING", True):
        try:
            conditioning = compute_conditioning(
                entry_date=request.entry_date,
                expiry_date=request.expiry,
                orats_client=client,
                benzinga_client=benzinga_client,
                store=store,
            )
            adjusted_distribution = apply_modifiers_to_distribution(
                base_distribution=outcome_summary,
                net_tail_multiplier=float(conditioning.get("netTailMultiplier", 1.0)),
                net_wr_shift_pct=float(conditioning.get("netWinRateShiftPct", 0.0)),
            )
            for n in conditioning.get("notes", []):
                if n and n not in notes:
                    notes.append(n)
        except Exception as e:
            LOG.warning("engine14 conditioning failed: %s", e)
            conditioning = {"error": f"{type(e).__name__}: {e}"}
            adjusted_distribution = {}

    return {
        "engine": 14,
        "version": "1.1.0",
        "request": asdict(request),
        "analoguesUsed": len(paths),
        "analoguesConsidered": len(candidates),
        "analogueBucket": {
            "regime": user_regime,
            "macro": "NORMAL",
            "season": request.season_mode or "ALL",
        },
        "entryState": {
            "userSpot": round(float(user_spot), 2),
            "userEmPct": round(float(user_em_pct), 3),
            "wingWidth": round(float(request.wing_width()), 2),
            "regimeBucket": user_regime,
        },
        "outcomeDistribution": outcome_summary,
        "adjustedOutcomeDistribution": adjusted_distribution,
        "conditioningModifiers": conditioning,
        "mtmTimeline": timeline,
        "expectedValue": {
            "meanPnlPct": round(float(mean_pnl), 1),
            "medianPnlPct": round(float(median_pnl), 1),
            "sharpeProxy": round(float(sharpe), 2),
        },
        "exitRulesOptimization": exit_opt,
        "conditioningNotes": notes,
        "matchedAnalogues": matched_analogues,
    }


def _neighbor_bucket(b: str) -> str:
    try:
        i = REGIME_BUCKETS.index(b)
        if i < len(REGIME_BUCKETS) - 1:
            return REGIME_BUCKETS[i + 1]
        return REGIME_BUCKETS[i - 1]
    except ValueError:
        return b


def _count_trading_sessions(
    entry_iso: str, expiry_iso: str, trading_days: Dict[str, float]
) -> int:
    """Count trading sessions from entry through expiry (inclusive both ends).

    Uses the caller's close series as the trading-day calendar so the value
    is consistent with what `_build_matching_windows` emits as dte_sessions.
    Falls back to 1 when dates are malformed or outside the cached range.
    """
    try:
        a = dt.date.fromisoformat(entry_iso)
        b = dt.date.fromisoformat(expiry_iso)
    except Exception:
        return 1
    if b < a:
        return 1
    n = 0
    d = a
    while d <= b:
        if d.isoformat() in trading_days:
            n += 1
        d += dt.timedelta(days=1)
    return max(1, n)


def _is_iso(s: str) -> bool:
    try:
        dt.date.fromisoformat(str(s))
        return True
    except Exception:
        return False


def _empty_payload(*, request: IcScenarioRequest, reason: str, analogues_considered: int = 0) -> Dict[str, Any]:
    empty_dist = {o: {"pct": 0.0, "n": 0, "avgPnlPct": 0.0, "avgDays": 0.0, "maxAdverseExcursionPct": 0.0} for o in OUTCOMES}
    return {
        "engine": 14,
        "version": "1.1.0",
        "request": asdict(request),
        "analoguesUsed": 0,
        "analoguesConsidered": int(analogues_considered),
        "analogueBucket": {"regime": None, "macro": None, "season": request.season_mode or "ALL"},
        "entryState": None,
        "outcomeDistribution": empty_dist,
        "adjustedOutcomeDistribution": {},
        "conditioningModifiers": {},
        "mtmTimeline": [],
        "expectedValue": {"meanPnlPct": 0.0, "medianPnlPct": 0.0, "sharpeProxy": 0.0},
        "exitRulesOptimization": {
            "recommendedProfitTarget": float(request.profit_target_pct),
            "recommendedStopLoss": float(request.stop_loss_pct),
            "deltaFromDefault": {"winRatePct": 0.0, "avgPnlPct": 0.0},
        },
        "conditioningNotes": [reason],
        "matchedAnalogues": [],
    }
