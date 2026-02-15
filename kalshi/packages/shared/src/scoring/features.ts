/**
 * Pure feature-engineering functions for per-trade anomaly detection.
 * All functions are stateless and unit-testable.
 */

// ─── Robust z-score ──────────────────────────────────────────

export interface Baseline {
  median: number;
  mad: number;       // Median Absolute Deviation
  mean: number;
  std: number;
  count: number;
}

/**
 * Compute z-score of a trade quantity vs rolling baseline.
 * Uses robust MAD-based z-score; falls back to mean/std if MAD is 0.
 * Returns 0 if no baseline exists.
 */
export function tradeSizeZ(qty: number, baseline: Baseline | null): number {
  if (!baseline || baseline.count < 5) return 0;

  // MAD-based z-score: z = 0.6745 * (x - median) / MAD
  // The constant 0.6745 makes MAD consistent with std for normal distributions
  if (baseline.mad > 0) {
    return (0.6745 * (qty - baseline.median)) / baseline.mad;
  }

  // Fallback to mean/std
  if (baseline.std > 0) {
    return (qty - baseline.mean) / baseline.std;
  }

  // All trades same size -- any deviation is notable
  return qty > baseline.median ? 2.0 : 0;
}

// ─── Sweep score ─────────────────────────────────────────────

/**
 * Fraction of top N book levels consumed by a single trade.
 * `bookLevels` is [[price_cents, qty], ...] sorted best-to-worst.
 * Returns 0-1.
 */
export function sweepScore(
  tradeQty: number,
  bookLevels: [number, number][],
  topN: number = 5
): number {
  if (bookLevels.length === 0 || tradeQty <= 0) return 0;

  const levels = bookLevels.slice(0, topN);
  const totalBookQty = levels.reduce((sum, [, q]) => sum + q, 0);
  if (totalBookQty <= 0) return 0;

  return Math.min(tradeQty / totalBookQty, 1.0);
}

// ─── Price impact ────────────────────────────────────────────

/**
 * Price impact in cents: midAfter - midBefore.
 * Positive = price moved up, negative = moved down.
 * Returns null if either mid is unavailable.
 */
export function priceImpact(
  midBefore: number | null,
  midAfter: number | null
): number | null {
  if (midBefore === null || midAfter === null) return null;
  return midAfter - midBefore;
}

// ─── Late factor ─────────────────────────────────────────────

/**
 * Sigmoid function that increases as time-to-close approaches 0.
 * Returns a value in (0, 1).
 *
 * - At T-60m: ~0.02 (negligible)
 * - At T-15m: ~0.27
 * - At T-5m:  ~0.73
 * - At T-1m:  ~0.95
 * - At T-0:   ~0.99
 *
 * If time_to_close_s is null or negative, returns 0.99 (assume imminent).
 */
export function lateFactor(timeToCloseS: number | null): number {
  if (timeToCloseS === null) return 0;
  if (timeToCloseS <= 0) return 0.99;

  // Sigmoid centered at 15 minutes (900s), steepness factor 5
  // f(t) = 1 / (1 + exp((t/900 - 1) * 5))
  const x = (timeToCloseS / 900 - 1) * 5;
  return 1 / (1 + Math.exp(x));
}

// ─── Aggressiveness ──────────────────────────────────────────

/**
 * How aggressively a trade crossed the spread.
 * 0 = passive (at or inside spread), 1 = maximally aggressive (far through).
 *
 * For a "yes" taker: aggressive if trade price >= mid
 * For a "no" taker: aggressive if (100 - trade price) >= (100 - mid) => trade price <= mid
 */
export function aggressiveness(
  tradePriceCents: number,
  mid: number | null,
  spread: number | null,
  takerSide: "yes" | "no"
): number {
  if (mid === null || spread === null || spread <= 0) return 0.5; // unknown

  let distanceThrough: number;
  if (takerSide === "yes") {
    // Taker buying yes -- aggressive if paying above mid
    distanceThrough = tradePriceCents - mid;
  } else {
    // Taker buying no -- aggressive if yes price dropped below mid
    distanceThrough = mid - tradePriceCents;
  }

  // Normalize by spread, clamp to [0, 1]
  const score = 0.5 + (distanceThrough / spread);
  return Math.max(0, Math.min(1, score));
}

// ─── Depth ratio ─────────────────────────────────────────────

/**
 * Trade quantity relative to total top-of-book depth.
 * depth_ratio = qty / (best_bid_qty + best_ask_qty)
 */
export function depthRatio(
  tradeQty: number,
  bestBidQty: number | null,
  bestAskQty: number | null
): number | null {
  if (bestBidQty === null || bestAskQty === null) return null;
  const total = bestBidQty + bestAskQty;
  if (total <= 0) return null;
  return Math.min(tradeQty / total, 10.0); // cap at 10x for scoring
}

// ─── Flow imbalance ──────────────────────────────────────────

/**
 * Aggressive buy volume minus sell volume over a window.
 * Normalized to [-1, 1] by total volume.
 */
export function flowImbalance(
  aggressiveBuyQty: number,
  aggressiveSellQty: number
): number {
  const total = aggressiveBuyQty + aggressiveSellQty;
  if (total <= 0) return 0;
  return (aggressiveBuyQty - aggressiveSellQty) / total;
}

// ─── Novelty ─────────────────────────────────────────────────

/**
 * Score for "first large print in X minutes".
 * Returns 1.0 if no large print in threshold window, 0 otherwise.
 * `minutesSinceLastLarge` = null means no prior large print (maximum novelty).
 */
export function novelty(
  minutesSinceLastLarge: number | null,
  thresholdMinutes: number = 30
): number {
  if (minutesSinceLastLarge === null) return 1.0;
  if (minutesSinceLastLarge >= thresholdMinutes) return 1.0;
  // Linear decay from 1.0 at threshold to 0 at 0 minutes
  return minutesSinceLastLarge / thresholdMinutes;
}

// ─── Baseline computation ────────────────────────────────────

/**
 * Compute rolling baseline stats from an array of trade quantities.
 */
export function computeBaseline(quantities: number[]): Baseline {
  const n = quantities.length;
  if (n === 0) {
    return { median: 0, mad: 0, mean: 0, std: 0, count: 0 };
  }

  const sorted = [...quantities].sort((a, b) => a - b);
  const median = n % 2 === 0
    ? (sorted[n / 2 - 1] + sorted[n / 2]) / 2
    : sorted[Math.floor(n / 2)];

  const deviations = quantities.map((q) => Math.abs(q - median));
  const sortedDev = [...deviations].sort((a, b) => a - b);
  const mad = n % 2 === 0
    ? (sortedDev[n / 2 - 1] + sortedDev[n / 2]) / 2
    : sortedDev[Math.floor(n / 2)];

  const mean = quantities.reduce((s, q) => s + q, 0) / n;
  const variance = quantities.reduce((s, q) => s + (q - mean) ** 2, 0) / n;
  const std = Math.sqrt(variance);

  return { median, mad, mean, std, count: n };
}
