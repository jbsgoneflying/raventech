import { describe, it, expect } from "vitest";
import {
  tradeSizeZ,
  sweepScore,
  priceImpact,
  lateFactor,
  aggressiveness,
  depthRatio,
  flowImbalance,
  novelty,
  computeBaseline,
} from "../features.js";
import type { Baseline } from "../features.js";

describe("tradeSizeZ", () => {
  it("returns 0 when no baseline", () => {
    expect(tradeSizeZ(100, null)).toBe(0);
  });

  it("returns 0 when baseline count < 5", () => {
    const b: Baseline = { median: 10, mad: 2, mean: 10, std: 2, count: 3 };
    expect(tradeSizeZ(100, b)).toBe(0);
  });

  it("computes MAD-based z-score", () => {
    const b: Baseline = { median: 10, mad: 2, mean: 10, std: 3, count: 50 };
    const z = tradeSizeZ(20, b);
    // z = 0.6745 * (20 - 10) / 2 = 3.3725
    expect(z).toBeCloseTo(3.3725, 2);
  });

  it("falls back to mean/std when MAD is 0", () => {
    const b: Baseline = { median: 10, mad: 0, mean: 10, std: 5, count: 50 };
    const z = tradeSizeZ(25, b);
    expect(z).toBeCloseTo(3.0, 2);
  });

  it("returns 2.0 when all same size and trade is larger", () => {
    const b: Baseline = { median: 10, mad: 0, mean: 10, std: 0, count: 50 };
    expect(tradeSizeZ(15, b)).toBe(2.0);
  });
});

describe("sweepScore", () => {
  it("returns 0 for empty book", () => {
    expect(sweepScore(100, [])).toBe(0);
  });

  it("returns fraction of top N levels consumed", () => {
    const levels: [number, number][] = [[50, 20], [49, 30], [48, 50]];
    // Total top 5 (only 3 levels) = 100
    // Trade of 60 => 60/100 = 0.6
    expect(sweepScore(60, levels)).toBeCloseTo(0.6);
  });

  it("caps at 1.0", () => {
    const levels: [number, number][] = [[50, 10]];
    expect(sweepScore(100, levels)).toBe(1.0);
  });
});

describe("priceImpact", () => {
  it("returns null when missing data", () => {
    expect(priceImpact(null, 55)).toBeNull();
    expect(priceImpact(50, null)).toBeNull();
  });

  it("computes positive impact", () => {
    expect(priceImpact(50, 55)).toBe(5);
  });

  it("computes negative impact", () => {
    expect(priceImpact(50, 47)).toBe(-3);
  });
});

describe("lateFactor", () => {
  it("returns 0 when null", () => {
    expect(lateFactor(null)).toBe(0);
  });

  it("returns ~0.99 at close", () => {
    expect(lateFactor(0)).toBeCloseTo(0.99, 1);
  });

  it("is low far from close", () => {
    // 1 hour out
    expect(lateFactor(3600)).toBeLessThan(0.1);
  });

  it("is high at 5 minutes (close to expiry)", () => {
    const f = lateFactor(300);
    // 5 minutes is quite close -- sigmoid should be > 0.9
    expect(f).toBeGreaterThan(0.9);
    expect(f).toBeLessThan(1.0);
  });
});

describe("aggressiveness", () => {
  it("returns 0.5 when mid unknown", () => {
    expect(aggressiveness(55, null, null, "yes")).toBe(0.5);
  });

  it("scores high for yes taker above mid", () => {
    const score = aggressiveness(55, 50, 4, "yes");
    expect(score).toBeGreaterThan(0.7);
  });

  it("scores low for passive fill", () => {
    const score = aggressiveness(48, 50, 4, "yes");
    expect(score).toBeLessThan(0.5);
  });
});

describe("depthRatio", () => {
  it("returns null when missing book data", () => {
    expect(depthRatio(10, null, null)).toBeNull();
  });

  it("computes ratio", () => {
    expect(depthRatio(30, 20, 20)).toBeCloseTo(0.75);
  });

  it("caps at 10.0", () => {
    expect(depthRatio(1000, 5, 5)).toBe(10.0);
  });
});

describe("flowImbalance", () => {
  it("returns 0 for no volume", () => {
    expect(flowImbalance(0, 0)).toBe(0);
  });

  it("returns 1 for all buys", () => {
    expect(flowImbalance(100, 0)).toBe(1);
  });

  it("returns -1 for all sells", () => {
    expect(flowImbalance(0, 100)).toBe(-1);
  });

  it("returns 0 for balanced flow", () => {
    expect(flowImbalance(50, 50)).toBe(0);
  });
});

describe("novelty", () => {
  it("returns 1.0 for no prior large print", () => {
    expect(novelty(null)).toBe(1.0);
  });

  it("returns 1.0 at threshold boundary", () => {
    expect(novelty(30, 30)).toBe(1.0);
  });

  it("returns 0 for immediate repeat", () => {
    expect(novelty(0, 30)).toBe(0);
  });

  it("returns linear interpolation", () => {
    expect(novelty(15, 30)).toBeCloseTo(0.5);
  });
});

describe("computeBaseline", () => {
  it("handles empty array", () => {
    const b = computeBaseline([]);
    expect(b.count).toBe(0);
  });

  it("computes correct stats", () => {
    const b = computeBaseline([1, 2, 3, 4, 5]);
    expect(b.median).toBe(3);
    expect(b.mean).toBe(3);
    expect(b.count).toBe(5);
    expect(b.mad).toBe(1); // MAD of [1,2,3,4,5] from median 3 = [2,1,0,1,2] => median = 1
    expect(b.std).toBeGreaterThan(0);
  });
});
