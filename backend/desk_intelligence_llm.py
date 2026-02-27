"""Desk Intelligence LLM — GPT-powered morning brief synthesis.

Takes the full context dict from desk_intelligence.py and produces a
structured 7-section intelligence brief via the OpenAI API.

Includes caching (Redis, 4-hour TTL) and a deterministic fallback
when the LLM is unavailable.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import textwrap
import time
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)

_CACHE_KEY = "rtv2:intelligence:brief"
_CACHE_TTL = 4 * 3600  # 4 hours
_HISTORY_KEY = "rtv2:intelligence:history"
_HISTORY_TTL = 7 * 86400  # 7 days


# ── OpenAI Client ────────────────────────────────────────────────────────

def _get_client():
    try:
        import openai
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return openai.OpenAI(api_key=api_key)
    except Exception:
        return None


def _model() -> str:
    return os.getenv("INTELLIGENCE_MODEL", os.getenv("LLM_MODEL_DISCOVERY", "gpt-4o"))


# ── Prompt Builder ───────────────────────────────────────────────────────

def _build_prompt(ctx: dict) -> str:
    """Build the full system + user prompt for the intelligence brief."""

    regime = ctx.get("regime") or {}
    regime_state = regime.get("state", "Unknown") if isinstance(regime, dict) else str(regime)
    regime_drivers = regime.get("drivers", []) if isinstance(regime, dict) else []

    fp = ctx.get("flow_pressure") or {}
    fp_score = fp.get("score", "?") if isinstance(fp, dict) else fp
    fp_state = fp.get("state", "?") if isinstance(fp, dict) else ""

    vol = ctx.get("vol_state") or {}
    vol_level = vol.get("level", "?") if isinstance(vol, dict) else "?"
    vol_ts = vol.get("term_structure", "?") if isinstance(vol, dict) else "?"
    vol_skew = vol.get("skew", "?") if isinstance(vol, dict) else "?"

    gates = ctx.get("engine_gates") or {}
    gates_str = ", ".join(f"{k}: {v}" for k, v in gates.items()) if isinstance(gates, dict) else str(gates)

    evolution = ctx.get("dms_evolution") or {}
    diffs_1d = evolution.get("vs_1d", {})
    diffs_5d = evolution.get("vs_5d", {})
    diffs_20d = evolution.get("vs_20d", {})

    def _fmt_diffs(d):
        if not d or not isinstance(d, dict):
            return "No data"
        changes = d.get("changes", [])
        if not changes:
            return "No changes"
        return "; ".join(f"{c['field']}: {c['from']} → {c['to']}" for c in changes)

    positions = ctx.get("active_positions") or []
    pos_lines: List[str] = []
    for p in positions[:15]:
        ticker = p.get("ticker", "?")
        state = p.get("position_state", "?")
        action = p.get("suggested_action", "?")
        pnl = p.get("current_pnl_pct", 0)
        days = p.get("days_in_trade", 0)
        reason = p.get("state_reason", "")
        entry = p.get("entry_price", "?")
        stop = p.get("thesis_stop", "?")
        target = p.get("thesis_target", "?")
        direction = p.get("direction", "?")
        engine = p.get("engine_source", "?")
        pos_lines.append(
            f"  {ticker} ({engine}, {direction}) entry={entry} stop={stop} target={target} "
            f"P&L={pnl:.1%} days={days} state={state} action={action} — {reason}"
        )
    positions_block = "\n".join(pos_lines) if pos_lines else "  No active positions."

    queue = ctx.get("queue_top") or []
    queue_lines: List[str] = []
    for q in queue[:10]:
        ticker = q.get("ticker", "?")
        engine = q.get("engine_source") or q.get("engine", "?")
        ups = q.get("ups_score") or q.get("final_ups", 0)
        raw = q.get("raw_engine_score") or q.get("raw_score") or q.get("score", 0)
        direction = q.get("direction", "?")
        bucket = q.get("bucket", "?")
        queue_lines.append(f"  {ticker} ({engine}, {direction}) UPS={ups:.0f} raw={raw:.0f} bucket={bucket}")
    queue_block = "\n".join(queue_lines) if queue_lines else "  No ideas in queue."

    e9 = ctx.get("e9_credit_stress") or {}
    e9_composite = e9.get("composite", "N/A")
    e9_phase = e9.get("phase_label", "N/A")

    themes = ctx.get("news_themes") or []
    themes_block = "\n".join(
        f"  {t.get('theme', '?')}: intensity={t.get('intensity', 0)}, acceleration={t.get('acceleration', '?')}"
        for t in themes[:6]
    ) if themes else "  No active news themes."

    macro = ctx.get("macro_events_upcoming") or []
    macro_block = "\n".join(
        f"  {e.get('date', '?')}: {e.get('title', e.get('short', '?'))} (importance={e.get('importance', '?')})"
        for e in macro[:8]
    ) if macro else "  No upcoming macro events."

    echoes = ctx.get("historical_echoes") or []
    echoes_block = "\n".join(
        f"  {e.get('date', '?')}: regime={e.get('regime', '?')}, vol={e.get('vol_structure', '?')}, flow={e.get('flow_state', '?')} (match={e.get('match_score', 0)}/3)"
        for e in echoes
    ) if echoes else "  No historical echoes found in last 120 days."

    convergences = ctx.get("theme_convergence") or []
    conv_block = "\n".join(
        f"  {c.get('ticker', '?')} ({c.get('engine', '?')}, {c.get('direction', '?')}) aligns with theme '{c.get('theme', '?')}' (intensity={c.get('theme_intensity', 0)})"
        for c in convergences[:8]
    ) if convergences else "  No theme-signal convergences detected."

    asymmetric = ctx.get("asymmetric_opportunities") or []
    asym_block = "\n".join(
        f"  {a.get('ticker', '?')} ({a.get('engine', '?')}, {a.get('direction', '?')}) UPS={a.get('ups', 0):.0f} flags={a.get('flags', [])} conviction={a.get('conviction', '?')}"
        for a in asymmetric[:8]
    ) if asymmetric else "  No asymmetric opportunities detected."

    risk = ctx.get("risk_dashboard") or {}
    risk_lines = []
    if isinstance(risk, dict):
        risk_lines.append(f"  Portfolio RU: {risk.get('total_ru', 0):.1f} / {risk.get('portfolio_ru_cap', 15)}")
        risk_lines.append(f"  Directional tilt: {risk.get('directional_tilt', '?')}")
        warnings = risk.get("sector_warnings") or []
        if warnings:
            risk_lines.append(f"  Sector warnings: {', '.join(warnings)}")
        if risk.get("drawdown_warning"):
            risk_lines.append(f"  Drawdown warning: {risk.get('weekly_drawdown_pct', 0):.2%}")
    risk_block = "\n".join(risk_lines) if risk_lines else "  No risk data available."

    pos_summary = ctx.get("positions_summary") or {}
    summary_str = ", ".join(f"{k}: {v}" for k, v in pos_summary.items()) if pos_summary else "No positions"

    sequencer = ctx.get("sequencer") or {}
    seq_pattern = ""
    if isinstance(sequencer, dict):
        seq = sequencer.get("sequence", {})
        if isinstance(seq, dict):
            mp = seq.get("matched_pattern", {})
            if isinstance(mp, dict):
                seq_pattern = f"{mp.get('label', '?')} ({mp.get('confidence', '?')}% confidence). Favored: {mp.get('favored', [])}"

    flow_traj = ctx.get("flow_trajectory") or []
    traj_str = ", ".join(f"{t.get('date','?')}:{t.get('score','?')}" for t in flow_traj[-5:]) if flow_traj else "N/A"

    regime_tl = ctx.get("regime_timeline") or []
    regime_recent = ", ".join(f"{t.get('date','?')}:{t.get('regime','?')}" for t in regime_tl[-10:]) if regime_tl else "N/A"

    user_prompt = textwrap.dedent(f"""\
    === TODAY'S MARKET STATE ({ctx.get('date', 'unknown')}) ===

    REGIME: {regime_state}
    Drivers: {regime_drivers}
    FLOW PRESSURE: {fp_score} ({fp_state})
    VOLATILITY: level={vol_level}, term_structure={vol_ts}, skew={vol_skew}
    ENGINE GATES: {gates_str}

    === WHAT CHANGED ===
    vs Yesterday: {_fmt_diffs(diffs_1d)}
    vs 5 days ago: {_fmt_diffs(diffs_5d)}
    vs 20 days ago: {_fmt_diffs(diffs_20d)}
    Recent regime history (last 10 sessions): {regime_recent}
    Recent flow trajectory (last 5 sessions): {traj_str}

    === CREDIT STRESS (Engine 9) ===
    Composite: {e9_composite}, Phase: {e9_phase}

    === NEWS THEMES ===
    {themes_block}

    === MACRO CALENDAR (next 5 sessions) ===
    {macro_block}

    === RISK DASHBOARD ===
    {risk_block}

    === ACTIVE POSITIONS ({len(positions)} total: {summary_str}) ===
    {positions_block}

    === TOP IDEAS IN QUEUE ===
    {queue_block}

    === THEME-SIGNAL CONVERGENCES ===
    {conv_block}

    === ASYMMETRIC OPPORTUNITIES ===
    {asym_block}

    === HISTORICAL ECHOES (similar past conditions) ===
    {echoes_block}

    === WEEKLY SEQUENCER ===
    {seq_pattern or 'No pattern matched.'}
    """)

    return user_prompt


SYSTEM_PROMPT = textwrap.dedent("""\
You are the Chief Investment Strategist for Raven Tech, a family office trading desk.
You have access to the complete market state including regime data, flow pressure,
volatility structure, credit stress, news themes, macro calendar, active positions,
and a ranked queue of trade ideas from multiple quantitative engines.

Your job is to produce a structured Morning Intelligence Brief that helps the desk
make better decisions today. Be specific, opinionated, and actionable. Reference
specific tickers, numbers, and dates. Connect dots that humans might miss.

IMPORTANT RULES:
- Be direct and concrete. No generic advice.
- Reference specific data points from the context provided.
- When you see contradictions between data sources, call them out explicitly.
- For opportunities, suggest conviction level: "watch", "scale-in", or "full position".
- For risks, suggest specific responses: "tighten stops", "reduce exposure", "hedge via X".
- Think across timeframes: what matters today, this week, this month.
- The desk trades options (iron condors, spreads), directional equity, and pairs.
  Suggest specific structure types when relevant.

Return a JSON object with exactly these 7 keys. Each value is an object with
"headline" (one sentence), "detail" (2-5 paragraphs, plain text), and
"urgency" (one of: "low", "medium", "high", "critical").

{
  "where_are_we": {"headline": "...", "detail": "...", "urgency": "..."},
  "what_changed": {"headline": "...", "detail": "...", "urgency": "..."},
  "risk_radar": {"headline": "...", "detail": "...", "urgency": "..."},
  "opportunities": {"headline": "...", "detail": "...", "urgency": "..."},
  "book_review": {"headline": "...", "detail": "...", "urgency": "..."},
  "historical_echoes": {"headline": "...", "detail": "...", "urgency": "..."},
  "action_items": {"headline": "...", "detail": "...", "urgency": "..."}
}

Return ONLY valid JSON. No markdown, no commentary outside the JSON.
""")


# ── LLM Call ─────────────────────────────────────────────────────────────

def _parse_json(content: str) -> Optional[dict]:
    """Robust JSON extraction from LLM response."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:])
        if content.rstrip().endswith("```"):
            content = content.rstrip()[:-3]
        content = content.strip()
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        pass
    start = content.find("{")
    if start == -1:
        return None
    depth = 0
    end = start
    for i in range(start, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
                break
    if depth != 0:
        return None
    try:
        return json.loads(content[start:end])
    except json.JSONDecodeError:
        return None


def generate_morning_brief(ctx: dict) -> dict:
    """Call GPT to produce the 7-section morning brief.

    Returns dict with the 7 sections + metadata, or a deterministic
    fallback if the LLM is unavailable.
    """
    client = _get_client()
    if client is None:
        LOG.info("Intelligence brief: LLM unavailable, using deterministic fallback")
        return _deterministic_fallback(ctx)

    user_prompt = _build_prompt(ctx)
    model = _model()

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_prompt},
            ],
            temperature=0.7,
            max_tokens=4096,
            timeout=60,
        )
        content = resp.choices[0].message.content or ""
        parsed = _parse_json(content)
        if parsed is None:
            LOG.warning("Intelligence brief: failed to parse LLM JSON response")
            return _deterministic_fallback(ctx)

        parsed["_meta"] = {
            "model": model,
            "generated_at": dt.datetime.now().isoformat(),
            "llm": True,
            "tokens": getattr(resp.usage, "total_tokens", None) if resp.usage else None,
        }
        return parsed

    except Exception as exc:
        LOG.warning("Intelligence brief LLM call failed: %s", exc)
        return _deterministic_fallback(ctx)


# ── Deterministic Fallback ───────────────────────────────────────────────

def _deterministic_fallback(ctx: dict) -> dict:
    """Build a basic brief from raw data when LLM is unavailable."""
    regime = ctx.get("regime") or {}
    regime_state = regime.get("state", "Unknown") if isinstance(regime, dict) else str(regime)
    fp = ctx.get("flow_pressure") or {}
    fp_score = fp.get("score", 50) if isinstance(fp, dict) else 50
    vol = ctx.get("vol_state") or {}

    pos_summary = ctx.get("positions_summary") or {}
    queue_count = len(ctx.get("queue_top") or [])
    e9 = ctx.get("e9_credit_stress") or {}

    action_items = ctx.get("action_items") or []
    red_count = sum(1 for a in action_items if a.get("priority") == "red")
    amber_count = sum(1 for a in action_items if a.get("priority") == "amber")
    green_count = sum(1 for a in action_items if a.get("priority") == "green")

    actions_detail = "\n".join(
        f"[{a.get('priority', '?').upper()}] {a.get('headline', '')}"
        for a in action_items[:10]
    ) or "No action items."

    return {
        "where_are_we": {
            "headline": f"Regime is {regime_state} with flow pressure at {fp_score}.",
            "detail": f"Volatility: {vol.get('term_structure', '?')} term structure, skew {vol.get('skew', '?')}, level {vol.get('level', '?')}.",
            "urgency": "medium" if regime_state in ("Risk-Off", "Stressed") else "low",
        },
        "what_changed": {
            "headline": "DMS diff analysis available — LLM synthesis unavailable.",
            "detail": str(ctx.get("dms_evolution") or "No evolution data."),
            "urgency": "low",
        },
        "risk_radar": {
            "headline": f"E9 credit stress at {e9.get('composite', 'N/A')}, phase: {e9.get('phase_label', 'N/A')}.",
            "detail": f"Macro events upcoming: {len(ctx.get('macro_events_upcoming') or [])}. News themes active: {len(ctx.get('news_themes') or [])}.",
            "urgency": "high" if (isinstance(e9.get("composite"), (int, float)) and e9["composite"] > 60) else "medium",
        },
        "opportunities": {
            "headline": f"{queue_count} ideas in queue, {len(ctx.get('asymmetric_opportunities') or [])} asymmetric setups detected.",
            "detail": "Run full LLM brief for detailed opportunity analysis.",
            "urgency": "medium" if queue_count > 0 else "low",
        },
        "book_review": {
            "headline": f"Active positions: {sum(pos_summary.values())} ({', '.join(f'{k}: {v}' for k, v in pos_summary.items())}).",
            "detail": f"Red items: {red_count}, Amber items: {amber_count}.",
            "urgency": "high" if red_count > 0 else "medium" if amber_count > 0 else "low",
        },
        "historical_echoes": {
            "headline": f"{len(ctx.get('historical_echoes') or [])} historical echo(es) found.",
            "detail": str(ctx.get("historical_echoes") or "No echoes."),
            "urgency": "low",
        },
        "action_items": {
            "headline": f"{red_count} urgent, {amber_count} review, {green_count} opportunities.",
            "detail": actions_detail,
            "urgency": "critical" if red_count > 0 else "high" if amber_count > 0 else "medium",
        },
        "_meta": {
            "model": "deterministic",
            "generated_at": dt.datetime.now().isoformat(),
            "llm": False,
        },
    }


# ── Caching ──────────────────────────────────────────────────────────────

def _get_store():
    from backend.redis_store import get_store_optional
    return get_store_optional()


def load_cached_brief() -> Optional[dict]:
    store = _get_store()
    if store is None:
        return None
    try:
        raw = store.get(_CACHE_KEY)
        if raw:
            return json.loads(raw)
    except Exception:
        pass
    return None


def cache_brief(brief: dict) -> None:
    store = _get_store()
    if store is None:
        return
    try:
        store.setex(_CACHE_KEY, _CACHE_TTL, json.dumps(brief, default=str))
        # Also add to history
        history_entry = json.dumps({
            "date": dt.date.today().isoformat(),
            "generated_at": brief.get("_meta", {}).get("generated_at", ""),
            "brief": brief,
        }, default=str)
        store.lpush(_HISTORY_KEY, history_entry)
        store.ltrim(_HISTORY_KEY, 0, 13)  # keep last 14 entries (~7 days x 2/day)
        store.expire(_HISTORY_KEY, _HISTORY_TTL)
    except Exception as exc:
        LOG.warning("Failed to cache intelligence brief: %s", exc)


def clear_cache() -> None:
    store = _get_store()
    if store is None:
        return
    try:
        store.delete(_CACHE_KEY)
    except Exception:
        pass


def load_brief_history(limit: int = 7) -> List[dict]:
    store = _get_store()
    if store is None:
        return []
    try:
        raw_list = store.lrange(_HISTORY_KEY, 0, limit - 1)
        return [json.loads(r) for r in raw_list if r]
    except Exception:
        return []
