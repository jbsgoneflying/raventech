"""Desk Intelligence — Context assembly, DMS diffing, historical echoes,
theme convergence detection, asymmetry scanning, and action item generation.

This module is pure Python (no LLM calls). It gathers and cross-references
all available data sources into a structured context dict that the LLM
synthesis layer can consume.
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import os
from typing import Any, Dict, List, Optional, Tuple

from backend.redis_store import get_store_optional
from backend.daily_market_state import load_dms as _load_dms_by_date

LOG = logging.getLogger(__name__)

PORTFOLIO_CAPITAL = float(os.getenv("RTV2_PORTFOLIO_CAPITAL", "500000"))


# ── DMS Loading Helpers ──────────────────────────────────────────────────

def _store():
    return get_store_optional()


def _load_dms(date_str: str) -> Optional[dict]:
    store = _store()
    if store is None:
        return None
    dms = _load_dms_by_date(date_str, store)
    return dms.to_dict() if dms else None


def _trading_days_back(n: int) -> List[str]:
    """Return the last *n* weekday date strings ending at today."""
    dates: List[str] = []
    d = dt.date.today()
    while len(dates) < n:
        if d.weekday() < 5:
            dates.append(d.isoformat())
        d -= dt.timedelta(days=1)
    return dates


# ── DMS Diffing ──────────────────────────────────────────────────────────

_DIFF_FIELDS = [
    ("regime", "state"),
    ("flow_pressure", "score"),
    ("flow_pressure", "state"),
    ("vol_state", "level"),
    ("vol_state", "term_structure"),
    ("vol_state", "skew"),
]


def _extract(dms: dict, section: str, field: str) -> Any:
    sec = dms.get(section)
    if isinstance(sec, dict):
        return sec.get(field)
    return None


def compute_dms_diff(current: dict, prior: dict) -> List[dict]:
    """Field-by-field diff between two DMS snapshots."""
    changes: List[dict] = []
    for section, field in _DIFF_FIELDS:
        cur_val = _extract(current, section, field)
        pri_val = _extract(prior, section, field)
        if cur_val != pri_val:
            changes.append({
                "field": f"{section}.{field}",
                "from": pri_val,
                "to": cur_val,
            })

    cur_gates = current.get("engine_gates") or {}
    pri_gates = prior.get("engine_gates") or {}
    for eng in ("earnings", "red_dog", "ichimoku", "index_income", "post_event_ext"):
        cv = cur_gates.get(eng)
        pv = pri_gates.get(eng)
        if cv != pv:
            changes.append({
                "field": f"engine_gates.{eng}",
                "from": pv,
                "to": cv,
            })

    return changes


def build_dms_evolution(today_dms: dict) -> dict:
    """Compare today's DMS against 1d, 5d, and 20d ago."""
    dates = _trading_days_back(21)
    result: Dict[str, Any] = {"today": today_dms.get("date")}
    comparisons = {"vs_1d": 1, "vs_5d": 5, "vs_20d": 20}
    for label, offset in comparisons.items():
        if offset < len(dates):
            prior = _load_dms(dates[offset])
            if prior:
                result[label] = {
                    "date": prior.get("date"),
                    "changes": compute_dms_diff(today_dms, prior),
                }
            else:
                result[label] = None
        else:
            result[label] = None
    return result


def compute_regime_timeline() -> List[dict]:
    """Build a timeline of regime states over the last 60 trading days."""
    dates = _trading_days_back(60)
    timeline: List[dict] = []
    for d in reversed(dates):
        dms = _load_dms(d)
        if dms:
            regime = dms.get("regime") or {}
            state = regime.get("state", "Unknown") if isinstance(regime, dict) else str(regime)
            timeline.append({"date": d, "regime": state})
    return timeline


def compute_flow_trajectory() -> List[dict]:
    """Flow pressure scores over the last 20 trading days."""
    dates = _trading_days_back(20)
    trajectory: List[dict] = []
    for d in reversed(dates):
        dms = _load_dms(d)
        if dms:
            fp = dms.get("flow_pressure") or {}
            score = fp.get("score") if isinstance(fp, dict) else None
            trajectory.append({"date": d, "score": score})
    return trajectory


# ── Historical Echo Finder ───────────────────────────────────────────────

def _regime_match(a: dict, b: dict) -> bool:
    ra = (a.get("regime") or {})
    rb = (b.get("regime") or {})
    return (ra.get("state") if isinstance(ra, dict) else str(ra)) == \
           (rb.get("state") if isinstance(rb, dict) else str(rb))


def _vol_match(a: dict, b: dict) -> bool:
    va = (a.get("vol_state") or {})
    vb = (b.get("vol_state") or {})
    return (va.get("term_structure") if isinstance(va, dict) else None) == \
           (vb.get("term_structure") if isinstance(vb, dict) else None)


def _flow_direction_match(a: dict, b: dict) -> bool:
    fa = (a.get("flow_pressure") or {})
    fb = (b.get("flow_pressure") or {})
    label_a = fa.get("state") if isinstance(fa, dict) else None
    label_b = fb.get("state") if isinstance(fb, dict) else None
    return label_a == label_b


def find_historical_echoes(current_dms: dict, max_echoes: int = 5) -> List[dict]:
    """Find past dates where regime + vol + flow all matched current state."""
    dates = _trading_days_back(120)
    echoes: List[dict] = []
    for d in dates[5:]:  # skip most recent 5 days
        prior = _load_dms(d)
        if prior is None:
            continue
        match_count = sum([
            _regime_match(current_dms, prior),
            _vol_match(current_dms, prior),
            _flow_direction_match(current_dms, prior),
        ])
        if match_count >= 2:
            echoes.append({
                "date": d,
                "match_score": match_count,
                "regime": _extract(prior, "regime", "state"),
                "vol_structure": _extract(prior, "vol_state", "term_structure"),
                "flow_state": _extract(prior, "flow_pressure", "state"),
            })
        if len(echoes) >= max_echoes:
            break
    return echoes


# ── Theme Convergence Detector ───────────────────────────────────────────

def detect_theme_convergence(
    queue: List[dict],
    news_themes: List[dict],
    dms: Optional[dict] = None,
) -> List[dict]:
    """Cross-reference engine signals with news theme clusters."""
    if not news_themes:
        return []

    theme_sectors: Dict[str, List[str]] = {}
    for t in news_themes:
        name = t.get("theme", "")
        sectors = t.get("affected_sectors") or []
        intensity = t.get("intensity", 0)
        if intensity > 30:
            theme_sectors[name] = sectors

    convergences: List[dict] = []
    for idea in queue:
        ticker = idea.get("ticker", "")
        sector = idea.get("sector", "")
        engine = idea.get("engine_source") or idea.get("engine", "")
        direction = idea.get("direction", "")

        for theme_name, sectors in theme_sectors.items():
            sector_match = any(
                s.lower() in sector.lower() or sector.lower() in s.lower()
                for s in sectors
            ) if sector and sectors else False

            if sector_match:
                convergences.append({
                    "ticker": ticker,
                    "engine": engine,
                    "direction": direction,
                    "theme": theme_name,
                    "matched_sector": sector,
                    "theme_intensity": next(
                        (t.get("intensity", 0) for t in news_themes if t.get("theme") == theme_name), 0
                    ),
                })

    return convergences


# ── Asymmetry Scanner ────────────────────────────────────────────────────

def scan_asymmetric_opportunities(
    queue: List[dict],
    positions: List[dict],
    dms: Optional[dict] = None,
) -> List[dict]:
    """Identify outsized risk/reward setups from the idea queue."""
    opportunities: List[dict] = []

    ticker_engines: Dict[str, List[str]] = {}
    for idea in queue:
        t = idea.get("ticker", "")
        e = idea.get("engine_source") or idea.get("engine", "")
        ticker_engines.setdefault(t, []).append(e)

    multi_engine_tickers = {t for t, engines in ticker_engines.items() if len(set(engines)) >= 2}

    for idea in queue:
        ticker = idea.get("ticker", "")
        ups = idea.get("ups_score") or idea.get("final_ups", 0)
        if not isinstance(ups, (int, float)):
            ups = 0
        raw = idea.get("raw_engine_score") or idea.get("raw_score") or idea.get("score", 0)
        if not isinstance(raw, (int, float)):
            raw = 0

        flags: List[str] = []
        if ticker in multi_engine_tickers:
            flags.append("multi_engine_convergence")
        if ups >= 70:
            flags.append("high_ups")
        if raw >= 85:
            flags.append("high_raw_score")

        if flags:
            opportunities.append({
                "ticker": ticker,
                "engine": idea.get("engine_source") or idea.get("engine", ""),
                "direction": idea.get("direction", ""),
                "ups": ups,
                "raw_score": raw,
                "flags": flags,
                "conviction": "high" if len(flags) >= 2 else "moderate",
            })

    seen: set = set()
    deduped: List[dict] = []
    for opp in sorted(opportunities, key=lambda x: x.get("ups", 0), reverse=True):
        key = (opp["ticker"], opp["direction"])
        if key not in seen:
            seen.add(key)
            deduped.append(opp)

    return deduped[:10]


# ── Action Item Generator ────────────────────────────────────────────────

def build_action_items(
    positions: List[dict],
    queue: List[dict],
    risk: Optional[dict] = None,
    dms: Optional[dict] = None,
) -> List[dict]:
    """Deterministic action item generation (pre-LLM)."""
    items: List[dict] = []

    for pos in positions:
        state = (pos.get("position_state") or "").upper()
        ticker = pos.get("ticker", "?")
        action = pos.get("suggested_action", "")
        reason = pos.get("state_reason", "")

        if state == "INVALIDATED":
            items.append({
                "priority": "red",
                "type": "position",
                "ticker": ticker,
                "headline": f"{ticker} — EXIT recommended. {reason}",
                "detail": f"Position state: {state}. Suggested action: {action}. {reason}",
            })
        elif state == "THESIS_WEAKENING":
            items.append({
                "priority": "red",
                "type": "position",
                "ticker": ticker,
                "headline": f"{ticker} — Thesis weakening. Review immediately.",
                "detail": f"{reason}",
            })
        elif state == "NEAR_TARGET":
            items.append({
                "priority": "amber",
                "type": "position",
                "ticker": ticker,
                "headline": f"{ticker} — Near profit target. Consider taking profits.",
                "detail": f"{reason}",
            })
        elif state == "RISK_INCREASING":
            items.append({
                "priority": "amber",
                "type": "position",
                "ticker": ticker,
                "headline": f"{ticker} — Risk increasing. Review stop/size.",
                "detail": f"{reason}",
            })

    top_ideas = sorted(queue, key=lambda x: x.get("ups_score") or x.get("final_ups", 0), reverse=True)[:5]
    for idea in top_ideas:
        ticker = idea.get("ticker", "?")
        ups = idea.get("ups_score") or idea.get("final_ups", 0)
        engine = idea.get("engine_source") or idea.get("engine", "")
        direction = idea.get("direction", "")
        items.append({
            "priority": "green",
            "type": "opportunity",
            "ticker": ticker,
            "headline": f"{ticker} — {engine} {direction} signal (UPS {ups:.0f})",
            "detail": f"Top-ranked idea from {engine}. Direction: {direction}.",
        })

    priority_order = {"red": 0, "amber": 1, "green": 2}
    items.sort(key=lambda x: priority_order.get(x.get("priority", "green"), 3))

    return items


# ── Master Context Assembler ─────────────────────────────────────────────

def gather_intelligence_context(
    *,
    dms: Optional[dict] = None,
    positions: Optional[List[dict]] = None,
    queue: Optional[List[dict]] = None,
    risk_dashboard: Optional[dict] = None,
    performance: Optional[dict] = None,
    e9_data: Optional[dict] = None,
    news_themes: Optional[List[dict]] = None,
    macro_events: Optional[List[dict]] = None,
    sequencer: Optional[dict] = None,
) -> dict:
    """Assemble all data sources into a single context dict for LLM consumption."""

    ctx: Dict[str, Any] = {
        "generated_at": dt.datetime.now().isoformat(),
        "date": dt.date.today().isoformat(),
    }

    # Section 1: Current market state
    if dms:
        ctx["regime"] = dms.get("regime")
        ctx["flow_pressure"] = dms.get("flow_pressure")
        ctx["vol_state"] = dms.get("vol_state")
        ctx["engine_gates"] = dms.get("engine_gates")
        ctx["news_risk"] = dms.get("news_risk")
        ctx["cross_asset_stress"] = dms.get("cross_asset_stress")
        ctx["earnings_candidates"] = dms.get("earnings_candidates", [])[:10]
        ctx["asymmetry_signals"] = dms.get("asymmetry_signals", [])[:5]
        ctx["post_event_extensions"] = dms.get("post_event_extensions", [])[:5]
    else:
        ctx["regime"] = {"state": "Unknown"}
        ctx["flow_pressure"] = {"score": 50, "state": "Neutral"}
        ctx["vol_state"] = {}
        ctx["engine_gates"] = {}

    # Section 2: Evolution / diffs
    if dms:
        try:
            ctx["dms_evolution"] = build_dms_evolution(dms)
        except Exception as exc:
            LOG.warning("DMS evolution failed: %s", exc)
            ctx["dms_evolution"] = None

        try:
            ctx["regime_timeline"] = compute_regime_timeline()
        except Exception:
            ctx["regime_timeline"] = []

        try:
            ctx["flow_trajectory"] = compute_flow_trajectory()
        except Exception:
            ctx["flow_trajectory"] = []
    else:
        ctx["dms_evolution"] = None
        ctx["regime_timeline"] = []
        ctx["flow_trajectory"] = []

    # Section 3: Risk radar
    ctx["e9_credit_stress"] = e9_data
    ctx["macro_events_upcoming"] = macro_events or []
    ctx["news_themes"] = news_themes or []
    ctx["risk_dashboard"] = risk_dashboard

    # Section 4: Opportunities
    ctx["queue_top"] = sorted(
        queue or [],
        key=lambda x: x.get("ups_score") or x.get("final_ups", 0),
        reverse=True,
    )[:15]

    ctx["theme_convergence"] = detect_theme_convergence(
        queue or [], news_themes or [], dms,
    )
    ctx["asymmetric_opportunities"] = scan_asymmetric_opportunities(
        queue or [], positions or [], dms,
    )

    # Section 5: Position intelligence
    ctx["active_positions"] = positions or []
    pos_summary: Dict[str, int] = {}
    for p in (positions or []):
        st = (p.get("position_state") or "UNKNOWN").upper()
        pos_summary[st] = pos_summary.get(st, 0) + 1
    ctx["positions_summary"] = pos_summary

    # Section 6: Historical echoes
    if dms:
        try:
            ctx["historical_echoes"] = find_historical_echoes(dms)
        except Exception:
            ctx["historical_echoes"] = []
    else:
        ctx["historical_echoes"] = []

    # Section 7: Action items (deterministic)
    ctx["action_items"] = build_action_items(
        positions or [], queue or [], risk_dashboard, dms,
    )

    # Extra context
    ctx["sequencer"] = sequencer
    ctx["performance"] = performance

    return ctx
