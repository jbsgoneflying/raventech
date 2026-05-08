"""HTTP entrypoint for the counterfactual logger.

v1 routes can POST paired verdicts here so we accumulate a clean dataset
of v1-vs-v2 disagreements before the desk ever sees v2 verdicts.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

from ..counterfactual_logger import log_counterfactual

router = APIRouter()


class CounterfactualPayload(BaseModel):
    engine: str = Field(..., description="e1 | e2 | e14 | e15 | mi")
    v1_verdict: dict[str, Any] | None = None
    v2_verdict: dict[str, Any] | None = None
    request_id: str | None = None
    delta_summary: str | None = None


@router.post("/api/v2/counterfactual/log")
def log(payload: CounterfactualPayload) -> dict:
    sid = log_counterfactual(
        engine=payload.engine,
        v1_verdict=payload.v1_verdict,
        v2_verdict=payload.v2_verdict,
        request_id=payload.request_id,
        delta_summary=payload.delta_summary,
    )
    return {"ok": True, "stream_id": sid, "logged": sid is not None}
