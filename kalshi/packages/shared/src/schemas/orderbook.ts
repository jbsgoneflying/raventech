import { z } from "zod";

// ─── Kalshi orderbook schemas ────────────────────────────────

/** A single price level: [price_dollars_string, count_fp_string] */
export const BookLevelSchema = z.tuple([z.string(), z.string()]);

export type BookLevel = z.infer<typeof BookLevelSchema>;

export const KalshiOrderbookSchema = z.object({
  orderbook: z.object({
    yes: z.array(z.tuple([z.number(), z.number()])).optional(),
    no: z.array(z.tuple([z.number(), z.number()])).optional(),
    yes_dollars: z.array(BookLevelSchema).optional(),
    no_dollars: z.array(BookLevelSchema).optional(),
  }).optional(),
  orderbook_fp: z.object({
    yes_dollars: z.array(BookLevelSchema).optional(),
    no_dollars: z.array(BookLevelSchema).optional(),
  }).optional(),
}).passthrough();

export type KalshiOrderbook = z.infer<typeof KalshiOrderbookSchema>;

// ─── Internal book state ─────────────────────────────────────

export const BookStateSchema = z.object({
  market_ticker: z.string(),
  /** yes bids: [[price_cents, qty], ...] sorted best-to-worst */
  yes_bids: z.array(z.tuple([z.number(), z.number()])),
  /** no bids: [[price_cents, qty], ...] sorted best-to-worst */
  no_bids: z.array(z.tuple([z.number(), z.number()])),
  /** Derived: best yes bid */
  best_yes_bid: z.number().nullable(),
  /** Derived: best yes ask = 100 - best no bid */
  best_yes_ask: z.number().nullable(),
  /** Derived: mid = (best_yes_bid + best_yes_ask) / 2 */
  mid: z.number().nullable(),
  captured_at: z.string(),
});

export type BookState = z.infer<typeof BookStateSchema>;

/**
 * Parse Kalshi orderbook response into internal BookState.
 * Kalshi returns yes_bids and no_bids (not asks).
 * yes_ask at price X = no_bid at price (100-X).
 */
export function parseOrderbook(ticker: string, raw: KalshiOrderbook): BookState {
  const now = new Date().toISOString();

  // Prefer fp (fixed-point) format
  const fp = raw.orderbook_fp;
  const legacy = raw.orderbook;

  let yesBids: [number, number][] = [];
  let noBids: [number, number][] = [];

  if (fp?.yes_dollars && fp.yes_dollars.length > 0) {
    yesBids = fp.yes_dollars.map(([p, q]) => [
      Math.round(parseFloat(p) * 100),
      parseFloat(q),
    ]);
    noBids = (fp.no_dollars ?? []).map(([p, q]) => [
      Math.round(parseFloat(p) * 100),
      parseFloat(q),
    ]);
  } else if (legacy?.yes) {
    yesBids = legacy.yes.map(([p, q]) => [p, q]);
    noBids = (legacy.no ?? []).map(([p, q]) => [p, q]);
  }

  // Sort best-to-worst (highest price first for bids)
  yesBids.sort((a, b) => b[0] - a[0]);
  noBids.sort((a, b) => b[0] - a[0]);

  const bestYesBid = yesBids.length > 0 ? yesBids[0][0] : null;
  // Best yes ask = 100 - best no bid price
  const bestNoBid = noBids.length > 0 ? noBids[0][0] : null;
  const bestYesAsk = bestNoBid !== null ? 100 - bestNoBid : null;

  const mid = bestYesBid !== null && bestYesAsk !== null
    ? (bestYesBid + bestYesAsk) / 2
    : null;

  return {
    market_ticker: ticker,
    yes_bids: yesBids,
    no_bids: noBids,
    best_yes_bid: bestYesBid,
    best_yes_ask: bestYesAsk,
    mid,
    captured_at: now,
  };
}
