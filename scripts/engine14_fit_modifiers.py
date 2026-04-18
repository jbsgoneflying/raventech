#!/usr/bin/env python3
"""Fit empirical Engine 14 modifier coefficients from cached history.

Phase B of the Engine 14 fine-tuning plan. The conditioning modifiers
(`calendar`, `dealerGamma`, `creditStress`, `gapRegime`) shipped with
hand-tuned `(tail_mult, wr_shift)` tables. Once the chain cache and the
daily-market-state history have enough coverage, this script learns the
same coefficients from the empirical outcome of 1σ iron-condor replays.

Output
------
A JSON file at `data/engine14_modifier_coefficients.json` (or whatever
`FeatureFlags.ENGINE14_MODIFIER_COEFFICIENTS_PATH` points to). Every bucket
carries a `source` field — `"empirical"` when fit from >=
`--min-samples` observations, otherwise `"hand_coded"` with the original
seed value preserved so behavior never regresses.

Usage
-----
    python scripts/engine14_fit_modifiers.py                 # 2y default
    python scripts/engine14_fit_modifiers.py --years 3
    python scripts/engine14_fit_modifiers.py --dry-run       # print, don't write
    python scripts/engine14_fit_modifiers.py --min-samples 40

Design notes
------------
* We replay every weekly 1σ short-strike IC in the cached universe and
  label each replay with a simplified outcome (win / loss / tail) based
  on final P&L and short-strike breach.
* Each replay is then tagged with whatever modifier state is persisted
  for its entry date:
    - calendar:    matches Benzinga macro-events in the window.
    - creditStress: matches the DMS snapshot's cross-asset-stress label.
    - dealerGamma / gapRegime: NOT persisted historically — we keep
      hand-coded values and record `n=0, source="hand_coded"`.
* For each bucket we compute:
      baseline_tail_pct  = mean(tail-outcome rate) across all replays
      bucket_tail_pct    = mean(tail-outcome rate) inside the bucket
      tailMult           = bucket / baseline    (clamped to [0.5, 2.5])
      wrShift            = (bucket_wr_pct - baseline_wr_pct)   (clamped)
* Sample-count thresholds protect against noisy single-event coefficients.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import os
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

_HERE = os.path.abspath(os.path.dirname(__file__))
_ROOT = os.path.abspath(os.path.join(_HERE, os.pardir))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from backend.config import get_flags  # noqa: E402
from backend.engine14 import chain_cache  # noqa: E402
from backend.engine14.analogue_matcher import build_analogue_universe  # noqa: E402
from backend.engine14.chain_replay import reprice_ic  # noqa: E402
from backend.engine14.conditioning import (  # noqa: E402
    _HAND_CODED_CALENDAR,
    _HAND_CODED_CALENDAR_CAPS,
    _HAND_CODED_CREDIT_STRESS,
    _hand_coded_payload,
)
from backend.spx_ic.ohlc import fetch_dailies_ohlc_range  # noqa: E402

LOG = logging.getLogger("engine14.fit_modifiers")

_TICKER = "SPX"


# ---------------------------------------------------------------------------
# Replay outcome records
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReplayOutcome:
    entry_date: str
    expiry_date: str
    # Simplified outcome label:
    #   "tail" -> breach or stopOut (max pain)
    #   "win"  -> earlyTarget or fullCollect (profitable close)
    #   "flat" -> neither win nor tail (small chop)
    label: str
    final_pnl_pct: float
    mae_pct: float
    breached: bool


def _build_replays(
    *, lookback_years: float,
    profit_target_pct: float,
    stop_loss_pct: float,
    em_target: float,
) -> List[ReplayOutcome]:
    """Replay every cached weekly 1σ IC and return outcome labels."""
    try:
        from backend.orats_client import OratsClient
        client = OratsClient.from_env()
    except Exception as e:
        LOG.warning("Could not build ORATS client: %s — skipping replays.", e)
        return []

    today = dt.date.today()
    start = today - dt.timedelta(days=int(float(lookback_years) * 370))
    bars = fetch_dailies_ohlc_range(client, ticker=_TICKER, start=start, end=today)
    closes_by_date = {b.trade_date: float(b.close) for b in bars if b.close is not None}
    if not closes_by_date:
        LOG.warning("No OHLC bars in range — aborting.")
        return []

    closes_sorted = sorted(closes_by_date.items())
    universe = build_analogue_universe(
        ticker=_TICKER,
        closes_sorted=closes_sorted,
        entry_dow=0,
        target_dte_calendar_days=4,
    )
    LOG.info("Built universe of %d weekly windows.", len(universe))

    out: List[ReplayOutcome] = []
    for w in universe:
        try:
            spot = float(w.entry_close)
            em = float(w.entry_em_pct)
            sp = round(spot * (1.0 - em_target * em / 100.0), 2)
            sc = round(spot * (1.0 + em_target * em / 100.0), 2)
            wing_pts = max(5.0, spot * 0.005)   # 0.5% wings
            lp = round(sp - wing_pts, 2)
            lc = round(sc + wing_pts, 2)
            entry_chain = chain_cache.fetch_chain_slice(
                ticker=_TICKER, trade_date=w.entry_date, expiry=w.expiry_date,
            )
            if not entry_chain:
                continue
            priced = reprice_ic(
                chain=entry_chain,
                short_put_strike=sp, long_put_strike=lp,
                short_call_strike=sc, long_call_strike=lc,
                entry_credit=0.0, snap_max_pts=25.0,
            )
            if priced is None or priced.net_debit_to_close is None:
                continue
            entry_credit = float(priced.net_debit_to_close)
            if entry_credit <= 0:
                continue

            # Enumerate replay days.
            replay_days: List[str] = []
            cur = dt.date.fromisoformat(w.entry_date)
            end = dt.date.fromisoformat(w.expiry_date)
            while cur <= end:
                iso = cur.isoformat()
                if iso in closes_by_date:
                    replay_days.append(iso)
                cur += dt.timedelta(days=1)
            mae = 0.0
            final_pnl: Optional[float] = None
            label = "flat"
            for td in replay_days:
                chain = chain_cache.fetch_chain_slice(
                    ticker=_TICKER, trade_date=td, expiry=w.expiry_date,
                )
                if not chain:
                    continue
                mark = reprice_ic(
                    chain=chain,
                    short_put_strike=sp, long_put_strike=lp,
                    short_call_strike=sc, long_call_strike=lc,
                    entry_credit=entry_credit, snap_max_pts=25.0,
                )
                if mark is None:
                    continue
                pnl_pct = float(mark.pnl_pct_of_credit)
                if pnl_pct < mae:
                    mae = pnl_pct
                # Exit rules:
                if label == "flat":
                    if pnl_pct >= profit_target_pct:
                        label, final_pnl = "win", pnl_pct
                    elif pnl_pct <= -stop_loss_pct:
                        label, final_pnl = "tail", pnl_pct
                if label != "flat":
                    break
                final_pnl = pnl_pct

            if final_pnl is None:
                continue
            # Breach check at expiry.
            last_close = closes_by_date.get(replay_days[-1]) if replay_days else None
            breached = bool(last_close is not None and (last_close < sp or last_close > sc))
            # Late-cycle escalations when no exit fired.
            if label == "flat":
                if breached and final_pnl <= -50.0:
                    label = "tail"
                elif final_pnl >= 0.0:
                    label = "win"
                elif final_pnl < 0.0:
                    label = "flat"
            out.append(ReplayOutcome(
                entry_date=w.entry_date, expiry_date=w.expiry_date,
                label=label, final_pnl_pct=float(final_pnl),
                mae_pct=float(mae), breached=breached,
            ))
        except Exception as e:
            LOG.debug("replay %s..%s failed: %s", w.entry_date, w.expiry_date, e)
            continue
    LOG.info("Produced %d outcome labels.", len(out))
    return out


# ---------------------------------------------------------------------------
# Bucket statistics
# ---------------------------------------------------------------------------

def _tail_and_wr(paths: List[ReplayOutcome]) -> Tuple[float, float, int]:
    """Return (tail_pct, wr_pct, n) for a set of outcomes."""
    n = len(paths)
    if n == 0:
        return (0.0, 0.0, 0)
    tail = 100.0 * sum(1 for p in paths if p.label == "tail") / n
    win = 100.0 * sum(1 for p in paths if p.label == "win") / n
    return (tail, win, n)


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def _derive_tail_mult(baseline: float, bucket: float) -> float:
    if baseline <= 0:
        return 1.0
    return _clamp(bucket / baseline, 0.5, 2.5)


def _derive_wr_shift(baseline: float, bucket: float) -> float:
    return _clamp(bucket - baseline, -15.0, 15.0)


# ---------------------------------------------------------------------------
# Calendar fitter (Benzinga)
# ---------------------------------------------------------------------------

def _fit_calendar(
    paths: List[ReplayOutcome], *, bz_client: Any, min_samples: int,
) -> List[Dict[str, Any]]:
    seed_rows = [
        {"keyword": kw, "severity": sev, "tailBump": bump, "wrShift": wr,
         "source": "hand_coded", "n": 0}
        for kw, sev, bump, wr in _HAND_CODED_CALENDAR
    ]
    if bz_client is None or not paths:
        return seed_rows
    try:
        from backend.macro_events import macro_events_by_date
    except Exception:
        return seed_rows

    baseline_tail, baseline_wr, n_total = _tail_and_wr(paths)
    if n_total == 0:
        return seed_rows

    by_kw: Dict[str, List[ReplayOutcome]] = defaultdict(list)
    for p in paths:
        try:
            entry = dt.date.fromisoformat(p.entry_date)
            expiry = dt.date.fromisoformat(p.expiry_date)
            events, _, _ = macro_events_by_date(
                bz=bz_client, start=entry, end=expiry,
                pagesize=500, max_pages=2, importance_min=3, country="US",
            )
        except Exception:
            continue
        descs: List[str] = []
        for _, rows in (events or {}).items():
            for r in rows or []:
                descs.append(
                    str(r.get("title") or r.get("event_name")
                        or r.get("description") or r.get("short") or r.get("key") or "")
                )
        joined = " ".join(d.lower() for d in descs)
        for seed in seed_rows:
            kw = str(seed["keyword"]).lower()
            if kw and kw in joined:
                by_kw[seed["keyword"]].append(p)

    out: List[Dict[str, Any]] = []
    for seed in seed_rows:
        bucket = by_kw.get(seed["keyword"], [])
        bt, bw, n = _tail_and_wr(bucket)
        if n >= min_samples and baseline_tail > 0:
            tail_bump = _clamp(bt / baseline_tail - 1.0, -0.3, 1.2)
            wr_shift = _derive_wr_shift(baseline_wr, bw)
            out.append({
                "keyword": seed["keyword"],
                "severity": seed["severity"],
                "tailBump": round(float(tail_bump), 3),
                "wrShift": round(float(wr_shift), 2),
                "source": "empirical",
                "n": int(n),
            })
        else:
            out.append(dict(seed, n=int(n)))
    return out


# ---------------------------------------------------------------------------
# Credit-stress fitter (DMS)
# ---------------------------------------------------------------------------

def _fit_credit_stress(
    paths: List[ReplayOutcome], *, store: Any, min_samples: int,
) -> Dict[str, Dict[str, Any]]:
    seed = {
        label: {"tailMult": t, "wrShift": w, "severity": s,
                "source": "hand_coded", "n": 0}
        for label, (t, w, s) in _HAND_CODED_CREDIT_STRESS.items()
    }
    if store is None or not paths:
        return seed
    try:
        from backend.daily_market_state import load_dms
    except Exception:
        return seed

    baseline_tail, baseline_wr, n_total = _tail_and_wr(paths)
    if n_total == 0:
        return seed

    by_label: Dict[str, List[ReplayOutcome]] = defaultdict(list)
    for p in paths:
        try:
            dms = load_dms(p.entry_date, store)
        except Exception:
            continue
        if dms is None:
            continue
        cas = getattr(dms, "cross_asset_stress", {}) or {}
        label = str(cas.get("composite_label") or "Neutral")
        by_label[label].append(p)

    out: Dict[str, Dict[str, Any]] = {}
    for label, seed_row in seed.items():
        bucket = by_label.get(label, [])
        t, w, n = _tail_and_wr(bucket)
        if n >= min_samples:
            out[label] = {
                "tailMult": round(_derive_tail_mult(baseline_tail, t), 3),
                "wrShift":  round(_derive_wr_shift(baseline_wr, w), 2),
                "severity": seed_row["severity"],
                "source": "empirical",
                "n": int(n),
            }
        else:
            out[label] = dict(seed_row, n=int(n))
    return out


# ---------------------------------------------------------------------------
# Optional deps
# ---------------------------------------------------------------------------

def _get_bz_client():
    try:
        from backend.deps import get_benzinga_client_optional
        return get_benzinga_client_optional()
    except Exception:
        return None


def _get_store():
    try:
        from backend.redis_store import get_store_optional
        return get_store_optional()
    except Exception:
        return None


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _configure_logging(verbose: bool) -> None:
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s - %(message)s",
    )


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--years", type=float, default=2.0,
                    help="Lookback window in years (default 2).")
    ap.add_argument("--min-samples", type=int, default=30,
                    help="Min observations per bucket before empirical overwrite.")
    ap.add_argument("--profit-target-pct", type=float, default=50.0)
    ap.add_argument("--stop-loss-pct", type=float, default=200.0)
    ap.add_argument("--em-target", type=float, default=1.0,
                    help="Short-strike EM-multiple used to place the replay IC.")
    ap.add_argument("--output", type=str, default="",
                    help="Output path (default: FeatureFlags.ENGINE14_MODIFIER_COEFFICIENTS_PATH).")
    ap.add_argument("--dry-run", action="store_true",
                    help="Print the coefficients JSON to stdout instead of writing.")
    ap.add_argument("-v", "--verbose", action="store_true")
    args = ap.parse_args(argv)

    _configure_logging(args.verbose)

    f = get_flags()
    out_path = args.output or str(getattr(f, "ENGINE14_MODIFIER_COEFFICIENTS_PATH",
                                          "data/engine14_modifier_coefficients.json"))

    LOG.info("Building replay set over %.1fy ...", args.years)
    paths = _build_replays(
        lookback_years=float(args.years),
        profit_target_pct=float(args.profit_target_pct),
        stop_loss_pct=float(args.stop_loss_pct),
        em_target=float(args.em_target),
    )
    baseline_tail, baseline_wr, n_total = _tail_and_wr(paths)
    LOG.info("Replay pool: n=%d, baseline tail=%.1f%%, wr=%.1f%%",
             n_total, baseline_tail, baseline_wr)

    bz = _get_bz_client()
    store = _get_store()

    LOG.info("Fitting calendar coefficients ...")
    calendar_rows = _fit_calendar(paths, bz_client=bz, min_samples=int(args.min_samples))
    LOG.info("Fitting credit-stress coefficients ...")
    credit_map = _fit_credit_stress(paths, store=store, min_samples=int(args.min_samples))

    seed = _hand_coded_payload()
    payload = {
        "version": 1,
        "generatedAt": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        "generator": "scripts/engine14_fit_modifiers.py",
        "lookbackYears": float(args.years),
        "sampleCount": {
            "total": int(n_total),
            "calendar": sum(int(r.get("n", 0)) for r in calendar_rows),
            "creditStress": sum(int(r.get("n", 0)) for r in credit_map.values()),
            "dealerGamma": 0,
            "gapRegime": 0,
        },
        "notes": (
            f"Fit from {n_total} cached IC replays; min_samples={args.min_samples}. "
            "dealerGamma / gapRegime remain hand-coded until historical snapshots "
            "are persisted (Phase C1)."
        ),
        "calendar": {
            "keywords": calendar_rows,
            **_HAND_CODED_CALENDAR_CAPS,
        },
        "dealerGamma": seed["dealerGamma"],
        "creditStress": credit_map,
        "gapRegime": seed["gapRegime"],
    }

    blob = json.dumps(payload, indent=2, sort_keys=False)
    if args.dry_run:
        sys.stdout.write(blob + "\n")
        return 0

    os.makedirs(os.path.dirname(os.path.abspath(out_path)) or ".", exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        fh.write(blob + "\n")
    LOG.info("Wrote %d bytes to %s", len(blob), out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
