from __future__ import annotations

import datetime as dt
import math
from typing import Any, Dict, List, Optional

from backend.orats_client import OratsClient
from backend.spx_ic.ohlc import (
    fetch_atm_iv_pct,
    fetch_daily_ohlc,
    iv_to_em1sigma_pct,
)
from backend.spx_ic.utils import _fmt_date, _pct_ret, _quarter_key
from backend.spx_ic.weekly_windows import WeeklyWindow, build_weekly_windows


def backtest_weekly_ic_risk(
    client: OratsClient,
    *,
    ticker: str,
    years: int,
    entry_dow: int,
    widths: List[float],
    today: Optional[dt.date] = None,
) -> Dict[str, Any]:
    """
    Risk-only weekly IC backtest.
    - Breach defined at expiry close beyond short strike distance.
    - Short strike distance set in EM multiples: width * EM1sigma% (derived from ATM IV).
    """
    now = today or dt.date.today()
    start = now - dt.timedelta(days=int(years) * 365)
    end = now

    windows = build_weekly_windows(client, ticker=ticker, start=start, end=end, entry_dow=entry_dow, max_weeks=260 * max(1, int(years)))

    rows_out: List[Dict[str, Any]] = []
    per_width: Dict[float, Dict[str, Any]] = {float(w): {"w": float(w), "n": 0, "breachEither": 0, "breachPut": 0, "breachCall": 0, "avgAbsRetPct": 0.0} for w in widths}
    per_quarter: Dict[str, Dict[str, Any]] = {q: {float(w): {"n": 0, "breachEither": 0} for w in widths} for q in ("Q1", "Q2", "Q3", "Q4")}

    used = 0
    for win in windows:
        entry_bar = fetch_daily_ohlc(client, ticker=ticker, date=win.entry_date)
        exp_bar = fetch_daily_ohlc(client, ticker=ticker, date=win.expiry_date)
        entry_px = None if entry_bar is None else entry_bar.close
        exp_px = None if exp_bar is None else exp_bar.close
        if entry_px is None or exp_px is None or entry_px <= 0:
            continue
        iv = fetch_atm_iv_pct(client, ticker=ticker, trade_date=win.entry_date, dte_target=max(1, win.dte_calendar_days))
        if iv is None or iv <= 0:
            continue

        ret = _pct_ret(entry_px, exp_px)
        abs_ret = abs(ret)
        em1 = iv_to_em1sigma_pct(iv_pct=float(iv), dte_calendar_days=max(1, win.dte_calendar_days))
        qk = _quarter_key(win.entry_date)
        used += 1

        down_mae_pct: Optional[float] = 0.0
        up_mae_pct: Optional[float] = 0.0
        d = win.entry_date
        while d <= win.expiry_date and (win.expiry_date - win.entry_date).days <= 14:
            b = fetch_daily_ohlc(client, ticker=ticker, date=d)
            if b and b.high is not None and b.low is not None and entry_px and entry_px > 0:
                up = (float(b.high) / float(entry_px) - 1.0) * 100.0
                dn = (1.0 - float(b.low) / float(entry_px)) * 100.0
                up_mae_pct = max(float(up_mae_pct or 0.0), float(up))
                down_mae_pct = max(float(down_mae_pct or 0.0), float(dn))
            d += dt.timedelta(days=1)
        mae_abs_pct = max(float(up_mae_pct or 0.0), float(down_mae_pct or 0.0))

        row = {
            "entryDate": _fmt_date(win.entry_date),
            "expiryDate": _fmt_date(win.expiry_date),
            "dte": int(win.dte_sessions),
            "dteCalendar": int(win.dte_calendar_days),
            "entryPx": round(float(entry_px), 2),
            "expiryPx": round(float(exp_px), 2),
            "retPct": round(float(ret), 3),
            "absRetPct": round(float(abs_ret), 3),
            "maeDownPct": None if down_mae_pct is None else round(float(down_mae_pct), 3),
            "maeUpPct": None if up_mae_pct is None else round(float(up_mae_pct), 3),
            "maeAbsPct": round(float(mae_abs_pct), 3),
            "ivAtmPct": round(float(iv), 2),
            "em1sigmaPct": round(float(em1), 3),
            "quarter": qk,
            "byWidth": {},
        }

        for w in widths:
            dist = float(w) * float(em1)
            breach_put = ret < -dist
            breach_call = ret > dist
            breach = bool(breach_put or breach_call)
            row["byWidth"][str(w)] = {"distPct": round(dist, 3), "breach": breach, "breachSide": ("PUT" if breach_put else "CALL" if breach_call else None)}

            acc = per_width[float(w)]
            acc["n"] += 1
            acc["breachEither"] += 1 if breach else 0
            acc["breachPut"] += 1 if breach_put else 0
            acc["breachCall"] += 1 if breach_call else 0
            acc["avgAbsRetPct"] += float(abs_ret)

            qacc = per_quarter[qk][float(w)]
            qacc["n"] += 1
            qacc["breachEither"] += 1 if breach else 0

        rows_out.append(row)

    by_width = []
    for w, acc in per_width.items():
        n = int(acc["n"])
        if n > 0:
            avg_abs = float(acc["avgAbsRetPct"]) / n
            out = dict(acc)
            out["avgAbsRetPct"] = round(avg_abs, 3)
            out["breachEitherPct"] = round(acc["breachEither"] / n * 100.0, 2)
            out["breachPutPct"] = round(acc["breachPut"] / n * 100.0, 2)
            out["breachCallPct"] = round(acc["breachCall"] / n * 100.0, 2)
            by_width.append(out)
        else:
            by_width.append({**acc, "breachEitherPct": None, "breachPutPct": None, "breachCallPct": None})
    by_width.sort(key=lambda x: x["w"])

    by_q = {}
    for qk, wmap in per_quarter.items():
        by_q[qk] = {}
        for w, acc in wmap.items():
            n = int(acc["n"])
            by_q[qk][str(w)] = {"n": n, "breachEitherPct": (round(acc["breachEither"] / n * 100.0, 2) if n else None)}

    rows_out.sort(key=lambda r: r["entryDate"], reverse=True)

    return {
        "rowsUsed": int(used),
        "rows": rows_out[:260],
        "byWidth": by_width,
        "byQuarter": by_q,
        "notes": [],
    }


def recommend_width(
    *,
    by_width: List[Dict[str, Any]],
    risk_target_breach_pct: float,
) -> Dict[str, Any]:
    """Pick the smallest width that meets breachEitherPct <= target (if possible)."""
    tgt = float(risk_target_breach_pct)
    eligible = [r for r in by_width if r.get("breachEitherPct") is not None and float(r["breachEitherPct"]) <= tgt]
    choice = eligible[0] if eligible else (by_width[-1] if by_width else None)
    if not choice:
        return {"width": None, "notes": ["No backtest rows available."]}
    return {
        "width": float(choice["w"]),
        "breachEitherPct": choice.get("breachEitherPct"),
        "notes": (["Meets risk target."] if eligible else ["No width met target; using widest candidate."]),
    }


def beta_binomial_mean(*, k: int, n: int, alpha: float = 1.0, beta: float = 1.0) -> Optional[float]:
    if n <= 0:
        return None
    return (float(k) + float(alpha)) / (float(n) + float(alpha) + float(beta))


def pctile(xs: List[float], p: float) -> Optional[float]:
    vals = sorted([float(x) for x in xs if x is not None and math.isfinite(float(x))])
    if not vals:
        return None
    if p <= 0:
        return vals[0]
    if p >= 100:
        return vals[-1]
    k = (len(vals) - 1) * (float(p) / 100.0)
    f = math.floor(k)
    c = math.ceil(k)
    if f == c:
        return vals[int(k)]
    d0 = vals[int(f)] * (c - k)
    d1 = vals[int(c)] * (k - f)
    return d0 + d1
