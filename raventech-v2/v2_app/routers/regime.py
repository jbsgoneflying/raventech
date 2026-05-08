"""v2 regime endpoints (Phase 0 stubs).

Once Layer 1 is trained, ``/api/v2/regime/embed`` returns the learned
regime embedding + cluster probabilities + nearest historical days.
For now the endpoint surfaces a deterministic placeholder so the
frontend can wire end-to-end without waiting for the model.
"""

from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, HTTPException

from ..config import get_config

router = APIRouter()


@router.get("/api/v2/regime/embed")
def embed(date: Optional[str] = None) -> dict:
    cfg = get_config()
    if not cfg.enable_regime_encoder:
        return {
            "status": "phase0_stub",
            "message": (
                "The v2 regime encoder is not yet trained. Once V2_ENABLE_REGIME_ENCODER=1, "
                "this endpoint returns a 64-dim embedding + cluster probabilities + nearest "
                "historical days."
            ),
            "as_of": date,
            "embedding_dim": 64,
            "expected_cluster_count": 6,
        }
    raise HTTPException(status_code=501, detail="Regime encoder enabled but not yet wired.")


@router.get("/api/v2/regime/nearest")
def nearest(date: Optional[str] = None, k: int = 5) -> dict:
    return {
        "status": "phase0_stub",
        "as_of": date,
        "k": int(k),
        "neighbors": [],
    }
