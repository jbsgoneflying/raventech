"""
News Risk Engine: Aggregates macro events, news headlines, and analyst ratings
for weekly event risk planning.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from backend.benzinga_client import BenzingaClient
from backend.macro_events import macro_events_by_date, _macro_key, _classify_macro
from backend.macro_event_stats import compute_macro_event_stats
from backend.macro_playbook import get_playbook
from backend.orats_client import OratsClient
from backend.universe import load_universe_sp500_and_nasdaq100

LOG = logging.getLogger(__name__)


def _fmt_date(d: dt.date) -> str:
    return d.isoformat()


def _parse_date(s: Any) -> Optional[dt.date]:
    try:
        return dt.date.fromisoformat(str(s)[:10])
    except Exception:
        return None


def _get_week_bounds(week_offset: int = 0) -> Tuple[dt.date, dt.date]:
    """
    Get the Monday and Friday of the target week.
    week_offset: 0 = current week, 1 = next week, -1 = last week
    """
    today = dt.date.today()
    # Find Monday of current week
    days_since_monday = today.weekday()
    current_monday = today - dt.timedelta(days=days_since_monday)
    
    # Apply offset
    target_monday = current_monday + dt.timedelta(weeks=week_offset)
    target_friday = target_monday + dt.timedelta(days=4)
    
    return target_monday, target_friday


def _generate_event_id(event_type: str, name: str, date: str, ticker: Optional[str] = None) -> str:
    """Generate a unique ID for an event."""
    key = f"{event_type}:{name}:{date}:{ticker or ''}"
    return hashlib.md5(key.encode()).hexdigest()[:12]


def _compute_day_risk_level(events: List[dict]) -> str:
    """
    Compute daily risk level based on events.
    HIGH: FOMC, CPI, NFP, or 3+ high-importance events
    MEDIUM: Treasury auction, PPI, or 2 high-importance events
    LOW: 0-1 minor events
    """
    high_impact_keys = {"CPI", "NFP", "FOMC_RATE_DECISION"}
    medium_impact_keys = {"PPI", "FOMC_MINUTES", "TREASURY_AUCTION", "TREASURY_REFUNDING", "RETAIL_SALES"}
    
    has_high_impact = False
    has_medium_impact = False
    high_importance_count = 0
    
    for ev in events:
        key = ev.get("macroKey")
        importance = ev.get("importance", 0)
        
        if key in high_impact_keys:
            has_high_impact = True
        if key in medium_impact_keys:
            has_medium_impact = True
        if importance >= 4:
            high_importance_count += 1
    
    if has_high_impact or high_importance_count >= 3:
        return "HIGH"
    if has_medium_impact or high_importance_count >= 2:
        return "MEDIUM"
    return "LOW"


def _get_spx_impact_simple(
    *,
    key: str,
    bz: BenzingaClient,
    orats: OratsClient,
) -> Optional[Dict[str, Any]]:
    """
    Get simplified SPX impact data for a macro event key.
    Returns median percent and direction (up/down/volatile).
    """
    try:
        stats = compute_macro_event_stats(
            key=key,
            bz=bz,
            orats=orats,
            lookback_years=5,
            max_events=60,
        )
        if not stats.get("enabled"):
            return None
        
        spy_data = stats.get("spy", {})
        event_day = spy_data.get("eventDayCloseToClose", {})
        
        median_pct = event_day.get("medianPct")
        median_abs_pct = event_day.get("medianAbsPct")
        sample_size = event_day.get("n", 0)
        
        if median_pct is None or median_abs_pct is None:
            return None
        
        # Determine direction
        # If median is close to zero but abs is significant, it's volatile (bidirectional)
        if abs(median_pct) < 0.1 and median_abs_pct >= 0.3:
            direction = "volatile"
        elif median_pct > 0.05:
            direction = "up"
        elif median_pct < -0.05:
            direction = "down"
        else:
            direction = "volatile"
        
        return {
            "medianPct": round(median_abs_pct, 2),  # Use absolute for display
            "direction": direction,
            "sampleSize": sample_size,
        }
    except Exception as e:
        LOG.debug("Failed to get SPX impact for %s: %s", key, e)
        return None


def _fetch_ratings_for_week(
    bz: BenzingaClient,
    start: dt.date,
    end: dt.date,
    universe: Set[str],
) -> Dict[str, List[dict]]:
    """
    Fetch analyst ratings for the week, filtered to universe tickers.
    Returns events grouped by date.
    """
    out: Dict[str, List[dict]] = {}
    
    try:
        resp = bz.calendar_ratings(
            date_from=_fmt_date(start),
            date_to=_fmt_date(end),
            pagesize=500,
        )
        
        for row in (resp.rows or []):
            if not isinstance(row, dict):
                continue
            
            date_str = str(row.get("date") or "")[:10]
            d = _parse_date(date_str)
            if d is None or d < start or d > end:
                continue
            
            ticker = str(row.get("ticker") or "").strip().upper()
            if not ticker or ticker not in universe:
                continue
            
            # Extract rating details
            action_company = row.get("action_company") or row.get("action") or ""
            action_pt = row.get("action_pt") or ""
            analyst = row.get("analyst") or row.get("analyst_name") or ""
            firm = row.get("analyst_firm") or row.get("firm") or ""
            rating_current = row.get("rating_current") or row.get("rating") or ""
            rating_prior = row.get("rating_prior") or ""
            pt_current = row.get("pt_current") or row.get("price_target") or ""
            pt_prior = row.get("pt_prior") or ""
            
            ev = {
                "id": _generate_event_id("RATING", ticker, date_str, ticker),
                "type": "RATING",
                "category": "ANALYST",
                "name": f"{ticker}: {action_company}" if action_company else f"{ticker} Rating",
                "time": None,
                "importance": 2,  # Ratings are generally lower impact
                "ticker": ticker,
                "macroKey": None,
                "spxImpact": None,
                "details": {
                    "ticker": ticker,
                    "actionCompany": action_company,
                    "actionPt": action_pt,
                    "analyst": analyst,
                    "firm": firm,
                    "ratingCurrent": rating_current,
                    "ratingPrior": rating_prior,
                    "ptCurrent": pt_current,
                    "ptPrior": pt_prior,
                },
            }
            out.setdefault(date_str, []).append(ev)
    except Exception as e:
        LOG.warning("Failed to fetch ratings: %s", e)
    
    return out


def _fetch_news_for_week(
    bz: BenzingaClient,
    start: dt.date,
    end: dt.date,
) -> Dict[str, List[dict]]:
    """
    Fetch high-importance news headlines for the week.
    Returns events grouped by date.
    """
    out: Dict[str, List[dict]] = {}
    
    try:
        # Fetch market-moving news (WIIM channel)
        resp = bz.news(
            date_from=_fmt_date(start),
            date_to=_fmt_date(end),
            channels="WIIM",  # Why It's Important Markets
            page_size=100,
            sort="created:desc",
        )
        
        for row in (resp.rows or []):
            if not isinstance(row, dict):
                continue
            
            # Extract date from created or updated timestamp
            created = row.get("created") or row.get("updated") or row.get("date") or ""
            date_str = str(created)[:10]
            d = _parse_date(date_str)
            if d is None or d < start or d > end:
                continue
            
            title = row.get("title") or row.get("headline") or ""
            if not title:
                continue
            
            # Truncate long titles
            short_title = title if len(title) <= 60 else (title[:57] + "...")
            
            tickers = row.get("tickers") or []
            if isinstance(tickers, str):
                tickers = [t.strip() for t in tickers.split(",") if t.strip()]
            
            ev = {
                "id": _generate_event_id("NEWS", title[:30], date_str),
                "type": "NEWS",
                "category": "HEADLINE",
                "name": short_title,
                "time": None,
                "importance": 3,  # WIIM news is moderately important
                "ticker": tickers[0] if tickers else None,
                "macroKey": None,
                "spxImpact": None,
                "details": {
                    "title": title,
                    "tickers": tickers,
                    "url": row.get("url"),
                    "channels": row.get("channels"),
                },
            }
            out.setdefault(date_str, []).append(ev)
    except Exception as e:
        LOG.warning("Failed to fetch news: %s", e)
    
    return out


def build_news_risk_payload(
    *,
    bz: BenzingaClient,
    orats: OratsClient,
    week_offset: int = 0,
) -> Dict[str, Any]:
    """
    Build the complete news risk payload for a given week.
    
    Args:
        bz: Benzinga client
        orats: ORATS client (for SPX impact stats)
        week_offset: 0 = current week, 1 = next week, -1 = last week
    
    Returns:
        Complete payload with days array and metadata
    """
    week_start, week_end = _get_week_bounds(week_offset)
    
    # Load universe for filtering ratings
    universe = set(load_universe_sp500_and_nasdaq100())
    
    # Fetch all data sources
    LOG.info("Fetching news risk data for %s to %s", week_start, week_end)
    
    # 1. Macro events (already includes playbook)
    macro_by_date, macro_sources, macro_notes = macro_events_by_date(
        bz=bz,
        start=week_start,
        end=week_end,
        importance_min=3,
    )
    
    # 2. Ratings
    ratings_by_date = _fetch_ratings_for_week(bz, week_start, week_end, universe)
    
    # 3. News headlines
    news_by_date = _fetch_news_for_week(bz, week_start, week_end)
    
    # Cache for SPX impact lookups
    spx_impact_cache: Dict[str, Optional[Dict[str, Any]]] = {}
    
    # Build days array
    days: List[Dict[str, Any]] = []
    total_events = 0
    
    current = week_start
    while current <= week_end:
        date_str = _fmt_date(current)
        day_name = current.strftime("%A")
        
        events: List[Dict[str, Any]] = []
        
        # Add macro events
        for macro_ev in macro_by_date.get(date_str, []):
            key = macro_ev.get("key")
            
            # Get SPX impact (with caching)
            spx_impact = None
            if key:
                if key not in spx_impact_cache:
                    spx_impact_cache[key] = _get_spx_impact_simple(key=key, bz=bz, orats=orats)
                spx_impact = spx_impact_cache.get(key)
            
            ev = {
                "id": _generate_event_id("MACRO", macro_ev.get("title", ""), date_str),
                "type": "MACRO",
                "category": macro_ev.get("kind", "ECON"),
                "name": macro_ev.get("short") or macro_ev.get("title", ""),
                "time": macro_ev.get("timeEt"),
                "importance": macro_ev.get("importance", 3),
                "ticker": None,
                "macroKey": key,
                "spxImpact": spx_impact,
                "details": {
                    "title": macro_ev.get("title"),
                    "kind": macro_ev.get("kind"),
                    "forecast": macro_ev.get("forecast"),
                    "previous": macro_ev.get("previous"),
                    "actual": macro_ev.get("actual"),
                    "unit": macro_ev.get("unit"),
                    "period": macro_ev.get("period"),
                    "playbook": macro_ev.get("playbook"),
                },
            }
            events.append(ev)
        
        # Add ratings (limit to prevent clutter)
        for rating_ev in ratings_by_date.get(date_str, [])[:10]:
            events.append(rating_ev)
        
        # Add news (limit to prevent clutter)
        for news_ev in news_by_date.get(date_str, [])[:5]:
            events.append(news_ev)
        
        # Sort events: macro first (by importance desc), then ratings, then news
        def event_sort_key(e: dict) -> tuple:
            type_order = {"MACRO": 0, "RATING": 1, "NEWS": 2}
            return (type_order.get(e.get("type", ""), 3), -(e.get("importance", 0)))
        
        events.sort(key=event_sort_key)
        
        # Compute day risk level
        risk_level = _compute_day_risk_level(events)
        
        total_events += len(events)
        
        days.append({
            "date": date_str,
            "dayName": day_name,
            "events": events,
            "riskLevel": risk_level,
            "eventCount": len(events),
        })
        
        current += dt.timedelta(days=1)
    
    return {
        "weekStart": _fmt_date(week_start),
        "weekEnd": _fmt_date(week_end),
        "weekOffset": week_offset,
        "days": days,
        "meta": {
            "asOfDate": _fmt_date(dt.date.today()),
            "totalEvents": total_events,
            "sources": macro_sources + ["benzinga:/calendar/ratings", "benzinga:/news"],
            "notes": macro_notes,
        },
    }
