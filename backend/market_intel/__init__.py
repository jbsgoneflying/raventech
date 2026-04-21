"""Raven Market Intelligence v2 — principled, HMM-grounded cross-asset layer.

Single source of truth for regime state, vol state, and cross-asset stress.
Every engine (E1-E15) that needs these signals should import from here
rather than recomputing them locally.

Public API:

    from backend.market_intel import (
        regime_snapshot,           # canonical RegimeSnapshot for today
        build_factor_matrix,       # factor reader entrypoint
        build_cross_asset_v2,      # broader-universe cross-asset stress
        compute_market_diff,       # day-over-day intelligent diff
        build_dms_v2,              # DMS assembler (replaces _build_live_dms)
    )

The HMM is implemented in pure Python to avoid adding numpy/hmmlearn as
a runtime dependency (~100MB Docker overhead). The math is standard:
Gaussian emissions, forward-backward smoothing, Baum-Welch fit. See
``regime_model.py`` for the 200-line implementation.
"""
from __future__ import annotations

from backend.market_intel.factors import (
    FactorReading,
    FactorSnapshot,
    build_factor_matrix,
    build_factor_snapshot,
    FACTOR_KEYS,
)
from backend.market_intel.regime_model import (
    CalibratedModel,
    RegimeInference,
    fit_model,
    infer,
    bootstrap_confidence,
    load_model,
    save_model,
)
from backend.market_intel.regime_service import (
    RegimeSnapshot,
    regime_snapshot,
    canonical_vol_state,
    clear_cache,
    service_health,
)
from backend.market_intel.cross_asset_v2 import (
    CrossAssetV2Snapshot,
    build_cross_asset_v2,
    CROSS_ASSET_V2_UNIVERSE,
)
from backend.market_intel.diff import (
    MarketDiff,
    compute_market_diff,
)
from backend.market_intel.dms_builder import (
    build_dms_v2,
)

__all__ = [
    "FactorReading",
    "FactorSnapshot",
    "FACTOR_KEYS",
    "build_factor_matrix",
    "build_factor_snapshot",
    "CalibratedModel",
    "RegimeInference",
    "fit_model",
    "infer",
    "bootstrap_confidence",
    "load_model",
    "save_model",
    "RegimeSnapshot",
    "regime_snapshot",
    "canonical_vol_state",
    "clear_cache",
    "service_health",
    "CrossAssetV2Snapshot",
    "build_cross_asset_v2",
    "CROSS_ASSET_V2_UNIVERSE",
    "MarketDiff",
    "compute_market_diff",
    "build_dms_v2",
]
