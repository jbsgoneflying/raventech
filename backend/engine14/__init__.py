"""Engine 14 — IC Scenario Simulator.

Path-dependent replay of an iron condor over its life, using historical
ORATS option chains as empirical evidence. Given a user's proposed IC
(short put, long put, short call, long call + credit + expiry), the engine:

  1) Finds historical weekly windows with comparable regime/season/macro.
  2) Re-prices the user's IC at each day-in-trade using real chain mids
     snapped to EM-distance-equivalent strikes.
  3) Classifies each analogue outcome (earlyTarget | fullCollect |
     whiteKnuckle | stopOut | breach) and aggregates a distribution.
  4) Surfaces MTM percentile timeline + an optimal exit recommendation.

Phase 1 scope: SPX only, weeklies only, empirical MTM from day one.
"""

from __future__ import annotations

__all__ = [
    "simulate_ic_scenario",
]


def simulate_ic_scenario(*args, **kwargs):
    """Forward to the simulator.run_scenario entrypoint (lazy import to avoid import cycles)."""
    from backend.engine14.simulator import run_scenario
    return run_scenario(*args, **kwargs)
