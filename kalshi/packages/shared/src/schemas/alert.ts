import { z } from "zod";

// ─── Alert types ─────────────────────────────────────────────

export const AlertType = z.enum([
  "LARGE_LATE_PRINT",
  "LIQUIDITY_SWEEP",
  "FAST_PRICE_IMPACT",
  "SUSTAINED_IMBALANCE",
]);

export type AlertType = z.infer<typeof AlertType>;

// ─── Feature vector computed per trade ───────────────────────

export const TradeFeatureSchema = z.object({
  trade_id: z.string(),
  market_ticker: z.string(),
  trade_size_z: z.number(),
  sweep_score: z.number(),
  price_impact_10s: z.number().nullable(),
  price_impact_30s: z.number().nullable(),
  time_to_close_s: z.number().nullable(),
  late_factor: z.number(),
  aggressiveness: z.number(),
  depth_ratio: z.number().nullable(),
  flow_imbalance_1m: z.number(),
  novelty: z.number(),
  mid_before: z.number().nullable(),
  trade_price_cents: z.number(),
  trade_count: z.number(),
  taker_side: z.enum(["yes", "no"]),
  timestamp: z.string(),
});

export type TradeFeature = z.infer<typeof TradeFeatureSchema>;

// ─── Alert explanation ───────────────────────────────────────

export const AlertExplanationSchema = z.object({
  trade_size_z: z.number().optional(),
  sweep_score: z.number().optional(),
  price_impact_10s: z.number().optional(),
  late_factor: z.number().optional(),
  depth_ratio: z.number().optional(),
  flow_imbalance_1m: z.number().optional(),
  aggressiveness: z.number().optional(),
  novelty: z.number().optional(),
  time_to_close_s: z.number().optional(),
  trade_count: z.number().optional(),
  taker_side: z.string().optional(),
});

export type AlertExplanation = z.infer<typeof AlertExplanationSchema>;

// ─── Alert row ───────────────────────────────────────────────

export const AlertSchema = z.object({
  id: z.string(),
  market_ticker: z.string(),
  alert_type: AlertType,
  anomaly_score: z.number(),
  trade_id: z.string().nullable(),
  explanation: AlertExplanationSchema,
  reason: z.string(),
  created_at: z.string(),
  // Joined fields for API responses
  market_title: z.string().optional(),
  close_time: z.string().optional(),
  last_price_cents: z.number().optional(),
  exchange: z.enum(["kalshi", "polymarket"]).default("kalshi"),
});

export type Alert = z.infer<typeof AlertSchema>;
