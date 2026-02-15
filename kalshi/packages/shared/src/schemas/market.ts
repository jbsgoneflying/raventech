import { z } from "zod";

// ─── Kalshi REST response schemas ────────────────────────────

export const KalshiMarketSchema = z.object({
  ticker: z.string(),
  event_ticker: z.string(),
  market_type: z.enum(["binary", "scalar"]).optional(),
  title: z.string().optional(),
  subtitle: z.string().optional(),
  yes_sub_title: z.string().optional(),
  no_sub_title: z.string().optional(),
  status: z.string(),
  created_time: z.string().optional(),
  open_time: z.string().optional(),
  close_time: z.string(),
  expected_expiration_time: z.string().nullish(),
  latest_expiration_time: z.string().optional(),
  yes_bid: z.number().optional(),
  yes_ask: z.number().optional(),
  no_bid: z.number().optional(),
  no_ask: z.number().optional(),
  yes_bid_dollars: z.string().optional(),
  yes_ask_dollars: z.string().optional(),
  no_bid_dollars: z.string().optional(),
  no_ask_dollars: z.string().optional(),
  last_price: z.number().optional(),
  last_price_dollars: z.string().optional(),
  previous_price: z.number().optional(),
  previous_price_dollars: z.string().optional(),
  volume: z.number().optional(),
  volume_24h: z.number().optional(),
  open_interest: z.number().optional(),
  liquidity: z.number().optional(),
  result: z.string().optional(),
  can_close_early: z.boolean().optional(),
  rules_primary: z.string().optional(),
  rules_secondary: z.string().optional(),
  category: z.string().optional(),
  // extensible: new fields from Kalshi won't fail validation
}).passthrough();

export type KalshiMarket = z.infer<typeof KalshiMarketSchema>;

export const KalshiGetMarketsResponseSchema = z.object({
  markets: z.array(KalshiMarketSchema),
  cursor: z.string(),
});

// ─── Internal market row (DB / API) ─────────────────────────

export const MarketRowSchema = z.object({
  ticker: z.string(),
  event_ticker: z.string(),
  title: z.string(),
  yes_sub_title: z.string().nullable(),
  no_sub_title: z.string().nullable(),
  status: z.string(),
  close_time: z.string().nullable(),
  last_price_cents: z.number().nullable(),
  yes_bid_cents: z.number().nullable(),
  yes_ask_cents: z.number().nullable(),
  no_bid_cents: z.number().nullable(),
  no_ask_cents: z.number().nullable(),
  volume: z.number().nullable(),
  open_interest: z.number().nullable(),
  category: z.string().nullable(),
  updated_at: z.string(),
  // Exchange source for multi-exchange support
  exchange: z.enum(["kalshi", "polymarket"]).default("kalshi"),
  // Polymarket-specific fields
  exchange_market_id: z.string().nullable().optional(),
  clob_token_ids: z.record(z.string()).nullable().optional(),
  event_slug: z.string().nullable().optional(),
});

export type MarketRow = z.infer<typeof MarketRowSchema>;
