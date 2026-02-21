"""Engine 7 – Thematic Relative Value (Pairs) Engine: theme classification.

INV-1  Determinism guarantee
------
Layer 1 – ``classify_themes_deterministic`` is a **pure function** (keyword
rules, no external calls, no randomness).  This is the sole gating truth used
for eligibility and scoring decisions.

Layer 2 – ``annotate_themes_llm`` is an **optional enrichment** layer.  It
calls OpenAI with temperature=0, a fixed prompt, and sorted input.  Results
are persisted to Redis keyed by ``date + sha256(sorted_headlines + model)``.
Re-runs and backtests always replay from storage.  If OpenAI is unavailable
the annotation is ``None``; scoring and eligibility are unaffected.

INV-2  Theme-pair eligibility
------
``THEME_PAIR_ELIGIBILITY`` maps each theme to the pair_ids it validates and
the expected directional bias.  ``score_theme_alignment`` returns 0 when no
active theme covers a pair, which causes the screener to mark the signal
``NOT_ELIGIBLE``.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

_LOG = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------


@dataclass
class ThemeClassification:
    """One theme's deterministic classification for a given day."""
    theme: str = ""
    label: str = ""
    active: bool = False
    intensity: float = 0.0       # 0-100
    keyword_hits: int = 0
    sample_keywords: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ThemeResult:
    """Aggregation of all theme classifications for one scan."""
    date: str = ""
    themes: List[ThemeClassification] = field(default_factory=list)
    active_themes: List[str] = field(default_factory=list)
    headline_count: int = 0
    llm_annotation: Optional[Dict[str, Any]] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        d["themes"] = [t.to_dict() if isinstance(t, ThemeClassification) else t for t in self.themes]
        return d


# ---------------------------------------------------------------------------
# Theme keyword definitions
# ---------------------------------------------------------------------------

THEME_KEYWORD_MAP: Dict[str, dict] = {
    "ai_expansion": {
        "label": "AI Expansion",
        "keywords": [
            "ai revenue", "ai growth", "ai investment", "ai spending",
            "ai demand", "ai adoption", "ai infrastructure", "gpu demand",
            "data center", "artificial intelligence grow", "ai capex",
            "generative ai", "ai chip demand", "nvidia revenue",
            "cloud ai", "ai enterprise",
        ],
        "activation_threshold": 0.02,
    },
    "ai_deceleration": {
        "label": "AI Deceleration",
        "keywords": [
            "ai slowdown", "ai spending cut", "ai pullback", "ai hype",
            "ai bubble", "ai overinvest", "ai disappoint", "ai fatigue",
            "ai layoff", "ai cost cut", "ai scaling back",
        ],
        "activation_threshold": 0.02,
    },
    "rates_rising": {
        "label": "Rates Rising",
        "keywords": [
            "rate hike", "rates higher", "yield rise", "bond sell",
            "treasury sell", "hawkish", "inflation surprise", "cpi hot",
            "sticky inflation", "tightening", "higher for longer",
            "fed hawk", "rate increase", "10-year rise", "yield surge",
        ],
        "activation_threshold": 0.02,
    },
    "rates_falling": {
        "label": "Rates Falling",
        "keywords": [
            "rate cut", "rates lower", "yield fall", "bond rally",
            "treasury rally", "dovish", "inflation cool", "cpi soft",
            "disinflation", "easing", "fed dove", "rate decrease",
            "pivot", "yield drop",
        ],
        "activation_threshold": 0.02,
    },
    "risk_off": {
        "label": "Risk-Off",
        "keywords": [
            "risk off", "flight to safety", "market fear", "vix spike",
            "sell off", "panic", "recession fear", "credit stress",
            "bank risk", "contagion", "safe haven", "volatility spike",
            "crash", "correction", "bear market",
        ],
        "activation_threshold": 0.02,
    },
    "risk_on": {
        "label": "Risk-On",
        "keywords": [
            "risk on", "risk appetite", "market rally", "bull market",
            "optimism", "all time high", "breakout", "melt up",
            "euphoria", "fomo", "momentum", "equity inflow",
        ],
        "activation_threshold": 0.02,
    },
    "liquidity_expansion": {
        "label": "Liquidity Expansion",
        "keywords": [
            "liquidity inject", "qe", "quantitative easing", "fed balance sheet",
            "reverse repo decline", "tga drawdown", "money supply grow",
            "liquidity flush", "stimulus", "fiscal spend",
        ],
        "activation_threshold": 0.02,
    },
    "liquidity_tightening": {
        "label": "Liquidity Tightening",
        "keywords": [
            "qt", "quantitative tightening", "liquidity drain", "fed shrink",
            "reverse repo rise", "money supply shrink", "credit tighten",
            "lending standard", "dollar shortage", "funding stress",
        ],
        "activation_threshold": 0.02,
    },
    "energy_shock": {
        "label": "Energy Shock",
        "keywords": [
            "oil spike", "oil surge", "energy crisis", "opec cut",
            "gasoline price", "natural gas surge", "energy shock",
            "crude spike", "oil embargo", "refinery", "oil supply",
            "energy shortage", "brent surge",
        ],
        "activation_threshold": 0.02,
    },
    "sector_rotation": {
        "label": "Sector Rotation",
        "keywords": [
            "sector rotation", "rotation into", "rotation out of",
            "value over growth", "growth over value", "small cap rotation",
            "defensive rotation", "cyclical rotation", "style rotation",
            "broadening", "market breadth",
        ],
        "activation_threshold": 0.02,
    },
}


# ---------------------------------------------------------------------------
# Theme → Pair eligibility mapping
# ---------------------------------------------------------------------------

# Maps theme_id -> list of (pair_id, directional_bias) tuples.
# directional_bias: "long_ratio" means the long leg outperforms (ratio up),
# "short_ratio" means the short leg outperforms (ratio down).
THEME_PAIR_ELIGIBILITY: Dict[str, List[dict]] = {
    "ai_expansion": [
        {"pair_id": "QQQ_IWM", "bias": "long_ratio"},
        {"pair_id": "QQQ_SPY", "bias": "long_ratio"},
        {"pair_id": "SOXX_IGV", "bias": "long_ratio"},
        {"pair_id": "XLF_XLK", "bias": "short_ratio"},
        {"pair_id": "IBIT_QQQ", "bias": "long_ratio"},
    ],
    "ai_deceleration": [
        {"pair_id": "QQQ_IWM", "bias": "short_ratio"},
        {"pair_id": "QQQ_SPY", "bias": "short_ratio"},
        {"pair_id": "SOXX_IGV", "bias": "short_ratio"},
        {"pair_id": "XLF_XLK", "bias": "long_ratio"},
        {"pair_id": "RSP_SPY", "bias": "long_ratio"},
    ],
    "rates_rising": [
        {"pair_id": "GLD_TLT", "bias": "long_ratio"},
        {"pair_id": "XLF_XLK", "bias": "long_ratio"},
        {"pair_id": "HYG_TLT", "bias": "short_ratio"},
        {"pair_id": "XHB_SPY", "bias": "short_ratio"},
        {"pair_id": "UUP_SPY", "bias": "long_ratio"},
    ],
    "rates_falling": [
        {"pair_id": "GLD_TLT", "bias": "short_ratio"},
        {"pair_id": "XLF_XLK", "bias": "short_ratio"},
        {"pair_id": "HYG_TLT", "bias": "long_ratio"},
        {"pair_id": "XHB_SPY", "bias": "long_ratio"},
        {"pair_id": "UUP_SPY", "bias": "short_ratio"},
    ],
    "risk_off": [
        {"pair_id": "VIXY_SPY", "bias": "long_ratio"},
        {"pair_id": "SPHB_SPLV", "bias": "short_ratio"},
        {"pair_id": "XLY_XLP", "bias": "short_ratio"},
        {"pair_id": "SPY_EFA", "bias": "long_ratio"},
        {"pair_id": "HYG_TLT", "bias": "short_ratio"},
        {"pair_id": "XLI_XLU", "bias": "short_ratio"},
    ],
    "risk_on": [
        {"pair_id": "VIXY_SPY", "bias": "short_ratio"},
        {"pair_id": "SPHB_SPLV", "bias": "long_ratio"},
        {"pair_id": "XLY_XLP", "bias": "long_ratio"},
        {"pair_id": "QQQ_IWM", "bias": "long_ratio"},
        {"pair_id": "HYG_TLT", "bias": "long_ratio"},
        {"pair_id": "XLI_XLU", "bias": "long_ratio"},
    ],
    "liquidity_expansion": [
        {"pair_id": "SPHB_SPLV", "bias": "long_ratio"},
        {"pair_id": "QQQ_SPY", "bias": "long_ratio"},
        {"pair_id": "IBIT_QQQ", "bias": "long_ratio"},
        {"pair_id": "SPY_IWM", "bias": "short_ratio"},
    ],
    "liquidity_tightening": [
        {"pair_id": "SPHB_SPLV", "bias": "short_ratio"},
        {"pair_id": "QQQ_SPY", "bias": "short_ratio"},
        {"pair_id": "HYG_TLT", "bias": "short_ratio"},
        {"pair_id": "XBI_XLV", "bias": "short_ratio"},
    ],
    "energy_shock": [
        {"pair_id": "XLE_SPY", "bias": "long_ratio"},
        {"pair_id": "USO_SPY", "bias": "long_ratio"},
        {"pair_id": "XLI_XLU", "bias": "short_ratio"},
        {"pair_id": "XLY_XLP", "bias": "short_ratio"},
    ],
    "sector_rotation": [
        {"pair_id": "RSP_SPY", "bias": "long_ratio"},
        {"pair_id": "SPY_IWM", "bias": "short_ratio"},
        {"pair_id": "XLB_XLI", "bias": "long_ratio"},
        {"pair_id": "XBI_XLV", "bias": "long_ratio"},
    ],
}


# ---------------------------------------------------------------------------
# Layer 1: Deterministic keyword classifier (GATING TRUTH)
# ---------------------------------------------------------------------------


def classify_themes_deterministic(
    headlines: List[str],
    macro_data: Optional[dict] = None,
) -> ThemeResult:
    """Pure-function theme classifier.  No external calls.  No randomness.

    Each headline is lower-cased and matched against keyword sets.
    A theme activates when hit_ratio >= activation_threshold.
    """
    if not headlines:
        return ThemeResult(themes=[], active_themes=[], headline_count=0)

    n = len(headlines)
    lower_headlines = [h.lower() for h in headlines]
    results: List[ThemeClassification] = []

    for theme_id, tdef in THEME_KEYWORD_MAP.items():
        kws = tdef["keywords"]
        threshold = tdef.get("activation_threshold", 0.02)
        hits = 0
        matched_kws: List[str] = []

        for hl in lower_headlines:
            for kw in kws:
                if kw in hl:
                    hits += 1
                    if kw not in matched_kws:
                        matched_kws.append(kw)
                    break  # one hit per headline per theme

        hit_ratio = hits / float(n) if n > 0 else 0.0
        intensity = min(100.0, hit_ratio * 500.0)  # 20% hit ratio -> 100
        active = hit_ratio >= threshold and hits >= 1

        results.append(ThemeClassification(
            theme=theme_id,
            label=tdef["label"],
            active=active,
            intensity=round(intensity, 2),
            keyword_hits=hits,
            sample_keywords=matched_kws[:5],
        ))

    active_themes = [t.theme for t in results if t.active]
    return ThemeResult(
        themes=results,
        active_themes=active_themes,
        headline_count=n,
    )


# ---------------------------------------------------------------------------
# Theme alignment scoring
# ---------------------------------------------------------------------------


def score_theme_alignment(pair_id: str, theme_result: ThemeResult) -> Tuple[float, List[str]]:
    """Return (score 0-100, matching_theme_tags) for a pair given active themes.

    Score is 0 when no active theme covers the pair (-> NOT_ELIGIBLE under INV-2).
    """
    if not theme_result.active_themes:
        return 0.0, []

    matching_tags: List[str] = []
    total_intensity = 0.0

    for theme_id in theme_result.active_themes:
        eligible_pairs = THEME_PAIR_ELIGIBILITY.get(theme_id, [])
        for ep in eligible_pairs:
            if ep.get("pair_id") == pair_id:
                matching_tags.append(theme_id)
                theme_cls = next((t for t in theme_result.themes if t.theme == theme_id), None)
                if theme_cls:
                    total_intensity += theme_cls.intensity
                break

    if not matching_tags:
        return 0.0, []

    score = min(100.0, total_intensity / len(matching_tags) if matching_tags else 0.0)
    score = max(score, 20.0) if matching_tags else 0.0
    return round(score, 2), matching_tags


def get_theme_bias(pair_id: str, theme_result: ThemeResult) -> Optional[str]:
    """Return the expected directional bias for a pair from active themes.

    Returns "long_ratio", "short_ratio", or None if no theme applies / mixed.
    """
    biases: List[str] = []
    for theme_id in theme_result.active_themes:
        eligible_pairs = THEME_PAIR_ELIGIBILITY.get(theme_id, [])
        for ep in eligible_pairs:
            if ep.get("pair_id") == pair_id:
                biases.append(ep.get("bias", ""))
                break

    if not biases:
        return None
    if all(b == biases[0] for b in biases):
        return biases[0]
    return None  # mixed signals


# ---------------------------------------------------------------------------
# Layer 2: LLM annotation (OPTIONAL ENRICHMENT – never affects scoring)
# ---------------------------------------------------------------------------

_LLM_ANNOTATION_PROMPT = """You are a macro strategist. Given the following market headlines,
identify the dominant macro themes currently in play and briefly describe how
each theme might affect relative performance between paired assets.

Headlines:
{headlines}

Respond with valid JSON only:
{{
  "dominant_themes": [
    {{"theme": "<theme_name>", "description": "<one sentence>", "confidence": <0.0-1.0>}}
  ],
  "macro_summary": "<two sentences summarising the macro environment>"
}}"""

_ANNOTATION_REDIS_PREFIX = "engine7:theme_annotation"


def _annotation_cache_key(date_str: str, headlines: List[str], model: str) -> str:
    """Build a deterministic Redis key for a given input set."""
    sorted_hl = sorted(headlines)
    payload = json.dumps(sorted_hl, sort_keys=True) + "|" + model
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"{_ANNOTATION_REDIS_PREFIX}:{date_str}:{digest}"


def annotate_themes_llm(
    headlines: List[str],
    date_str: str,
    *,
    store: Any = None,
    model: str = "gpt-4o-mini",
    ttl_s: int = 7 * 86400,
) -> Optional[Dict[str, Any]]:
    """Optional LLM enrichment.  Replays from Redis when available.

    INV-1: this output is NEVER used for scoring or eligibility.
    """
    if not headlines:
        return None

    cache_key = _annotation_cache_key(date_str, headlines, model)

    # Try Redis replay
    if store is not None:
        try:
            cached = store.get_json(cache_key)
            if cached is not None:
                _LOG.debug("Engine7 theme LLM annotation replayed from Redis: %s", cache_key)
                return cached
        except Exception:
            pass

    # Call LLM
    try:
        from backend.llm_client import _get_openai_client, _rate_limiter, _parse_desk_brief_json
    except ImportError:
        _LOG.debug("LLM client not available for Engine7 annotation")
        return None

    if not _rate_limiter.acquire():
        _LOG.debug("Engine7 theme LLM rate-limited; skipping annotation")
        return None

    client = _get_openai_client()
    if client is None:
        return None

    sorted_hl = sorted(headlines)
    prompt = _LLM_ANNOTATION_PROMPT.format(headlines="\n".join(f"- {h}" for h in sorted_hl[:50]))

    try:
        resp = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": prompt}],
            temperature=0,
            max_tokens=500,
            timeout=15,
        )
        content = resp.choices[0].message.content or ""
        parsed = _parse_desk_brief_json(content)
        if parsed is None:
            _LOG.warning("Engine7 theme LLM annotation parse failed")
            return None
    except Exception as exc:
        _LOG.warning("Engine7 theme LLM annotation failed: %s", exc)
        return None

    # Persist to Redis for replay
    if store is not None:
        try:
            store.set_json(cache_key, parsed, ttl_s=ttl_s)
            _LOG.debug("Engine7 theme LLM annotation persisted: %s", cache_key)
        except Exception:
            pass

    return parsed


# ---------------------------------------------------------------------------
# Headline fetching helper
# ---------------------------------------------------------------------------


def fetch_headlines(date_str: str, lookback_days: int = 7) -> List[str]:
    """Fetch recent headlines from EODHD.  Returns list of title strings.

    Default lookback is 7 days to ensure full coverage across weekends
    and holidays when market news may be sparse.
    """
    try:
        from backend.eodhd_client import EodhdClient
        import os as _os
        token = _os.getenv("EODHD_API_TOKEN", "")
        if not token:
            _LOG.warning("Engine7 headline fetch: EODHD_API_TOKEN not set")
            return []
        client = EodhdClient(token=token)
        end = dt.date.fromisoformat(date_str)
        start = end - dt.timedelta(days=lookback_days)
        resp = client.get_news(
            topic="market",
            from_date=start.isoformat(),
            to_date=end.isoformat(),
            limit=200,
        )
        titles: List[str] = []
        for row in (resp.rows or []):
            title = row.get("title") or ""
            if title.strip():
                titles.append(title.strip())
        _LOG.info("Engine7 headline fetch: %d headlines from %s to %s", len(titles), start.isoformat(), end.isoformat())
        return titles
    except Exception as exc:
        _LOG.warning("Engine7 headline fetch failed: %s", exc)
        return []
