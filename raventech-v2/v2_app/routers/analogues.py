"""v2 analogue retrieval endpoints (Phase 0 stubs).

The killer module for E15 v2: cross-ticker, cross-time analogue matching
in a contrastive embedding space. For now this endpoint advertises its
shape so the frontend can render the eventual result skeleton.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter

from ..config import get_config

router = APIRouter()


@router.get("/api/v2/analogues/search")
def search(
    ticker: Optional[str] = None,
    event_date: Optional[str] = None,
    k: int = 80,
    cross_ticker: bool = True,
) -> dict:
    cfg = get_config()
    return {
        "status": "phase0_stub" if not cfg.enable_contrastive_analogues else "not_yet_wired",
        "query": {
            "ticker": (ticker or "").upper() or None,
            "event_date": event_date,
            "k": int(k),
            "cross_ticker": bool(cross_ticker),
        },
        "neighbors": [],
        "embedding_space": {
            "dim": 128,
            "training_corpus_size": None,
            "trained_at": None,
        },
        "message": (
            "Contrastive analogue embedder is the v2 killer module. Once trained, this "
            "endpoint returns up to 80 cross-ticker / cross-time earnings-event neighbors "
            "with their forward 5-day path distributions."
        ),
    }
