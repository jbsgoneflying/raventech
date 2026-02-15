/**
 * Anomaly scoring: combine per-trade features into a single [0, 100] score
 * and classify alert type.
 */

import type { TradeFeature, AlertType, AlertExplanation } from "../schemas/alert.js";
import type { ScoringWeights } from "../schemas/config.js";
import { DEFAULT_WEIGHTS } from "../schemas/config.js";

// ─── Component normalization ─────────────────────────────────

/** Sigmoid normalization: maps any real value to (0, 1) */
function sigmoid(x: number, center: number = 0, steepness: number = 1): number {
  return 1 / (1 + Math.exp(-steepness * (x - center)));
}

/**
 * Normalize individual feature components to [0, 1] range
 * for composability in the weighted score.
 */
function normalizeComponents(f: TradeFeature) {
  return {
    // Size: z-score > 2 starts getting notable, > 5 is extreme
    size: sigmoid(f.trade_size_z, 2, 1),

    // Late: already in [0, 1]
    late: f.late_factor,

    // Impact: 3+ cents in 10s is notable on a 0-100 scale
    impact: f.price_impact_10s !== null
      ? sigmoid(Math.abs(f.price_impact_10s), 2, 0.5)
      : 0,

    // Liquidity: combine depth_ratio and sweep_score
    liquidity: Math.min(1, (
      (f.depth_ratio !== null ? sigmoid(f.depth_ratio, 0.5, 3) : 0) * 0.5 +
      sigmoid(f.sweep_score, 0.3, 5) * 0.5
    )),

    // Persistence: combine flow imbalance and novelty
    persistence: (
      sigmoid(Math.abs(f.flow_imbalance_1m), 0.3, 3) * 0.6 +
      f.novelty * 0.4
    ),
  };
}

// ─── Main scoring function ───────────────────────────────────

export interface AnomalyResult {
  score: number;          // [0, 100]
  alert_type: AlertType;
  explanation: AlertExplanation;
  reason: string;         // Natural language
}

/**
 * Compute anomaly score for a trade based on its features.
 * Returns score in [0, 100], alert classification, explanation, and reason.
 */
export function anomalyScore(
  features: TradeFeature,
  weights: ScoringWeights = DEFAULT_WEIGHTS,
): AnomalyResult {
  const c = normalizeComponents(features);

  // Weighted combination
  const raw =
    c.size * weights.size_weight +
    c.late * weights.late_weight +
    c.impact * weights.impact_weight +
    c.liquidity * weights.liquidity_weight +
    c.persistence * weights.persistence_weight;

  // Scale to [0, 100]
  const score = Math.round(Math.min(100, Math.max(0, raw * 100)));

  // Classify by dominant factor
  const factors: { type: AlertType; value: number }[] = [
    { type: "LARGE_LATE_PRINT", value: c.size * 0.5 + c.late * 0.5 },
    { type: "LIQUIDITY_SWEEP", value: c.liquidity },
    { type: "FAST_PRICE_IMPACT", value: c.impact },
    { type: "SUSTAINED_IMBALANCE", value: c.persistence },
  ];
  factors.sort((a, b) => b.value - a.value);
  const alert_type = factors[0].type;

  // Build explanation
  const explanation: AlertExplanation = {
    trade_size_z: round2(features.trade_size_z),
    sweep_score: round2(features.sweep_score),
    price_impact_10s: features.price_impact_10s !== null ? round2(features.price_impact_10s) : undefined,
    late_factor: round2(features.late_factor),
    depth_ratio: features.depth_ratio !== null ? round2(features.depth_ratio) : undefined,
    flow_imbalance_1m: round2(features.flow_imbalance_1m),
    aggressiveness: round2(features.aggressiveness),
    novelty: round2(features.novelty),
    time_to_close_s: features.time_to_close_s ?? undefined,
    trade_count: features.trade_count,
    taker_side: features.taker_side,
  };

  // Natural language reason
  const reason = buildReason(features, alert_type);

  return { score, alert_type, explanation, reason };
}

// ─── Reason builder ──────────────────────────────────────────

function buildReason(f: TradeFeature, alertType: AlertType): string {
  const parts: string[] = [];

  switch (alertType) {
    case "LARGE_LATE_PRINT":
      parts.push(`Large late print`);
      parts.push(`z=${f.trade_size_z.toFixed(1)}`);
      if (f.time_to_close_s !== null) {
        parts.push(`T-${formatTimeLeft(f.time_to_close_s)}`);
      }
      parts.push(`${f.trade_count} contracts`);
      break;

    case "LIQUIDITY_SWEEP":
      parts.push(`Liquidity sweep`);
      parts.push(`swept ${(f.sweep_score * 100).toFixed(0)}% of top book`);
      if (f.depth_ratio !== null) {
        parts.push(`depth_ratio=${f.depth_ratio.toFixed(2)}`);
      }
      break;

    case "FAST_PRICE_IMPACT":
      parts.push(`Fast price impact`);
      if (f.price_impact_10s !== null) {
        parts.push(`${f.price_impact_10s > 0 ? "+" : ""}${f.price_impact_10s.toFixed(1)} pts in 10s`);
      }
      break;

    case "SUSTAINED_IMBALANCE":
      parts.push(`Sustained ${f.flow_imbalance_1m > 0 ? "buy" : "sell"} imbalance`);
      parts.push(`flow=${(Math.abs(f.flow_imbalance_1m) * 100).toFixed(0)}%`);
      break;
  }

  // Always append taker side and aggressiveness
  parts.push(`${f.taker_side} side`);
  if (f.aggressiveness > 0.7) {
    parts.push(`aggressive`);
  }

  return parts.join(", ");
}

function formatTimeLeft(seconds: number): string {
  if (seconds < 60) return `${Math.round(seconds)}s`;
  if (seconds < 3600) return `${Math.round(seconds / 60)}m`;
  if (seconds < 86400) return `${(seconds / 3600).toFixed(1)}h`;
  return `${Math.round(seconds / 86400)}d`;
}

function round2(n: number): number {
  return Math.round(n * 100) / 100;
}
