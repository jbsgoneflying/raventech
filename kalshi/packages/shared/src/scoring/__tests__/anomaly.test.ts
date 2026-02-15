import { describe, it, expect } from "vitest";
import { anomalyScore } from "../anomaly.js";
import type { TradeFeature } from "../../schemas/alert.js";
import { DEFAULT_WEIGHTS } from "../../schemas/config.js";

function makeFeature(overrides: Partial<TradeFeature> = {}): TradeFeature {
  return {
    trade_id: "test-001",
    market_ticker: "TEST-MKT",
    trade_size_z: 0,
    sweep_score: 0,
    price_impact_10s: null,
    price_impact_30s: null,
    time_to_close_s: 86400, // 1 day out
    late_factor: 0,
    aggressiveness: 0.5,
    depth_ratio: null,
    flow_imbalance_1m: 0,
    novelty: 0,
    mid_before: 50,
    trade_price_cents: 50,
    trade_count: 1,
    taker_side: "yes",
    timestamp: new Date().toISOString(),
    ...overrides,
  };
}

describe("anomalyScore", () => {
  it("returns low score for normal trade", () => {
    const f = makeFeature();
    const result = anomalyScore(f, DEFAULT_WEIGHTS);
    expect(result.score).toBeLessThan(30);
  });

  it("returns high score for large late print", () => {
    const f = makeFeature({
      trade_size_z: 6,
      late_factor: 0.95,
      time_to_close_s: 60,
      sweep_score: 0.8,
      depth_ratio: 2.0,
      novelty: 1.0,
      trade_count: 500,
    });
    const result = anomalyScore(f, DEFAULT_WEIGHTS);
    expect(result.score).toBeGreaterThan(60);
    expect(result.alert_type).toBe("LARGE_LATE_PRINT");
    expect(result.reason).toContain("z=6.0");
  });

  it("detects liquidity sweep", () => {
    const f = makeFeature({
      trade_size_z: 3,
      sweep_score: 0.9,
      depth_ratio: 5.0,
    });
    const result = anomalyScore(f, DEFAULT_WEIGHTS);
    expect(result.alert_type).toBe("LIQUIDITY_SWEEP");
  });

  it("detects fast price impact", () => {
    const f = makeFeature({
      price_impact_10s: 8,
      trade_size_z: 1,
    });
    const result = anomalyScore(f, DEFAULT_WEIGHTS);
    expect(result.alert_type).toBe("FAST_PRICE_IMPACT");
  });

  it("detects sustained imbalance", () => {
    const f = makeFeature({
      flow_imbalance_1m: 0.9,
      novelty: 1.0,
    });
    const result = anomalyScore(f, DEFAULT_WEIGHTS);
    expect(result.alert_type).toBe("SUSTAINED_IMBALANCE");
  });

  it("score is always in [0, 100]", () => {
    // Extreme values
    const f = makeFeature({
      trade_size_z: 100,
      late_factor: 1,
      price_impact_10s: 50,
      sweep_score: 1,
      depth_ratio: 10,
      flow_imbalance_1m: 1,
      novelty: 1,
    });
    const result = anomalyScore(f, DEFAULT_WEIGHTS);
    expect(result.score).toBeLessThanOrEqual(100);
    expect(result.score).toBeGreaterThanOrEqual(0);
  });

  it("includes explanation fields", () => {
    const f = makeFeature({ trade_size_z: 3.14159 });
    const result = anomalyScore(f, DEFAULT_WEIGHTS);
    expect(result.explanation.trade_size_z).toBeCloseTo(3.14, 1);
    expect(result.explanation.taker_side).toBe("yes");
  });
});
