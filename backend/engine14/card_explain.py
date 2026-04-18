"""Engine 14 — LLM "what is this card" explainer.

Each Engine 14 results card is paired with an authoritative static description
(the "spec"). When the desk clicks the info button on a card, we send both the
spec **and** the live card_data to an LLM and get back a short, structured
desk-friendly explanation:

    {
      "what_this_shows":  "...",
      "how_to_read_it":   "...",
      "how_to_use_it":    "...",
      "watch_for":        "...",
      "desk_takeaway":    "..."
    }

The spec is baked into the system prompt so the LLM can't hallucinate what
e.g. "White Knuckle" means — it's grounded. If the LLM is unavailable, we fall
back to the spec itself as a plain-language explanation.

Rate-limited and TTL-cached per (card_type + data hash) so hovering the same
card after a run is instant.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

from cachetools import TTLCache

from backend.llm_client import _get_openai_client, _parse_desk_brief_json

LOG = logging.getLogger("engine14.card_explain")


# ---------------------------------------------------------------------------
# Fixed output schema — the desk sees the same five sections every time so
# scanning is fast.
# ---------------------------------------------------------------------------

OUTPUT_KEYS: List[str] = [
    "what_this_shows",
    "how_to_read_it",
    "how_to_use_it",
    "watch_for",
    "desk_takeaway",
]

OUTPUT_LABELS: Dict[str, str] = {
    "what_this_shows": "What This Shows",
    "how_to_read_it":  "How To Read It",
    "how_to_use_it":   "How To Use It",
    "watch_for":       "Watch For",
    "desk_takeaway":   "Desk Takeaway",
}


# ---------------------------------------------------------------------------
# Card catalog — authoritative specs for every card/section in Engine 14.
# ---------------------------------------------------------------------------

# Keys MUST stay in sync with the `data-explain="..."` values on the frontend
# dividers. Adding a new card here lights up an info button automatically on
# the corresponding section as long as the HTML uses the matching slug.

CARD_CATALOG: Dict[str, Dict[str, str]] = {

    "entry_state": {
        "title": "Entry State",
        "spec": (
            "The Entry State strip summarizes the **replay context** at the "
            "trade's entry moment.\n"
            "Fields:\n"
            "- Analogues Used / Considered: how many historical IC replays "
            "survived the matcher filter. More = tighter estimate.\n"
            "- Regime Bucket: a label (e.g. low/mid/high RV20 percentile) "
            "classifying today's realized-vol regime; analogues are drawn from "
            "the same bucket.\n"
            "- Spot (Entry): SPX cash price on entry date.\n"
            "- 1σ EM %: the market-implied 1-standard-deviation expected move "
            "to expiry (from the ATM straddle), expressed as a percentage.\n"
            "- Short PUT Dist / Short CALL Dist: distance from entry spot to "
            "each short-wing strike, as a percent of spot, with the distance "
            "also expressed in multiples of the 1σ EM (e.g. 1.25× EM means "
            "the short strike sits 1.25 standard deviations away from spot). "
            "Rule of thumb: <1.00× EM = inside the cone (higher breach risk, "
            "red/amber); ≥1.00× = outside (blue/green). These two numbers let "
            "the desk see at a glance how aggressive each wing is relative to "
            "what the market is pricing in.\n"
            "- Wing Width: the smaller of the put-wing or call-wing distance "
            "in points — the max loss geometry of the condor.\n"
            "- Mean / Median P&L: average and median replay P&L (as a % of the "
            "credit received) across all analogues under the active exit rules "
            "and fill model.\n"
            "- Sharpe (proxy): mean-P&L / std-dev across analogues; a crude "
            "quality score for this structure vs history."
        ),
    },

    "regime_match": {
        "title": "Regime Match Quality",
        "spec": (
            "Shows **how** we picked the historical analogues.\n"
            "- Match Source = KNN: multi-factor nearest-neighbor match over a "
            "feature store (RV20, term structure, skew, dealer-gamma, etc.) "
            "weighted by covariance. Distances are weighted-L2; lower = "
            "closer.\n"
            "- Match Source = RV20 bucket: legacy fallback — only match on the "
            "realized-vol percentile bucket because the feature store was "
            "unavailable that day.\n"
            "- Distance (min / mean / max): spread of neighbor distances. A "
            "wide spread means the analogue pool isn't cohesive.\n"
            "- Feature Imputation: share of feature cells that had to be "
            "median-filled (missing data). High imputation = brittle match.\n"
            "- Admitted: how many analogues came from KNN scoring vs. legacy "
            "bucket fallback — fallback rows are lower-confidence."
        ),
    },

    "outcome_distribution": {
        "title": "Outcome Distribution (NBBO)",
        "spec": (
            "The primary empirical outcome mix across all matched analogue "
            "replays, using the active **fill model** (NBBO close / mid / "
            "mid+penalty) shown in the badge. Five mutually-exclusive "
            "outcomes:\n"
            "- **Early Target**: hit the profit target early (typically 50% "
            "of credit collected) and closed for a win.\n"
            "- **Full Collect**: held to (or near) expiration and kept the "
            "full credit.\n"
            "- **White Knuckle**: survived a meaningful adverse excursion "
            "intraday (touched stop territory) but ultimately closed inside "
            "the short strikes without triggering the stop. Functionally a "
            "win, but a stressful one — path matters.\n"
            "- **Stop Out**: triggered the loss stop at debit ≥ stop_loss_pct "
            "× credit and closed for a defined loss.\n"
            "- **Breach**: the underlying closed **beyond a short strike** at "
            "expiry → assignment/max-loss territory if held.\n"
            "Per-outcome metrics:\n"
            "- pct / n: share and count of analogues in that bucket.\n"
            "- avg P&L: average realized P&L (% of credit) for that bucket.\n"
            "- avg days: average days-held in that bucket.\n"
            "- 90% CI (if shown): bootstrap confidence interval around pct "
            "and P&L — wider bands = thinner sample."
        ),
    },

    "outcome_mid": {
        "title": "Legacy Mid-Fill Distribution",
        "spec": (
            "Same five-outcome mix as the primary distribution, but computed "
            "under a pure **mid-price fill model** (no NBBO, no slippage "
            "penalty). Shown only as a **calibration reference** — it's what "
            "the numbers looked like before we modeled realistic fills. "
            "Expect mid-only to overstate win rate vs NBBO because it doesn't "
            "pay the bid/ask to exit."
        ),
    },

    "outcome_adjusted": {
        "title": "Adjusted Distribution (Phase 2 conditioning)",
        "spec": (
            "The outcome distribution **after** applying the Conditioning "
            "Modifiers (macro calendar density, dealer-gamma regime, "
            "cross-asset stress, gap regime from Engine 13). We multiply the "
            "tail probabilities by the net tail-multiplier and shift win-rate "
            "by the net win-rate shift. This is the distribution to trust "
            "when today's regime diverges from the raw analogue pool's "
            "regime."
        ),
    },

    "modifiers": {
        "title": "Conditioning Modifiers",
        "spec": (
            "Per-factor adjustments applied to the raw empirical distribution "
            "to get the Adjusted Distribution. Each card shows a severity "
            "label (none / low / moderate / elevated / extreme), a **tail "
            "multiplier** (how much to scale the breach+stop probabilities), "
            "a **win-rate shift** (percentage-point add-on to full-collect + "
            "early-target), and an explanatory note.\n"
            "- Macro Calendar: high-impact events in the holding window "
            "(FOMC, CPI, NFP, etc.) — denser calendars fatten tails.\n"
            "- Dealer Gamma: SPX dealer net gamma regime. Positive gamma = "
            "dealers damp moves (IC friendly). Negative gamma = amplifies "
            "moves (IC-hostile).\n"
            "- Cross-Asset Stress: HYG/LQD credit spreads, DXY, crude, gold, "
            "bitcoin composite stress score — elevated cross-asset stress "
            "raises breach tails.\n"
            "- Gap Regime (Engine 13): current overnight-gap environment — "
            "gappy regimes mechanically raise stop-out rates.\n"
            "- Net Adjustment: composite tail-multiplier × win-rate shift "
            "actually applied to the adjusted distribution."
        ),
    },

    "mtm_timeline": {
        "title": "MTM Timeline (P10 / P50 / P90)",
        "spec": (
            "Mark-to-market P&L **path** through the life of the trade, as a "
            "% of credit received, at each day-to-expiry step.\n"
            "- P50 (median): the typical path — what you'd MTM on a normal "
            "analogue.\n"
            "- P10 / P90: the 10th and 90th percentile paths — the bad-tail "
            "and good-tail envelopes.\n"
            "A steep P10 dip early = analogues commonly got punched in the "
            "face before recovering (path risk even if the outcome was "
            "positive). A flat P50 that drifts up is the classic theta-decay "
            "glide."
        ),
    },

    "position_sizing": {
        "title": "Position Sizing",
        "spec": (
            "Four sizing recommendations expressed as a **fraction of "
            "equity** to risk on this structure:\n"
            "- **Consensus (min of three)**: the floor — the most "
            "conservative of the three methods below. This is the value to "
            "defer to unless you have a reason.\n"
            "- **Kelly (½-Kelly)**: half-Kelly sizing using the empirical "
            "win probability and payoff ratio from the replay. Clamped to "
            "guard against outliers.\n"
            "- **Fixed-Fractional**: standard risk-per-trade sizing against "
            "the worst-case loss seen in the analogue pool.\n"
            "- **Empirical Max-DD**: sizing that would have capped historical "
            "drawdown to the target percentage given this structure's "
            "observed drawdown path."
        ),
    },

    "greeks_attribution": {
        "title": "P&L Attribution (Greeks)",
        "spec": (
            "Average decomposition of per-analogue P&L across delta / gamma / "
            "theta / vega / residual, using an **entry-Taylor approximation** "
            "(greeks × realized factor moves). Two numbers per greek:\n"
            "- Pct value: contribution to P&L in % of credit (signed).\n"
            "- Share of |P&L|: the greek's % of the total absolute-value "
            "bar.\n"
            "Residual absorbs unmodeled IV-path, second-order cross greeks, "
            "and fill slippage — so a large residual is itself a signal that "
            "the Taylor proxy is missing something."
        ),
    },

    "exit_optimization": {
        "title": "Exit-Rule Optimization",
        "spec": (
            "A grid search over profit-target and stop-loss levels across the "
            "matched analogues, picking the PT/SL pair that maximizes average "
            "P&L subject to a minimum win-rate floor.\n"
            "- Recommended PT / SL: the best grid cell.\n"
            "- Δ Win Rate / Δ Avg P&L: change vs the defaults you submitted "
            "(green = improvement).\n"
            "If the recommendation matches the defaults, your rules are "
            "already near-optimal on this pool — don't chase small edges."
        ),
    },

    "exit_sensitivity": {
        "title": "Exit-Rule Sensitivity",
        "spec": (
            "Interactive sliders that let you scrub across the exit-rule grid "
            "and see win-rate + avg-P&L for any PT/SL combo without re-running "
            "the replay. Use this to see how robust the optimum is: if the "
            "metrics are flat across a wide region, the rule is sturdy; if "
            "they cliff, the optimum is fragile."
        ),
    },

    "conditioning_notes": {
        "title": "Conditioning Notes",
        "spec": (
            "Plain-English bullets the simulator emits when unusual conditions "
            "were detected: thin sample, feature-store outage, unusual "
            "calendar density, sparse chain cache, analogue-pool skew, etc. "
            "Treat these as sanity checks before leaning on the distribution."
        ),
    },

    "matched_analogues": {
        "title": "Matched Analogues",
        "spec": (
            "Row-by-row view of the individual historical IC replays that "
            "informed the distribution. Each row shows the historical entry "
            "and expiry dates, the resulting outcome bucket, the day the "
            "replay exited, realized P&L (% of credit), max adverse excursion "
            "(% of credit), the mapped strikes from that day, and whether a "
            "short strike was breached at expiry. Use this to sanity-check "
            "the distribution against specific dates and to spot unusual "
            "rows that might deserve exclusion."
        ),
    },

    "post_trade_review": {
        "title": "Post-Trade Review",
        "spec": (
            "After a live trade is saved to the journal and later closed, "
            "this panel compares the **actual** realized P&L and outcome vs "
            "the **predicted** mean / median / outcome-probability from the "
            "simulation at entry. The verdict banner summarizes whether the "
            "sim was within ±15pp of reality, and in which direction the "
            "divergence went — a fast feedback loop for model calibration."
        ),
    },

    "actions": {
        "title": "Actions",
        "spec": (
            "Operational hand-offs after a run:\n"
            "- Save to Trade Log: persists the scenario + entry context to "
            "the Engine 2 trade journal so Post-Trade Review can score it "
            "later.\n"
            "- Copy Chat Summary: builds a text summary of the scenario and "
            "copies it to the clipboard so you can paste it into Raven Chat "
            "for a human-in-the-loop discussion."
        ),
    },
}


def card_title(card_type: str) -> str:
    """Public accessor used by the router for titles when falling back."""
    spec = CARD_CATALOG.get(card_type)
    return (spec or {}).get("title", card_type)


def supported_card_types() -> List[str]:
    return sorted(CARD_CATALOG.keys())


# ---------------------------------------------------------------------------
# LLM call
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT_TMPL = """You are a senior quant options desk strategist explaining a live analytics card to a trader.

The trader is looking at Engine 14 — an SPX iron condor scenario simulator that replays the proposed structure against matched historical analogues and shows the outcome distribution plus conditioning adjustments.

The card the trader is asking about is:

{card_title}

AUTHORITATIVE SPEC for this card (use this as ground truth — do NOT contradict it):
---
{card_spec}
---

You will receive a JSON payload:
  {{
    "card_data":         <the live numbers this card is displaying>,
    "scenario_context":  <high-level context: strikes, expiry, credit, entry, analogues_used>
  }}

Produce EXACTLY this JSON (no extra fields, no markdown, no code fences):
{{
  "what_this_shows":  "<one or two sentences, plain English>",
  "how_to_read_it":   "<how to interpret the numbers/labels on THIS card — reference the live values>",
  "how_to_use_it":    "<concrete, real-world action: how this changes a sizing/entry/exit decision>",
  "watch_for":        "<the failure mode or footgun — what the card does NOT tell you, or when to distrust it>",
  "desk_takeaway":    "<one sentence, 20-30 words, the single takeaway for THIS specific scenario>"
}}

Rules:
- Ground every claim in the spec and the live card_data. Do not invent data.
- Reference live numbers from card_data where relevant (e.g. "with fullCollect at 62% here...").
- Never recommend a specific trade or mention specific dollar amounts.
- Each field is prose, not bullet points, 1-3 sentences each (except desk_takeaway: one sentence).
- Output valid JSON only."""


# In-memory cache: (card_type, data_hash) -> result dict.
# 10-minute TTL is plenty for a desk session without staling on a re-run.
_cache_lock = threading.Lock()
_cache: TTLCache = TTLCache(maxsize=256, ttl=10 * 60)


# 20 calls/minute is comfortable for a desk clicking around, and protects
# the OpenAI quota if someone wires a hover handler by mistake.
class _RateLimiter:
    def __init__(self, max_calls_per_minute: int = 20):
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


_rate_limiter = _RateLimiter()


def _cache_key(card_type: str, card_data: Any, scenario_context: Any) -> str:
    try:
        payload = json.dumps(
            {"d": card_data, "s": scenario_context},
            default=str, sort_keys=True,
        )
    except Exception:
        payload = repr((card_data, scenario_context))
    h = hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]
    return f"{card_type}:{h}"


def _static_fallback(card_type: str, reason: str) -> Dict[str, Any]:
    """Deterministic fallback: return the static spec as a plain explanation.

    This lets the UI always render *something* even when the LLM is disabled,
    rate-limited, or errors out.
    """
    spec_entry = CARD_CATALOG.get(card_type) or {}
    spec = spec_entry.get("spec", "")
    # Crude but safe: the first sentence of the spec tends to be a summary.
    first_sentence = ""
    for sep in (". ", ".\n"):
        if sep in spec:
            first_sentence = spec.split(sep, 1)[0].rstrip(".") + "."
            break
    if not first_sentence:
        first_sentence = spec[:280] + ("…" if len(spec) > 280 else "")
    return {
        "what_this_shows": first_sentence or "Desk analytics card.",
        "how_to_read_it":  spec[:700] or "See the card labels and values directly.",
        "how_to_use_it":   (
            "Cross-check the numbers on this card against the adjacent "
            "outcome distribution and conditioning modifiers before leaning "
            "on the signal."
        ),
        "watch_for":       (
            "This is a spec-based fallback — the narrative LLM isn't available, "
            "so the text is generic and not grounded in today's specific values."
        ),
        "desk_takeaway":   "Spec fallback — refer to the raw card values.",
        "_source":         "fallback",
        "_card_type":      card_type,
        "_fallback_reason": reason,
    }


def generate_card_explanation(
    card_type: str,
    card_data: Any,
    scenario_context: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Generate a desk-friendly explanation of a single Engine 14 card.

    Args:
        card_type: One of the keys in CARD_CATALOG.
        card_data: The live payload the card is rendering (any JSON-serializable).
        scenario_context: Optional high-level scenario context (strikes, credit,
            expiry, analogues_used, regime bucket, etc.).

    Returns:
        Dict with the 5 required keys plus `_source`, `_card_type`, and,
        on fallback, `_fallback_reason`.
    """
    card_type = str(card_type or "").strip()
    spec_entry = CARD_CATALOG.get(card_type)
    if spec_entry is None:
        return _static_fallback(card_type, f"Unknown card_type: {card_type!r}")

    scenario_context = scenario_context or {}

    ckey = _cache_key(card_type, card_data, scenario_context)
    with _cache_lock:
        cached = _cache.get(ckey)
    if cached is not None:
        return cached

    if not _rate_limiter.acquire():
        return _static_fallback(
            card_type,
            "Rate limited (20/min). Pause briefly and try again.",
        )

    client = _get_openai_client()
    if client is None:
        return _static_fallback(card_type, "OPENAI_API_KEY not configured")

    model = (os.getenv("ENGINE14_EXPLAIN_MODEL")
             or os.getenv("LLM_MODEL_NARRATIVE")
             or "gpt-5.4").strip()

    system_prompt = _SYSTEM_PROMPT_TMPL.format(
        card_title=spec_entry.get("title", card_type),
        card_spec=spec_entry.get("spec", "").strip(),
    )

    user_payload = {
        "card_data": card_data,
        "scenario_context": scenario_context,
    }
    try:
        user_str = json.dumps(user_payload, default=str)
    except Exception as e:
        user_str = json.dumps({"card_data": repr(card_data),
                               "scenario_context": repr(scenario_context),
                               "_serialize_error": str(e)})
    if len(user_str) > 12000:
        user_str = user_str[:12000] + "…(truncated)"

    try:
        response = client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_str},
            ],
            temperature=0.3,
            max_completion_tokens=700,
            timeout=30,
            response_format={"type": "json_object"},
        )
        content = (response.choices[0].message.content or "").strip()
    except Exception as e:
        reason = f"{type(e).__name__}: {e}"
        LOG.warning("engine14 explain-card LLM call failed (%s): %s", card_type, reason)
        return _static_fallback(card_type, reason)

    parsed = _parse_desk_brief_json(content)
    if parsed is None:
        LOG.warning("engine14 explain-card (%s) could not parse LLM JSON", card_type)
        return _static_fallback(card_type, "LLM returned invalid JSON")

    result: Dict[str, Any] = {}
    missing: List[str] = []
    for k in OUTPUT_KEYS:
        v = parsed.get(k)
        if not isinstance(v, str) or not v.strip():
            missing.append(k)
            result[k] = ""
        else:
            # Cap each field so a runaway LLM can't push 10 KB into a tooltip.
            result[k] = v.strip()[:900]

    if missing:
        LOG.warning(
            "engine14 explain-card (%s) missing fields: %s",
            card_type, ",".join(missing),
        )
        return _static_fallback(
            card_type, f"LLM output missing fields: {', '.join(missing)}"
        )

    result["_source"] = "llm"
    result["_card_type"] = card_type
    result["_meta"] = {
        "card_title": spec_entry.get("title", card_type),
        "model": model,
    }
    with _cache_lock:
        _cache[ckey] = result
    return result
