/**
 * Scoring weight presets and tuning helpers.
 *
 * TUNING GUIDE:
 *
 * The anomaly score is a weighted sum of 5 normalized components:
 *   score = size * w_size + late * w_late + impact * w_impact
 *         + liquidity * w_liq + persistence * w_pers
 *
 * Each component is normalized to [0, 1] via sigmoid functions.
 *
 * Weights should sum to 1.0 for interpretability (score in [0, 100]).
 *
 * PRESETS:
 * - "balanced": Equal-ish weighting, good default.
 * - "size_hunter": Emphasizes large prints over everything else.
 * - "late_flow": Emphasizes late-breaking flow near market close.
 * - "impact_focused": Emphasizes price movement after trades.
 *
 * To tune:
 * 1. Run replay mode over historical data
 * 2. Look at alerts that fire vs ones missed
 * 3. Adjust weights and thresholds via /api/config or env vars
 * 4. Re-run replay to validate
 */

import type { ScoringWeights } from "../schemas/config.js";

export const WEIGHT_PRESETS: Record<string, ScoringWeights> = {
  balanced: {
    size_weight: 0.25,
    late_weight: 0.20,
    impact_weight: 0.20,
    liquidity_weight: 0.20,
    persistence_weight: 0.15,
  },

  size_hunter: {
    size_weight: 0.40,
    late_weight: 0.15,
    impact_weight: 0.15,
    liquidity_weight: 0.20,
    persistence_weight: 0.10,
  },

  late_flow: {
    size_weight: 0.15,
    late_weight: 0.35,
    impact_weight: 0.15,
    liquidity_weight: 0.15,
    persistence_weight: 0.20,
  },

  impact_focused: {
    size_weight: 0.15,
    late_weight: 0.15,
    impact_weight: 0.35,
    liquidity_weight: 0.20,
    persistence_weight: 0.15,
  },
};
