"""Engine 2 v2 — SPX Iron Condor Command Deck.

Transforms E2 from a scan-page-with-a-hidden-advisor into a
"where do I place my weekly wings?" Command Deck for index ICs. The
desk is assumed to be writing an SPX / SPY / QQQ weekly iron condor;
this engine ranks candidate placements by a deterministic composite
score that combines historical breach stats, Monte Carlo forward
simulation, empirical intraweek MAE, theta capture, and credit
richness. MI v2 regime is the single source of truth across the
scan and the open-trade tracker.

Public entry points:

- :func:`score_placements` — fit a grid of (EM-mult × wing-width)
  candidates against the historical + MC + MAE pools, return ranked
  :class:`PlacementScore` objects.
- :func:`build_wing_console` — full pipeline: given an SPX IC engine
  payload + trading context, produce a :class:`WingConsolePayload`
  ready for the frontend + LLM advisor.
- :func:`run_mc_for_placement` — thin adapter around the MC
  simulator that scores one placement on demand (used by the slider
  exact-scoring endpoint).

All scoring is deterministic and cacheable; the LLM advisor at
``POST /api/spx-ic/advisor`` is a narrative layer on top of this
pure-Python truth.
"""
from __future__ import annotations

from backend.engine2.mae_proxy import (
    MAEDistribution,
    compute_mae_distribution,
)
from backend.engine2.mc_simulator import (
    MCResult,
    run_weekly_mc,
)
from backend.engine2.scoring_context import (
    ScoringContext,
    get_scoring_context,
    store_scoring_context,
)
from backend.engine2.shared_cache import (
    clear as clear_command_deck_cache,
    get_or_compute_command_deck,
    get_stats_snapshot as get_command_deck_cache_stats,
    reset_stats as reset_command_deck_cache_stats,
)
from backend.engine2.wing_console import (
    DEFAULT_WEIGHTS,
    PlacementScore,
    WingConsolePayload,
    WingConsoleWeights,
    build_wing_console,
    run_mc_for_placement,
    score_placements,
    score_single_placement,
)

__all__ = [
    "DEFAULT_WEIGHTS",
    "MAEDistribution",
    "MCResult",
    "PlacementScore",
    "ScoringContext",
    "WingConsolePayload",
    "WingConsoleWeights",
    "build_wing_console",
    "clear_command_deck_cache",
    "compute_mae_distribution",
    "get_command_deck_cache_stats",
    "get_or_compute_command_deck",
    "get_scoring_context",
    "reset_command_deck_cache_stats",
    "run_mc_for_placement",
    "run_weekly_mc",
    "score_placements",
    "score_single_placement",
    "store_scoring_context",
]
