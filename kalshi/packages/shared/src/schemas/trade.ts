import { z } from "zod";

// ─── Kalshi REST / WS trade schemas ─────────────────────────

export const KalshiTradeSchema = z.object({
  trade_id: z.string(),
  ticker: z.string(),
  yes_price: z.number().optional(),
  no_price: z.number().optional(),
  yes_price_dollars: z.string().optional(),
  no_price_dollars: z.string().optional(),
  count: z.number().optional(),
  count_fp: z.string().optional(),
  taker_side: z.enum(["yes", "no"]),
  created_time: z.string().optional(),
}).passthrough();

export type KalshiTrade = z.infer<typeof KalshiTradeSchema>;

export const KalshiGetTradesResponseSchema = z.object({
  trades: z.array(KalshiTradeSchema),
  cursor: z.string(),
});

// ─── Internal trade event row ────────────────────────────────

export const TradeEventSchema = z.object({
  trade_id: z.string(),
  market_ticker: z.string(),
  yes_price_cents: z.number(),
  no_price_cents: z.number(),
  count: z.number(),
  taker_side: z.enum(["yes", "no"]),
  created_time: z.string(),
  ingested_at: z.string().optional(),
  exchange: z.enum(["kalshi", "polymarket"]).default("kalshi"),
});

export type TradeEvent = z.infer<typeof TradeEventSchema>;
