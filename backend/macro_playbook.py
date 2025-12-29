from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MacroPlaybook:
    key: str
    label: str
    importance: int  # 1..5 (relative)
    desk_view: List[str]
    watch: List[str]
    notes: List[str]


def _pb(
    key: str,
    *,
    label: str,
    importance: int,
    desk_view: List[str],
    watch: List[str] | None = None,
    notes: List[str] | None = None,
) -> MacroPlaybook:
    return MacroPlaybook(
        key=str(key),
        label=str(label),
        importance=int(importance),
        desk_view=list(desk_view or []),
        watch=list(watch or []),
        notes=list(notes or []),
    )


# Top10-ish set requested:
# CPI/FOMC/NFP + PPI, Retail Sales, PMI/ISM, Claims, Treasury auctions/refunding.
_PLAYBOOKS: Dict[str, MacroPlaybook] = {
    "CPI": _pb(
        "CPI",
        label="CPI (headline)",
        importance=5,
        desk_view=[
            "Treat as a volatility catalyst: reduce size and widen wings into the print; avoid being short gamma right into release.",
            "Focus on surprise vs forecast and the immediate rate-path repricing; direction can flip quickly.",
            "If you must hold short premium, bias toward structures that survive a 1–2σ gap + intraday trend day.",
        ],
        watch=[
            "Release time (usually 08:30 ET).",
            "Actual vs forecast; revisions; core vs headline if available.",
            "Rates (2y/10y), USD, and SPX reaction (risk-on/off regime).",
        ],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "PPI": _pb(
        "PPI",
        label="PPI",
        importance=3,
        desk_view=[
            "Secondary inflation input; can move rates but typically lower impact than CPI.",
            "Expect choppiness at release; fade attempts can fail if it changes Fed pricing materially.",
        ],
        watch=["Actual vs forecast; revisions; follow-through into CPI/FOMC windows."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "RETAIL_SALES": _pb(
        "RETAIL_SALES",
        label="Retail Sales",
        importance=3,
        desk_view=[
            "Growth-sensitive release: can shift cyclicals vs defensives and impact rates via growth expectations.",
            "Short premium risk: watch for trend days if the print flips the growth narrative.",
        ],
        watch=["Actual vs forecast; control group (if present); revisions."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "NFP": _pb(
        "NFP",
        label="Nonfarm Payrolls (NFP)",
        importance=5,
        desk_view=[
            "High-impact macro print; treat like CPI in terms of gap risk and fast repricing.",
            "If short premium, avoid tight wings; consider standing down if liquidity is thin.",
        ],
        watch=[
            "Headline payrolls, unemployment rate, AHE/wages (if present).",
            "Rates first, then index; watch for reversal after initial impulse.",
        ],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "JOBLESS_CLAIMS": _pb(
        "JOBLESS_CLAIMS",
        label="Initial Jobless Claims",
        importance=2,
        desk_view=[
            "Usually lower impact, but can matter in recession/scare regimes.",
            "Short premium: watch for regime dependence—claims surprises can extend an existing trend.",
        ],
        watch=["4-week avg trend; revisions; correlation with rates if risk is macro-driven."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "PMI_ISM": _pb(
        "PMI_ISM",
        label="PMI / ISM",
        importance=3,
        desk_view=[
            "Growth/forward-looking sentiment; can drive sector rotation and intraday trends.",
            "Short premium: be cautious when the tape is already fragile—PMI surprises can accelerate moves.",
        ],
        watch=["Headline vs components (prices paid/employment/new orders) when available."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "FOMC_RATE_DECISION": _pb(
        "FOMC_RATE_DECISION",
        label="FOMC rate decision",
        importance=5,
        desk_view=[
            "Treat as a two-part event: statement + press conference. Vol can expand again at 14:30 ET.",
            "Short premium: widen/stand down into the event; be careful with 0DTE/short gamma.",
            "Watch for dovish/hawkish repricing through the rates complex; equities can whipsaw.",
        ],
        watch=["Decision + SEP/dot plot (if present), statement tone, press Q&A."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "FOMC_MINUTES": _pb(
        "FOMC_MINUTES",
        label="FOMC minutes",
        importance=3,
        desk_view=[
            "Often a secondary catalyst; can reprice rates if tone differs from the last presser.",
            "Short premium: expect a burst of realized vol around release; usually smaller than decision day.",
        ],
        watch=["Tone vs prior meeting; any shift in inflation/growth emphasis."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "TREASURY_AUCTION": _pb(
        "TREASURY_AUCTION",
        label="Treasury auction",
        importance=2,
        desk_view=[
            "Watch tails/bid-to-cover—poor auctions can pressure rates and risk assets intraday.",
            "Short premium: usually manageable, but can matter in duration-sensitive regimes.",
        ],
        watch=["Auction tenor (2y/5y/7y/10y/30y), tail, indirect/direct bid, WIIM headlines."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
    "TREASURY_REFUNDING": _pb(
        "TREASURY_REFUNDING",
        label="Treasury refunding",
        importance=3,
        desk_view=[
            "Supply announcements can shift term premium; can matter if the market is supply-sensitive.",
            "Short premium: treat like a rates catalyst—watch for duration-driven equity moves.",
        ],
        watch=["Issuance mix, size, and any commentary that shifts supply expectations."],
        notes=["Risk-only desk guidance; not investment advice."],
    ),
}


def get_playbook(*, key: str) -> Optional[Dict[str, Any]]:
    """
    Return a JSON-serializable playbook object for a macro event key.
    """
    k = str(key or "").strip().upper()
    pb = _PLAYBOOKS.get(k)
    if pb is None:
        return None
    return {
        "key": pb.key,
        "label": pb.label,
        "importance": pb.importance,
        "deskView": pb.desk_view,
        "watch": pb.watch,
        "notes": pb.notes,
    }


