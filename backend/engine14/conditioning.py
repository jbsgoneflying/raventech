"""Engine 14 — Phase 2 conditioning modifiers.

Enriches the empirical simulator payload with forward-looking context:

  * `calendar`     — FOMC/CPI/NFP/PCE/jobs proximity inside the trade window
  * `dealerGamma`  — current SPX dealer gamma (pinning tailwind vs vol headwind)
  * `creditStress` — cross-asset stress composite from today's DMS
  * `gapRegime`    — Engine 13 gap-regime scan (active overnight gap context)

Design rules
------------
1. Each modifier gracefully degrades: if its upstream client/DB isn't available
   we return a `{"status": "unavailable", ...}` block without raising.
2. Modifiers NEVER mutate the empirical `outcomeDistribution`. Instead they
   compute a `tailMultiplier` and `winRateShiftPct` and the caller builds a
   parallel `adjustedOutcomeDistribution` view. The base distribution stays
   the source of truth; the adjusted view is labeled conditional.
3. No per-analogue macro enrichment in Phase 2 (would require N Benzinga
   calls per replay — parked for Phase 4).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

LOG = logging.getLogger("engine14.conditioning")


# ---------------------------------------------------------------------------
# Data shapes
# ---------------------------------------------------------------------------

@dataclass
class Modifier:
    name: str
    status: str                     # "ok" | "unavailable" | "skipped"
    severity: str = "none"          # "none" | "low" | "moderate" | "elevated" | "extreme"
    tail_multiplier: float = 1.0    # >1 widens tail risk, <1 narrows
    win_rate_shift_pct: float = 0.0  # absolute pts to add to fullCollect+earlyTarget combined
    note: str = ""
    details: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status,
            "severity": self.severity,
            "tailMultiplier": round(float(self.tail_multiplier), 3),
            "winRateShiftPct": round(float(self.win_rate_shift_pct), 2),
            "note": self.note,
            "details": self.details,
        }


# ---------------------------------------------------------------------------
# 2a. Macro calendar (FOMC / CPI / NFP / PCE / claims)
# ---------------------------------------------------------------------------

# Event-keyword → (severity, tail_mult_bump, wr_shift). Keys are matched
# case-insensitively against the event's `description`/`key`.
_HIGH_IMPACT_KEYWORDS: List[tuple[str, str, float, float]] = [
    ("FOMC",                    "extreme",   0.45, -6.0),
    ("Interest Rate",           "extreme",   0.45, -6.0),
    ("CPI",                     "elevated",  0.25, -3.5),
    ("PPI",                     "elevated",  0.20, -3.0),
    ("Core PCE",                "elevated",  0.22, -3.2),
    ("Nonfarm Payroll",         "elevated",  0.25, -3.5),
    ("Employment Situation",    "elevated",  0.25, -3.5),
    ("Unemployment",            "moderate",  0.15, -2.0),
    ("GDP",                     "moderate",  0.15, -2.0),
    ("Retail Sales",            "moderate",  0.12, -1.5),
    ("ISM",                     "moderate",  0.10, -1.0),
    ("Jobless Claims",          "low",       0.05, -0.5),
    ("Consumer Confidence",     "low",       0.05, -0.5),
]

_SEVERITY_ORDER = {"none": 0, "low": 1, "moderate": 2, "elevated": 3, "extreme": 4}


def _classify_event(desc: str) -> Optional[tuple[str, float, float]]:
    """Return (severity, tail_bump, wr_shift) for a matching event, else None."""
    if not desc:
        return None
    d = desc.lower()
    for kw, sev, bump, wr in _HIGH_IMPACT_KEYWORDS:
        if kw.lower() in d:
            return (sev, bump, wr)
    return None


def compute_calendar_modifier(
    *,
    entry_date: str,
    expiry_date: str,
    benzinga_client: Any = None,
) -> Modifier:
    """Scan macro events in [entry_date, expiry_date] and produce a modifier."""
    if benzinga_client is None:
        return Modifier(
            name="calendar", status="unavailable",
            note="No Benzinga client — event-risk modifier skipped.",
        )

    try:
        entry = dt.date.fromisoformat(str(entry_date)[:10])
        expiry = dt.date.fromisoformat(str(expiry_date)[:10])
    except Exception:
        return Modifier(
            name="calendar", status="skipped",
            note="Could not parse entry/expiry dates.",
        )

    try:
        from backend.macro_events import macro_events_by_date
        events_by_date, sources, notes = macro_events_by_date(
            bz=benzinga_client, start=entry, end=expiry,
            pagesize=500, max_pages=4, importance_min=3, country="US",
        )
    except Exception as e:
        LOG.debug("calendar modifier: macro_events_by_date failed: %s", e)
        return Modifier(
            name="calendar", status="unavailable",
            note=f"Macro calendar fetch failed: {type(e).__name__}",
        )

    flat: List[Dict[str, Any]] = []
    for ds, rows in events_by_date.items():
        for r in rows or []:
            # macro_events_by_date normalizes to {kind, title, short, key, importance, ...}
            # Raw event_name/description are also accepted for fake fixtures in tests.
            desc = (
                r.get("title") or r.get("event_name")
                or r.get("description") or r.get("short") or r.get("key") or ""
            )
            flat.append({
                "date": ds,
                "description": str(desc),
                "importance": int(r.get("importance") or 0),
                "kind": str(r.get("kind") or ""),
            })

    hits: List[Dict[str, Any]] = []
    max_sev = "none"
    tail_bump = 0.0
    wr_shift = 0.0
    for ev in flat:
        cls = _classify_event(ev["description"])
        if cls is None:
            continue
        sev, bump, wr = cls
        tail_bump += bump
        wr_shift += wr
        if _SEVERITY_ORDER[sev] > _SEVERITY_ORDER[max_sev]:
            max_sev = sev
        hits.append({**ev, "severity": sev})

    # Cap adjustments so one frothy week can't blow out the payload.
    tail_bump = min(1.2, tail_bump)
    wr_shift = max(-18.0, wr_shift)

    if not hits:
        return Modifier(
            name="calendar", status="ok", severity="none",
            tail_multiplier=1.0, win_rate_shift_pct=0.0,
            note="No high-impact macro events inside the trade window.",
            details={"eventsConsidered": len(flat)},
        )

    descs = ", ".join(sorted({h["description"].split("(")[0].strip() for h in hits})[:4])
    note = f"{max_sev.upper()} macro week: {descs}"

    return Modifier(
        name="calendar", status="ok", severity=max_sev,
        tail_multiplier=1.0 + tail_bump, win_rate_shift_pct=wr_shift,
        note=note,
        details={
            "events": hits[:10],
            "eventsConsidered": len(flat),
            "windowStart": entry.isoformat(),
            "windowEnd": expiry.isoformat(),
        },
    )


# ---------------------------------------------------------------------------
# 2b. Dealer gamma (live-only SPX)
# ---------------------------------------------------------------------------

def compute_dealer_gamma_modifier(
    *,
    orats_client: Any = None,
    entry_date: str = "",
) -> Modifier:
    """Compute a pinning tailwind from current SPX dealer gamma.

    Live-only for Phase 2; if the requested entry_date is in the past we
    mark the modifier as informational but still return the current reading
    (useful context even for a back-dated trade).
    """
    if orats_client is None:
        return Modifier(
            name="dealerGamma", status="unavailable",
            note="No ORATS client — dealer-gamma modifier skipped.",
        )
    try:
        from backend.spx_ic.live_levels import compute_spx_live_levels
        ll = compute_spx_live_levels(orats_client, view="weekly")
    except Exception as e:
        LOG.debug("dealer_gamma modifier: compute_spx_live_levels failed: %s", e)
        return Modifier(
            name="dealerGamma", status="unavailable",
            note=f"Live gamma fetch failed: {type(e).__name__}",
        )

    dg = (ll or {}).get("dealerGamma") or {}
    sign = str(dg.get("netGammaSign") or "NEUTRAL").upper()
    magnitude = str(dg.get("magnitudeBucket") or "low").lower()
    net_gex = float(dg.get("netGex") or 0.0)

    if sign == "POSITIVE":
        tail_mult = 0.85 if magnitude in ("medium", "high") else 0.92
        wr_shift = +3.0 if magnitude in ("medium", "high") else +1.5
        severity = "low"
        note = f"Dealer gamma POSITIVE ({magnitude}) — intraday pinning tailwind for short-vol."
    elif sign == "NEGATIVE":
        tail_mult = 1.20 if magnitude in ("medium", "high") else 1.10
        wr_shift = -3.0 if magnitude in ("medium", "high") else -1.5
        severity = "moderate" if magnitude == "low" else "elevated"
        note = f"Dealer gamma NEGATIVE ({magnitude}) — realized-vol amplification headwind."
    else:
        tail_mult = 1.0
        wr_shift = 0.0
        severity = "none"
        note = "Dealer gamma NEUTRAL — no significant pinning/amplification bias."

    return Modifier(
        name="dealerGamma", status="ok", severity=severity,
        tail_multiplier=tail_mult, win_rate_shift_pct=wr_shift,
        note=note,
        details={
            "netGammaSign": sign,
            "magnitudeBucket": magnitude,
            "netGex": net_gex,
            "gammaFlipStrike": ll.get("gammaFlipStrike"),
            "asOf": ll.get("asOf") or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat() + "Z",
        },
    )


# ---------------------------------------------------------------------------
# 2c. Credit stress (from today's DMS)
# ---------------------------------------------------------------------------

_STRESS_TABLE = {
    "Risk-On":     ("low",      0.90, +1.5),
    "Neutral":     ("none",     1.00,  0.0),
    "Risk-Off":    ("moderate", 1.15, -2.5),
    "Stressed":    ("elevated", 1.30, -5.0),
}


def compute_credit_stress_modifier(
    *,
    store: Any = None,
    entry_date: str = "",
) -> Modifier:
    """Read today's cross-asset stress label from DMS."""
    if store is None:
        return Modifier(
            name="creditStress", status="unavailable",
            note="No Redis store — credit-stress modifier skipped.",
        )
    try:
        from backend.daily_market_state import load_dms, load_dms_history
        as_of = dt.date.today().isoformat()
        dms = load_dms(as_of, store)
        if dms is None:
            # Fall back to most recent persisted DMS.
            hist = load_dms_history(store, n=5)
            dms = hist[0] if hist else None
    except Exception as e:
        LOG.debug("credit_stress modifier: DMS load failed: %s", e)
        return Modifier(
            name="creditStress", status="unavailable",
            note=f"DMS load failed: {type(e).__name__}",
        )
    if dms is None:
        return Modifier(
            name="creditStress", status="unavailable",
            note="No DailyMarketState snapshot available.",
        )

    cas = getattr(dms, "cross_asset_stress", {}) or {}
    label = str(cas.get("composite_label") or "Neutral")
    score = float(cas.get("composite_score") or 50.0)
    sev, tail_mult, wr_shift = _STRESS_TABLE.get(label, ("none", 1.0, 0.0))

    note = (
        f"Cross-asset stress: {label} (score {score:.0f}/100)."
        if label != "Neutral"
        else "Cross-asset stress neutral — no macro-risk adjustment."
    )
    return Modifier(
        name="creditStress", status="ok", severity=sev,
        tail_multiplier=tail_mult, win_rate_shift_pct=wr_shift,
        note=note,
        details={
            "compositeLabel": label,
            "compositeScore": score,
            "asOf": getattr(dms, "date", None),
        },
    )


# ---------------------------------------------------------------------------
# 2d. Engine 13 gap regime (live-only)
# ---------------------------------------------------------------------------

def compute_gap_regime_modifier(
    *,
    orats_client: Any = None,
    benzinga_client: Any = None,
    entry_date: str = "",
) -> Modifier:
    """Summarize today's Engine 13 gap-regime scan as a modifier.

    Only materially adjusts the payload when a gap is *enabled* (Engine 13's
    definition of "actionable overnight gap"). Otherwise the modifier is a
    no-op with an informational note.
    """
    try:
        from backend.engine13_gap_regime import compute_gap_regime_scan
    except Exception:
        return Modifier(
            name="gapRegime", status="unavailable",
            note="Engine 13 gap module not importable.",
        )

    try:
        scan = compute_gap_regime_scan(
            orats=orats_client, benzinga=benzinga_client, gap_threshold_pct=1.5,
        )
    except Exception as e:
        LOG.debug("gap_regime modifier: compute_gap_regime_scan failed: %s", e)
        return Modifier(
            name="gapRegime", status="unavailable",
            note=f"Gap-regime scan failed: {type(e).__name__}",
        )

    gap = (scan or {}).get("gap") or {}
    enabled = bool(gap.get("enabled"))
    abs_pct = float(gap.get("absGapPct") or 0.0)
    direction = str(gap.get("direction") or "").upper()
    scenarios = (scan or {}).get("scenarios") or {}
    dom = str(scenarios.get("dominantScenario") or "").strip()

    if not enabled:
        return Modifier(
            name="gapRegime", status="ok", severity="none",
            tail_multiplier=1.0, win_rate_shift_pct=0.0,
            note="No actionable overnight gap today.",
            details={"enabled": False, "absGapPct": abs_pct},
        )

    # Scale with gap magnitude.
    if abs_pct >= 2.5:
        severity, tail_mult, wr_shift = "extreme", 1.45, -5.5
    elif abs_pct >= 1.75:
        severity, tail_mult, wr_shift = "elevated", 1.25, -3.5
    else:
        severity, tail_mult, wr_shift = "moderate", 1.12, -1.5

    note = f"Gap regime ACTIVE: {direction} {abs_pct:.2f}% — scenario '{dom or 'n/a'}'."
    return Modifier(
        name="gapRegime", status="ok", severity=severity,
        tail_multiplier=tail_mult, win_rate_shift_pct=wr_shift,
        note=note,
        details={
            "enabled": True,
            "direction": direction,
            "absGapPct": abs_pct,
            "dominantScenario": dom,
            "asOf": (scan or {}).get("asOfDate"),
        },
    )


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------

def compute_conditioning(
    *,
    entry_date: str,
    expiry_date: str,
    orats_client: Any = None,
    benzinga_client: Any = None,
    store: Any = None,
) -> Dict[str, Any]:
    """Compute all four modifiers and the net tail/win-rate adjustments.

    Returns a dict with shape:

        {
          "calendar":   Modifier dict,
          "dealerGamma": Modifier dict,
          "creditStress": Modifier dict,
          "gapRegime":  Modifier dict,
          "netTailMultiplier": 1.05,
          "netWinRateShiftPct": -1.2,
          "notes": [<strings>]
        }
    """
    mods: Dict[str, Modifier] = {
        "calendar": compute_calendar_modifier(
            entry_date=entry_date, expiry_date=expiry_date,
            benzinga_client=benzinga_client,
        ),
        "dealerGamma": compute_dealer_gamma_modifier(
            orats_client=orats_client, entry_date=entry_date,
        ),
        "creditStress": compute_credit_stress_modifier(
            store=store, entry_date=entry_date,
        ),
        "gapRegime": compute_gap_regime_modifier(
            orats_client=orats_client, benzinga_client=benzinga_client,
            entry_date=entry_date,
        ),
    }

    net_tail = 1.0
    net_wr = 0.0
    notes: List[str] = []
    for m in mods.values():
        if m.status == "ok":
            net_tail *= float(m.tail_multiplier)
            net_wr += float(m.win_rate_shift_pct)
        if m.note:
            notes.append(m.note)

    # Soft clips so pathological combos don't lie to the user.
    net_tail = max(0.55, min(2.00, net_tail))
    net_wr = max(-20.0, min(12.0, net_wr))

    out = {k: v.to_dict() for k, v in mods.items()}
    out["netTailMultiplier"] = round(float(net_tail), 3)
    out["netWinRateShiftPct"] = round(float(net_wr), 2)
    out["notes"] = notes
    return out


# ---------------------------------------------------------------------------
# Apply modifiers to a base outcome distribution
# ---------------------------------------------------------------------------

def apply_modifiers_to_distribution(
    *,
    base_distribution: Dict[str, Dict[str, Any]],
    net_tail_multiplier: float,
    net_wr_shift_pct: float,
) -> Dict[str, Dict[str, Any]]:
    """Produce an adjusted-outcome view from the empirical base.

    The algorithm:
      1. Scale `breach` and `stopOut` probabilities by the net tail multiplier.
      2. Shift `earlyTarget + fullCollect` combined probability by `net_wr_shift_pct`.
         (`whiteKnuckle` absorbs the residual.)
      3. Renormalize so the five buckets still sum to 100%.
    Averages (avgPnlPct, avgDays, MAE) are preserved as-is — we don't touch
    the empirical means; only probabilities shift.
    """
    base = {k: dict(v) for k, v in (base_distribution or {}).items()}
    if not base:
        return base

    def _pct(k: str) -> float:
        return float((base.get(k) or {}).get("pct") or 0.0)

    p_early = _pct("earlyTarget")
    p_full  = _pct("fullCollect")
    p_white = _pct("whiteKnuckle")
    p_stop  = _pct("stopOut")
    p_breach = _pct("breach")

    # 1) Tail scaling
    p_stop   *= float(net_tail_multiplier)
    p_breach *= float(net_tail_multiplier)

    # 2) Win-rate shift distributed across earlyTarget/fullCollect proportionally.
    total_win = max(1e-6, p_early + p_full)
    shift = float(net_wr_shift_pct)
    p_early_new = max(0.0, p_early + shift * (p_early / total_win))
    p_full_new  = max(0.0, p_full  + shift * (p_full  / total_win))

    # 3) Compute residual for whiteKnuckle and renormalize.
    total_now = p_early_new + p_full_new + p_white + p_stop + p_breach
    if total_now <= 0:
        return base

    # Renormalize to 100.
    scale = 100.0 / total_now
    out: Dict[str, Dict[str, Any]] = {}
    for k, p in (
        ("earlyTarget",  p_early_new * scale),
        ("fullCollect",  p_full_new * scale),
        ("whiteKnuckle", p_white * scale),
        ("stopOut",      p_stop * scale),
        ("breach",       p_breach * scale),
    ):
        src = base.get(k) or {}
        out[k] = {
            "pct": round(float(p), 1),
            "n": int(src.get("n", 0)),
            "avgPnlPct": float(src.get("avgPnlPct", 0.0)),
            "avgDays": float(src.get("avgDays", 0.0)),
            "maxAdverseExcursionPct": float(src.get("maxAdverseExcursionPct", 0.0)),
        }
    return out
