from __future__ import annotations

import datetime as dt
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from backend.benzinga_client import BenzingaClient
from backend.earnings_calendar import benzinga_next_earnings
from backend.orats_client import OratsClient, OratsError


def _parse_date(s: str) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, float(x)))


def _safe_int(v: Any) -> Optional[int]:
    try:
        if v is None:
            return None
        return int(float(v))
    except Exception:
        return None


def _safe_float(v: Any) -> Optional[float]:
    try:
        if v is None:
            return None
        x = float(v)
        if x != x:
            return None
        return x
    except Exception:
        return None


def _uniq(seq: List[str]) -> List[str]:
    out: List[str] = []
    seen = set()
    for s in seq:
        if s in seen:
            continue
        seen.add(s)
        out.append(s)
    return out


@dataclass(frozen=True)
class _Window:
    # Next earnings date (if known). Kept for display/context; NOT used to set window.
    earn_anchor: Optional[str]
    # Rolling window anchor (today, ET) used to set start/end.
    window_anchor: str
    start: str
    end: str


def _build_window(*, now: dt.date, earn_date_next: Optional[str]) -> _Window:
    # Rolling window (requested): today .. today+7 (ET), regardless of earnings date.
    start = now
    end = now + dt.timedelta(days=7)
    earn_anchor = _fmt_date(_parse_date(earn_date_next)) if earn_date_next and _parse_date(earn_date_next) else None
    return _Window(earn_anchor=earn_anchor, window_anchor=_fmt_date(now), start=_fmt_date(start), end=_fmt_date(end))


def compute_event_risk_overlay(
    bz: BenzingaClient,
    *,
    ticker: str,
    as_of_date: str,
    now: dt.date,
    earn_date_next: Optional[str] = None,
    orats: Optional[OratsClient] = None,
) -> Dict[str, Any]:
    """
    Compute an additive event-risk overlay from Benzinga data.

    Output is deterministic given:
    - as_of_date (for anchoring)
    - now (to pick rolling windows)
    - Benzinga responses (cached in BenzingaClient)
    """
    t = str(ticker).strip().upper()
    asof = str(as_of_date)[:10]
    win = _build_window(now=now, earn_date_next=earn_date_next)

    notes: List[str] = []
    sources: List[str] = []

    # ---- Macro proximity (Economic Calendar) ----
    macro_score = 0.0
    macro_top: List[str] = []
    macro_count = 0
    macro_max_importance = None
    try:
        # Filter client-side to avoid being too strict on country; importance is an integer [0..5].
        econ = bz.calendar_economics(date_from=win.start, date_to=win.end, pagesize=1000, page=0)
        sources.append("benzinga:/calendar/economics")
        rows = econ.rows or []
        # Prefer US high-importance events.
        hi = []
        for r in rows:
            imp = _safe_int(r.get("importance"))
            ctry = str(r.get("country") or "").upper()
            if imp is None:
                continue
            if ctry and ctry not in ("US", "UNITED STATES", "USA"):
                continue
            if imp >= 3:
                hi.append(r)
        macro_count = len(hi)
        macro_max_importance = max((_safe_int(r.get("importance")) or 0) for r in hi) if hi else None
        macro_top = _uniq([f'{str(r.get("date") or "")[:10]} {str(r.get("event_name") or "").strip()}' for r in hi][:5])
        # Score: saturate at 5 high-impact events.
        macro_score = _clamp01(macro_count / 5.0)
    except Exception as e:
        notes.append(f"macroProximity unavailable: {type(e).__name__}: {e}")

    # ---- Headline shock (News + WIIM channel) ----
    headline_score = 0.0
    news_count = 0
    wiim_count = 0
    try:
        news = bz.news(
            tickers=t,
            date_from=_fmt_date(now),
            date_to=_fmt_date(now + dt.timedelta(days=7)),
            page_size=50,
            display_output="headline",
        )
        sources.append("benzinga:/news")
        news_rows = news.rows or []
        news_count = len(news_rows)
    except Exception as e:
        notes.append(f"headlineShock/news unavailable: {type(e).__name__}: {e}")

    try:
        wiim = bz.news(
            tickers=t,
            date_from=_fmt_date(now),
            date_to=_fmt_date(now + dt.timedelta(days=7)),
            channels="WIIM",
            page_size=50,
            display_output="headline",
        )
        sources.append("benzinga:/news?channels=WIIM")
        wiim_rows = wiim.rows or []
        wiim_count = len(wiim_rows)
    except Exception as e:
        # Not all plans may have WIIM; treat as optional but make it diagnosable.
        notes.append(f"headlineShock/wiim unavailable: {type(e).__name__}: {e}")

    # Score: 0.5 if any news, +0.5 if any WIIM, capped to 1.0.
    headline_score = _clamp01((0.5 if news_count > 0 else 0.0) + (0.5 if wiim_count > 0 else 0.0))

    # ---- Analyst cluster (Calendar Ratings) ----
    ratings_score = 0.0
    ratings_count = 0
    ratings_actions: List[str] = []
    try:
        rat = bz.calendar_ratings(
            tickers=t,
            date_from=_fmt_date(now - dt.timedelta(days=7)),
            date_to=_fmt_date(now),
            pagesize=1000,
            page=0,
        )
        sources.append("benzinga:/calendar/ratings")
        rows = rat.rows or []
        ratings_count = len(rows)
        ratings_actions = _uniq([str(r.get("action_company") or r.get("action_pt") or "").strip() for r in rows if (r.get("action_company") or r.get("action_pt"))][:5])
        # Score: saturate at 3 ratings in a week.
        ratings_score = _clamp01(ratings_count / 3.0)
    except Exception as e:
        notes.append(f"analystCluster unavailable: {type(e).__name__}: {e}")

    # ---- Options activity (Signals: option_activity) ----
    options_score = 0.0
    options_activity: Dict[str, Any] = {"enabled": False, "mode": "orats_live_strikes_proxy", "score01": None}
    # Replacement for Benzinga Signals: lightweight ORATS LIVE proxy computed from live strikes.
    # This is current-only (not true 3d history) but provides a practical “unusual flow” hint
    # without additional providers or many API calls.
    if orats is not None and callable(getattr(orats, "live_strikes", None)):
        try:
            fields = "ticker,tradeDate,expirDate,strike,spotPrice,stockPrice,callVolume,putVolume,callOpenInterest,putOpenInterest"
            rows = orats.live_strikes(ticker=t, fields=fields).rows or []
            rows = [r for r in rows if isinstance(r, dict)]
            if rows:
                total_call_vol = 0.0
                total_put_vol = 0.0
                total_call_oi = 0.0
                total_put_oi = 0.0
                vol_by_strike: Dict[str, float] = {}
                for r in rows:
                    cv = _safe_float(r.get("callVolume")) or 0.0
                    pv = _safe_float(r.get("putVolume")) or 0.0
                    co = _safe_float(r.get("callOpenInterest")) or 0.0
                    po = _safe_float(r.get("putOpenInterest")) or 0.0
                    total_call_vol += max(0.0, cv)
                    total_put_vol += max(0.0, pv)
                    total_call_oi += max(0.0, co)
                    total_put_oi += max(0.0, po)
                    k = str(r.get("strike") or "").strip()
                    if k:
                        vol_by_strike[k] = float(vol_by_strike.get(k, 0.0) + max(0.0, cv) + max(0.0, pv))

                total_vol = total_call_vol + total_put_vol
                total_oi = total_call_oi + total_put_oi
                pc_vol = (total_put_vol / max(1e-9, total_call_vol)) if total_call_vol > 0 else None
                vol_oi = (total_vol / max(1.0, total_oi)) if total_oi > 0 else None

                top5 = sorted(vol_by_strike.items(), key=lambda kv: -float(kv[1]))[:5]
                top5_conc = (sum(v for _, v in top5) / total_vol) if total_vol > 0 else None

                # Score heuristics (explainable, bounded):
                # - Higher if volume is large vs OI (turnover), and/or concentrated in few strikes.
                # These are proxies for “unusual activity” without needing a dedicated signals feed.
                s_voloi = 0.0 if vol_oi is None else _clamp01(float(vol_oi) / 0.20)  # 0.20 ~= “high turnover”
                s_conc = 0.0 if top5_conc is None else _clamp01(float(top5_conc) / 0.60)  # 60% in top5 is very concentrated
                options_score = _clamp01(0.60 * s_voloi + 0.40 * s_conc)

                options_activity = {
                    "enabled": True,
                    "mode": "orats_live_strikes_proxy",
                    "asOf": _fmt_date(now),
                    "score01": round(float(options_score), 3),
                    "totalVolume": int(round(total_vol)),
                    "totalOI": int(round(total_oi)),
                    "putCallVolRatio": None if pc_vol is None else round(float(pc_vol), 3),
                    "volOverOi": None if vol_oi is None else round(float(vol_oi), 3),
                    "top5VolConcentration": None if top5_conc is None else round(float(top5_conc), 3),
                    "topStrikesByVol": [f"{k} ({int(round(v))})" for k, v in top5 if v is not None],
                    "notes": [
                        "Proxy computed from ORATS LIVE strikes (current-only), not Benzinga Signals.",
                    ],
                }
        except OratsError:
            # If ORATS live is not entitled for this ticker, omit the component entirely (UI will hide the line).
            options_activity = {"enabled": False, "mode": "orats_live_strikes_proxy", "score01": None}
        except Exception:
            options_activity = {"enabled": False, "mode": "orats_live_strikes_proxy", "score01": None}

    # Weighted combination (simple + explainable).
    score01 = _clamp01(0.35 * macro_score + 0.25 * headline_score + 0.25 * ratings_score + 0.15 * options_score)
    label = "LOW" if score01 < 0.33 else "MED" if score01 < 0.66 else "HIGH"

    return {
        "enabled": True,
        "asOfDate": asof,
        "windowAnchorDate": win.window_anchor,
        "earnDateNext": win.earn_anchor,
        "window": {"start": win.start, "end": win.end},
        "score01": round(float(score01), 3),
        "label": label,
        "components": {
            "macroProximity": {
                "score01": round(float(macro_score), 3),
                "countHighImpactUS": int(macro_count),
                "maxImportance": macro_max_importance,
                "top": macro_top,
            },
            "headlineShock": {
                "score01": round(float(headline_score), 3),
                "newsCount3d": int(news_count),
                "wiimCount3d": int(wiim_count),
            },
            "analystCluster": {
                "score01": round(float(ratings_score), 3),
                "ratingsCount7d": int(ratings_count),
                "actions": ratings_actions,
            },
            "optionsActivity": {
                **options_activity,
            },
        },
        "sources": _uniq(sources),
        "notes": notes,
    }


def compute_event_risk_overlay_optional(
    bz: Optional[BenzingaClient],
    *,
    ticker: str,
    as_of_date: str,
    now: dt.date,
    earn_date_next: Optional[str] = None,
    orats: Optional[OratsClient] = None,
) -> Dict[str, Any]:
    if bz is None:
        return {
            "enabled": False,
            "asOfDate": str(as_of_date)[:10],
            "earnDateNext": None if not earn_date_next else str(earn_date_next)[:10],
            "window": None,
            "score01": None,
            "label": None,
            "components": {},
            "sources": [],
            "notes": ["Benzinga unavailable (no client)."],
        }
    return compute_event_risk_overlay(bz, ticker=ticker, as_of_date=as_of_date, now=now, earn_date_next=earn_date_next, orats=orats)


