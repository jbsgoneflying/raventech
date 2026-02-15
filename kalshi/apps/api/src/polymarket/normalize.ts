/**
 * Polymarket data normalization layer.
 * Maps Polymarket-specific data formats to our exchange-agnostic internal model.
 */

import { createHash } from "crypto";
import { getRedis } from "../services/redis-windows.js";
import { logger } from "../logger.js";
import type { IncomingTrade } from "../services/feature-engine.js";
import type { RedisBookState } from "../services/redis-windows.js";
import type { PolyEvent, PolyMarket } from "./rest.js";

// ─── Redis Token Map ───────────────────────────────────────────

const TOKEN_MAP_KEY = "pm:token_map";

/**
 * Populate the Redis token map with asset_id -> condition_id mappings.
 * Called during market discovery.
 */
export async function populateTokenMap(
  entries: Array<{ assetId: string; conditionId: string }>,
): Promise<void> {
  const redis = getRedis();
  if (!redis || entries.length === 0) return;

  const pipeline = redis.pipeline();
  for (const { assetId, conditionId } of entries) {
    pipeline.hset(TOKEN_MAP_KEY, assetId, conditionId);
  }
  await pipeline.exec();

  logger.debug({ count: entries.length }, "Token map populated");
}

/**
 * Resolve an asset_id (token ID) to its condition_id (market ticker).
 */
export async function resolveTokenToMarket(assetId: string): Promise<string | null> {
  const redis = getRedis();
  if (!redis) return null;
  return redis.hget(TOKEN_MAP_KEY, assetId);
}

// ─── Trade Normalization ───────────────────────────────────────

/**
 * Generate a deterministic trade ID for Polymarket WS trades
 * (which lack explicit IDs). Uses SHA-256 hash of key fields, truncated.
 */
export function generateTradeId(
  assetId: string,
  timestamp: string,
  price: string,
  size: string,
): string {
  const input = `${assetId}:${timestamp}:${price}:${size}`;
  return createHash("sha256").update(input).digest("hex").slice(0, 24);
}

/**
 * Normalize a Polymarket `last_trade_price` WebSocket event
 * into our internal IncomingTrade format.
 */
export async function normalizePolyTrade(
  msg: Record<string, unknown>,
): Promise<IncomingTrade | null> {
  const assetId = msg.asset_id as string;
  const price = msg.price as string;
  const size = msg.size as string;
  const side = msg.side as string; // "BUY" | "SELL"
  const market = msg.market as string; // condition_id (sometimes present)

  if (!assetId || !price || !size) {
    logger.debug({ msg }, "Incomplete Polymarket trade, skipping");
    return null;
  }

  // Resolve asset_id to condition_id (market ticker)
  let conditionId = market || null;
  if (!conditionId) {
    conditionId = await resolveTokenToMarket(assetId);
  }
  if (!conditionId) {
    logger.debug({ assetId }, "Could not resolve Polymarket asset_id to market");
    return null;
  }

  const priceFloat = parseFloat(price);
  const sizeFloat = parseFloat(size);
  const yesPriceCents = Math.round(priceFloat * 100);
  const timestamp = (msg.timestamp as string) ?? new Date().toISOString();

  // BUY on Yes token = "yes" taker, SELL on Yes token = "no" taker
  // We assume events from the Yes token by default
  const takerSide: "yes" | "no" = side === "SELL" ? "no" : "yes";

  const tradeId = `pm_${generateTradeId(assetId, timestamp, price, size)}`;

  return {
    trade_id: tradeId,
    market_ticker: conditionId,
    yes_price_cents: yesPriceCents,
    no_price_cents: 100 - yesPriceCents,
    count: Math.max(1, Math.round(sizeFloat)),
    taker_side: takerSide,
    created_time: timestamp,
  };
}

// ─── Book Normalization ────────────────────────────────────────

/**
 * Normalize a Polymarket `book` WebSocket event into our RedisBookState.
 */
export function normalizePolyBook(
  msg: Record<string, unknown>,
): { ticker: string; state: RedisBookState } | null {
  const market = msg.market as string;
  const bidsRaw = msg.bids as Array<{ price: string; size: string }> | undefined;
  const asksRaw = msg.asks as Array<{ price: string; size: string }> | undefined;

  if (!market) return null;

  // Parse bids: convert price strings to cents, size to numbers
  const yesBids: [number, number][] = (bidsRaw ?? [])
    .map((b) => [Math.round(parseFloat(b.price) * 100), parseFloat(b.size)] as [number, number])
    .filter(([p]) => !isNaN(p))
    .sort((a, b) => b[0] - a[0]);

  // Parse asks and convert to no_bids: no_bid_price = 100 - ask_price
  const noBids: [number, number][] = (asksRaw ?? [])
    .map((a) => [100 - Math.round(parseFloat(a.price) * 100), parseFloat(a.size)] as [number, number])
    .filter(([p]) => !isNaN(p))
    .sort((a, b) => b[0] - a[0]);

  const bestYesBid = yesBids.length > 0 ? yesBids[0][0] : null;
  const bestNoBid = noBids.length > 0 ? noBids[0][0] : null;
  const bestYesAsk = bestNoBid !== null ? 100 - bestNoBid : null;
  const mid = bestYesBid !== null && bestYesAsk !== null ? (bestYesBid + bestYesAsk) / 2 : null;

  return {
    ticker: market,
    state: {
      mid,
      best_yes_bid: bestYesBid,
      best_yes_ask: bestYesAsk,
      best_yes_bid_qty: yesBids.length > 0 ? yesBids[0][1] : null,
      best_yes_ask_qty: noBids.length > 0 ? noBids[0][1] : null,
      yes_bids_json: JSON.stringify(yesBids.slice(0, 10)),
      no_bids_json: JSON.stringify(noBids.slice(0, 10)),
    },
  };
}

// ─── Market Normalization ──────────────────────────────────────

/**
 * Normalize a Polymarket event + market into fields suitable for our DB schema.
 */
export function normalizePolyMarket(
  event: PolyEvent,
  market: PolyMarket,
): {
  ticker: string;
  event_ticker: string;
  title: string;
  yes_sub_title: string | null;
  no_sub_title: string | null;
  status: string;
  close_time: Date | null;
  last_price_cents: number | null;
  yes_bid_cents: number | null;
  yes_ask_cents: number | null;
  no_bid_cents: number | null;
  no_ask_cents: number | null;
  volume: number | null;
  open_interest: number | null;
  category: string | null;
  exchange: "polymarket";
  exchange_market_id: string;
  clob_token_ids: Record<string, string>;
  event_slug: string;
} {
  // Map tokens: find Yes and No token IDs
  const yesToken = market.tokens?.find((t) => t.outcome === "Yes");
  const noToken = market.tokens?.find((t) => t.outcome === "No");

  const clobTokenIds: Record<string, string> = {};
  if (yesToken) clobTokenIds.yes = yesToken.token_id;
  if (noToken) clobTokenIds.no = noToken.token_id;

  // Use clobTokenIds array if tokens aren't populated
  if (!clobTokenIds.yes && market.clobTokenIds && market.clobTokenIds.length > 0) {
    clobTokenIds.yes = market.clobTokenIds[0];
  }
  if (!clobTokenIds.no && market.clobTokenIds && market.clobTokenIds.length > 1) {
    clobTokenIds.no = market.clobTokenIds[1];
  }

  // Parse outcome prices
  let yesPriceCents: number | null = null;
  if (market.outcomePrices && market.outcomePrices.length > 0) {
    const p = parseFloat(market.outcomePrices[0]);
    if (!isNaN(p)) yesPriceCents = Math.round(p * 100);
  } else if (yesToken?.price != null) {
    yesPriceCents = Math.round(yesToken.price * 100);
  }

  // Determine close time from event or market
  const endDateStr = market.end_date_iso ?? event.end_date_iso ?? null;
  const closeTime = endDateStr ? new Date(endDateStr) : null;

  // Status mapping
  let status: string;
  if (market.closed) {
    status = "closed";
  } else if (market.active && market.accepting_orders) {
    status = "open";
  } else {
    status = "inactive";
  }

  // Category from tags
  const category = event.tags && event.tags.length > 0
    ? event.tags[0].label
    : null;

  return {
    // Use condition_id as the ticker (primary key in our DB)
    ticker: market.condition_id,
    event_ticker: event.id,
    title: market.question || event.title,
    yes_sub_title: "Yes",
    no_sub_title: "No",
    status,
    close_time: closeTime,
    last_price_cents: yesPriceCents,
    yes_bid_cents: null, // Populated by WS book events
    yes_ask_cents: null,
    no_bid_cents: null,
    no_ask_cents: null,
    volume: null, // Polymarket doesn't expose aggregate volume easily
    open_interest: null,
    category,
    exchange: "polymarket",
    exchange_market_id: market.condition_id,
    clob_token_ids: clobTokenIds,
    event_slug: event.slug,
  };
}
