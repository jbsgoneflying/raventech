"""Raven-Tech Front Layer – LLM Pipeline (Read-Only).

Generates Morning Brief and Weekly Roadmap from DailyMarketState.
Also includes deterministic Asymmetry Radar detection.

Hard Rules:
  - LLM never sees raw prices or P&L
  - LLM never outputs trades
  - LLM must cite which fields informed each statement
  - All outputs timestamped with source attribution
  - Fallback mode if LLM is unavailable
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Rate limiter (separate budget from desk brief)
# ---------------------------------------------------------------------------


class _FrontLayerRateLimiter:
    """Token-bucket rate limiter for Front Layer LLM calls."""

    def __init__(self, max_calls_per_minute: int = 4):
        self._lock = threading.Lock()
        self._max = max_calls_per_minute
        self._timestamps: List[float] = []

    def acquire(self) -> bool:
        with self._lock:
            now = time.time()
            self._timestamps = [t for t in self._timestamps if now - t < 60.0]
            if len(self._timestamps) >= self._max:
                return False
            self._timestamps.append(now)
            return True


_rate_limiter = _FrontLayerRateLimiter()


# ---------------------------------------------------------------------------
# OpenAI client (reuse pattern from llm_client.py)
# ---------------------------------------------------------------------------


def _get_openai_client():
    """Lazy-load OpenAI client. Returns None if not available."""
    try:
        import openai
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            return None
        return openai.OpenAI(api_key=api_key)
    except ImportError:
        LOG.warning("openai package not installed; Front Layer LLM disabled")
        return None
    except Exception as e:
        LOG.warning("Failed to create OpenAI client: %s", e)
        return None


def _load_prompt(name: str) -> str:
    """Load a prompt template from backend/prompts/."""
    prompt_dir = Path(__file__).parent / "prompts"
    path = prompt_dir / name
    if path.exists():
        return path.read_text(encoding="utf-8").strip()
    return ""


def _parse_llm_json(content: str) -> Optional[dict]:
    """Parse LLM response, handling markdown code blocks."""
    content = content.strip()
    if content.startswith("```"):
        lines = content.split("\n")
        content = "\n".join(lines[1:-1])
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        LOG.warning("LLM returned invalid JSON")
        return None


# ---------------------------------------------------------------------------
# Morning Brief
# ---------------------------------------------------------------------------

_MORNING_BRIEF_FALLBACK: Dict[str, Any] = {
    "market_posture": "Market data is being processed. Review DailyMarketState cards directly.",
    "changes_vs_yesterday": "Diff data unavailable. Check regime and flow pressure cards.",
    "active_themes": "Theme scoring in progress. See Active Themes panel.",
    "cross_asset_signals": "Cross-asset data loading. Check stress grid.",
    "engine_alignment": "Engine gate status available in the engine gates panel.",
    "watch_list": "None",
    "stand_down": "Review regime state for stand-down guidance.",
    "_source": "fallback",
}

_MORNING_BRIEF_REQUIRED_KEYS = {
    "market_posture", "changes_vs_yesterday", "active_themes",
    "cross_asset_signals", "engine_alignment", "watch_list", "stand_down",
}


def _fallback_brief(reason: str) -> Dict[str, Any]:
    """Return morning brief fallback with reason attached."""
    fb = dict(_MORNING_BRIEF_FALLBACK)
    fb["_fallback_reason"] = reason
    return _add_timestamp(fb)


def generate_morning_brief(
    dms_today: dict,
    dms_history: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Generate the Pre-Open Morning Brief from DailyMarketState.

    Args:
        dms_today: Today's DailyMarketState dict.
        dms_history: Rolling prior DailyMarketState dicts (newest first).

    Returns:
        Dict with morning brief sections. Includes _generated_at timestamp.
    """
    if not _rate_limiter.acquire():
        LOG.info("Morning brief rate-limited; returning fallback")
        return _fallback_brief("Rate limited (max 4 calls/minute)")

    client = _get_openai_client()
    if client is None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            reason = "OPENAI_API_KEY not set in environment"
        else:
            reason = "OpenAI client failed to initialize (check openai package installation)"
        LOG.warning("Morning brief: %s", reason)
        return _fallback_brief(reason)

    system_prompt = _load_prompt("morning_brief.txt")
    if not system_prompt:
        reason = "Prompt file backend/prompts/morning_brief.txt not found"
        LOG.warning(reason)
        return _fallback_brief(reason)

    # Build context payload
    context = {
        "today": _sanitize_dms(dms_today),
    }
    if dms_history:
        context["prior_days"] = [_sanitize_dms(d) for d in dms_history[:5]]

    payload_str = json.dumps(context, default=str)
    # Truncate to fit token budget (~4000 tokens)
    if len(payload_str) > 12000:
        payload_str = payload_str[:12000] + "..."

    model = os.getenv("LLM_MODEL_NARRATIVE", "gpt-4o-mini").strip()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload_str},
            ],
            temperature=0.2,
            max_tokens=800,
            timeout=15,
        )

        content = response.choices[0].message.content.strip()
        result = _parse_llm_json(content)

        if result is None or not _MORNING_BRIEF_REQUIRED_KEYS.issubset(set(result.keys())):
            LOG.warning("Morning brief LLM response missing required keys; got: %s",
                        list(result.keys()) if result else "None")
            return _fallback_brief("LLM returned invalid/incomplete JSON (model: " + model + ")")

        # Sanitize output lengths
        brief = {}
        for key in _MORNING_BRIEF_REQUIRED_KEYS:
            val = result.get(key, "")
            if isinstance(val, list):
                brief[key] = val
            else:
                brief[key] = str(val)[:500]

        brief["_source"] = "llm"
        return _add_timestamp(brief)

    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        LOG.warning("Morning brief LLM call failed: %s", reason)
        return _fallback_brief(reason)


# ---------------------------------------------------------------------------
# Weekly Roadmap
# ---------------------------------------------------------------------------

_WEEKLY_ROADMAP_FALLBACK: Dict[str, Any] = {
    "regime_flow_summary": "Weekly analysis pending. Review regime and flow pressure trend.",
    "expected_pattern": "Pattern detection in progress. Check sequencer panel.",
    "high_risk_days": [],
    "engine_behaviors": "Engine gate summary available in Command Center.",
    "earnings_focus": [],
    "asymmetry_radar": "No asymmetries detected.",
    "break_the_plan": "Check regime transition triggers for invalidation conditions.",
    "_source": "fallback",
}

_WEEKLY_ROADMAP_REQUIRED_KEYS = {
    "regime_flow_summary", "expected_pattern", "high_risk_days",
    "engine_behaviors", "earnings_focus", "asymmetry_radar", "break_the_plan",
}


def _fallback_roadmap(reason: str) -> Dict[str, Any]:
    """Return weekly roadmap fallback with reason attached."""
    fb = dict(_WEEKLY_ROADMAP_FALLBACK)
    fb["_fallback_reason"] = reason
    return _add_timestamp(fb)


def generate_weekly_roadmap(
    dms_today: dict,
    dms_history: Optional[List[dict]] = None,
) -> Dict[str, Any]:
    """Generate the Sunday Night Weekly Roadmap from DailyMarketState.

    Args:
        dms_today: Today's DailyMarketState dict.
        dms_history: Rolling prior week DailyMarketState dicts (newest first).

    Returns:
        Dict with weekly roadmap sections. Includes _generated_at timestamp.
    """
    if not _rate_limiter.acquire():
        LOG.info("Weekly roadmap rate-limited; returning fallback")
        return _fallback_roadmap("Rate limited (max 4 calls/minute)")

    client = _get_openai_client()
    if client is None:
        api_key = os.getenv("OPENAI_API_KEY", "").strip()
        if not api_key:
            reason = "OPENAI_API_KEY not set in environment"
        else:
            reason = "OpenAI client failed to initialize (check openai package installation)"
        LOG.warning("Weekly roadmap: %s", reason)
        return _fallback_roadmap(reason)

    system_prompt = _load_prompt("weekly_roadmap.txt")
    if not system_prompt:
        reason = "Prompt file backend/prompts/weekly_roadmap.txt not found"
        LOG.warning(reason)
        return _fallback_roadmap(reason)

    context = {
        "today": _sanitize_dms(dms_today),
    }
    if dms_history:
        context["prior_days"] = [_sanitize_dms(d) for d in dms_history[:7]]

    payload_str = json.dumps(context, default=str)
    if len(payload_str) > 15000:
        payload_str = payload_str[:15000] + "..."

    model = os.getenv("LLM_MODEL_NARRATIVE", "gpt-4o-mini").strip()

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": payload_str},
            ],
            temperature=0.2,
            max_tokens=1000,
            timeout=20,
        )

        content = response.choices[0].message.content.strip()
        result = _parse_llm_json(content)

        if result is None or not _WEEKLY_ROADMAP_REQUIRED_KEYS.issubset(set(result.keys())):
            LOG.warning("Weekly roadmap LLM response missing required keys; got: %s",
                        list(result.keys()) if result else "None")
            return _fallback_roadmap("LLM returned invalid/incomplete JSON (model: " + model + ")")

        roadmap: Dict[str, Any] = {}
        for key in _WEEKLY_ROADMAP_REQUIRED_KEYS:
            val = result.get(key, "")
            if isinstance(val, list):
                roadmap[key] = val
            else:
                roadmap[key] = str(val)[:500]

        # Enforce max 2 earnings focus
        if isinstance(roadmap.get("earnings_focus"), list):
            roadmap["earnings_focus"] = roadmap["earnings_focus"][:2]

        roadmap["_source"] = "llm"
        return _add_timestamp(roadmap)

    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        LOG.warning("Weekly roadmap LLM call failed: %s", reason)
        return _fallback_roadmap(reason)


# ---------------------------------------------------------------------------
# Asymmetry Radar (deterministic – NOT LLM)
# ---------------------------------------------------------------------------


def detect_asymmetries(
    dms_today: dict,
    dms_history: Optional[List[dict]] = None,
) -> List[Dict[str, Any]]:
    """Detect rare high-impact asymmetric conditions.

    Pure deterministic logic – no LLM involved.
    Each alert tagged: "Monitor only / Await confirmation / No action yet"

    Conditions checked:
      1. Vol underpricing vs narrative acceleration
      2. FX stress without equity reaction
      3. Commodity spike with muted index response
      4. Regime-flow divergence
      5. Theme persistence without vol reaction
    """
    signals: List[Dict[str, Any]] = []

    if not dms_today:
        return signals

    regime = dms_today.get("regime", {})
    flow = dms_today.get("flow_pressure", {})
    vol = dms_today.get("vol_state", {})
    xstress = dms_today.get("cross_asset_stress", {})
    themes = dms_today.get("news_themes", [])
    regime_score = float(regime.get("score", 50))
    fp_score = float(flow.get("score", 50))
    vol_level = float(vol.get("level", 0))
    vol_skew = str(vol.get("skew", "neutral"))

    xstress_score = float(xstress.get("composite_score", 50))
    xstress_readings = xstress.get("readings", [])

    # --- 1. Vol underpricing vs narrative acceleration ---
    high_intensity_themes = [
        t for t in themes
        if float(t.get("intensity", 0)) > 60
        and str(t.get("acceleration", "")) == "rising"
    ]
    if high_intensity_themes and vol_skew != "elevated" and vol_level < 25:
        signals.append({
            "type": "vol_underpricing_vs_narrative",
            "description": (
                f"Narrative themes accelerating ({len(high_intensity_themes)} themes rising) "
                f"but vol skew is {vol_skew} and VIX-level proxy is {vol_level:.0f}. "
                "Vol may be underpricing tail risk."
            ),
            "severity": "elevated",
            "action": "Monitor only. Await confirmation from vol term structure.",
            "sources": ["news_themes", "vol_state.skew", "vol_state.level"],
        })

    # --- 2. FX stress without equity reaction ---
    fx_readings = [r for r in xstress_readings if r.get("asset_class") == "fx"]
    fx_stress_avg = 0.0
    if fx_readings:
        fx_stress_avg = sum(float(r.get("stress_score", 50)) for r in fx_readings) / len(fx_readings)

    if fx_stress_avg > 65 and fp_score > 45:
        signals.append({
            "type": "fx_stress_no_equity_reaction",
            "description": (
                f"FX stress elevated ({fx_stress_avg:.0f}) but flow pressure remains "
                f"neutral-to-positive ({fp_score:.0f}). Equities may be ignoring "
                "funding currency stress."
            ),
            "severity": "watch",
            "action": "Monitor only. FX stress may lead equities by 1-2 sessions.",
            "sources": ["cross_asset_stress.fx", "flow_pressure.score"],
        })

    # --- 3. Commodity spike with muted index response ---
    commodity_readings = [r for r in xstress_readings if r.get("asset_class") == "commodity"]
    commodity_stress_avg = 0.0
    if commodity_readings:
        commodity_stress_avg = sum(
            float(r.get("stress_score", 50)) for r in commodity_readings
        ) / len(commodity_readings)

    if commodity_stress_avg > 65 and regime_score < 50:
        signals.append({
            "type": "commodity_spike_muted_index",
            "description": (
                f"Commodity stress elevated ({commodity_stress_avg:.0f}) but regime score "
                f"remains moderate ({regime_score:.0f}). Supply-side or geopolitical "
                "risk may not yet be reflected in equities."
            ),
            "severity": "watch",
            "action": "Await confirmation. No action yet.",
            "sources": ["cross_asset_stress.commodity", "regime.score"],
        })

    # --- 4. Regime-flow divergence ---
    regime_label = str(regime.get("state", ""))
    fp_label = str(flow.get("state", ""))

    if regime_label in ("Risk-Off", "Stressed") and fp_label == "Risk-On":
        signals.append({
            "type": "regime_flow_divergence",
            "description": (
                f"Regime is {regime_label} (score {regime_score:.0f}) but flow pressure "
                f"reads {fp_label} ({fp_score:.0f}). Internal divergence may resolve "
                "sharply in either direction."
            ),
            "severity": "elevated",
            "action": "Monitor only. Divergence typically resolves within 1-3 sessions.",
            "sources": ["regime.state", "flow_pressure.state"],
        })

    # --- 5. Theme persistence without vol reaction ---
    persistent_themes = [
        t for t in themes
        if int(t.get("persistence_days", 0)) >= 5
        and float(t.get("intensity", 0)) > 40
    ]
    if persistent_themes and vol_skew == "low":
        signals.append({
            "type": "persistent_theme_no_vol",
            "description": (
                f"{len(persistent_themes)} theme(s) have been active for 5+ days "
                f"but vol skew is low. Market may be complacent."
            ),
            "severity": "watch",
            "action": "Monitor only. Await confirmation from vol term structure.",
            "sources": ["news_themes.persistence_days", "vol_state.skew"],
        })

    return signals


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sanitize_dms(dms: dict) -> dict:
    """Remove any fields that should not reach the LLM (raw prices, P&L, etc).

    The DailyMarketState should already be clean, but this is a defense-in-depth check.
    """
    if not isinstance(dms, dict):
        return {}

    # Whitelist of allowed top-level fields
    allowed = {
        "date", "generated_at", "regime", "flow_pressure", "vol_state",
        "engine_gates", "earnings_candidates", "index_state", "news_risk",
        "cross_asset_stress", "news_themes", "sequencer_summary",
        "asymmetry_signals",
    }
    sanitized = {k: v for k, v in dms.items() if k in allowed}

    # Strip any raw_price or pnl fields that might leak through
    return _recursive_strip(sanitized, {"raw_price", "price", "pnl", "profit", "loss", "close", "open", "high", "low"})


def _recursive_strip(obj: Any, forbidden_keys: set) -> Any:
    """Recursively remove forbidden keys from nested dicts."""
    if isinstance(obj, dict):
        return {
            k: _recursive_strip(v, forbidden_keys)
            for k, v in obj.items()
            if k.lower() not in forbidden_keys
        }
    elif isinstance(obj, list):
        return [_recursive_strip(item, forbidden_keys) for item in obj]
    return obj


def _add_timestamp(result: dict) -> dict:
    """Add generation timestamp to LLM output."""
    result = dict(result)
    result["_generated_at"] = dt.datetime.now(dt.timezone.utc).isoformat().replace("+00:00", "Z")
    return result
