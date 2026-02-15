/**
 * Polymarket REST clients for Gamma API (market discovery),
 * Data API (trade history), and CLOB API (orderbook/prices).
 * All endpoints are public read-only -- no authentication required.
 */

import { polymarketConfig } from "../config.js";
import { logger } from "../logger.js";

// ─── Rate Limiter ──────────────────────────────────────────────

class TokenBucket {
  private tokens: number;
  private lastRefill: number;
  constructor(
    private maxTokens: number,
    private refillPerSecond: number,
  ) {
    this.tokens = maxTokens;
    this.lastRefill = Date.now();
  }

  async take(): Promise<void> {
    this.refill();
    if (this.tokens < 1) {
      const waitMs = ((1 - this.tokens) / this.refillPerSecond) * 1000;
      await new Promise((r) => setTimeout(r, waitMs));
      this.refill();
    }
    this.tokens -= 1;
  }

  private refill() {
    const now = Date.now();
    const elapsed = (now - this.lastRefill) / 1000;
    this.tokens = Math.min(this.maxTokens, this.tokens + elapsed * this.refillPerSecond);
    this.lastRefill = now;
  }
}

// Separate rate limiters per API (different limits)
const gammaLimiter = new TokenBucket(50, 50); // 500 req / 10s = 50/s
const dataLimiter = new TokenBucket(20, 20); // 200 req / 10s = 20/s
const clobLimiter = new TokenBucket(150, 150); // 1500 req / 10s = 150/s

// ─── Generic fetch helper ──────────────────────────────────────

async function polyFetch<T>(url: string, limiter: TokenBucket): Promise<T> {
  await limiter.take();
  const res = await fetch(url, {
    headers: { Accept: "application/json" },
  });
  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Polymarket API ${res.status} ${url}: ${body.slice(0, 200)}`);
  }
  return res.json() as Promise<T>;
}

// ─── Types ─────────────────────────────────────────────────────

/** A single token (Yes or No outcome) within a Polymarket market */
export interface PolyToken {
  token_id: string;
  outcome: string; // "Yes" | "No"
  price?: number;
  winner?: boolean;
}

/** A market (condition) within a Polymarket event */
export interface PolyMarket {
  condition_id: string;
  question: string;
  description?: string;
  tokens: PolyToken[];
  end_date_iso?: string;
  game_start_time?: string;
  active: boolean;
  closed: boolean;
  accepting_orders: boolean;
  // CLOB token IDs
  clobTokenIds?: string[];
  outcomePrices?: string[];
}

/** A top-level event containing 1..N markets */
export interface PolyEvent {
  id: string;
  title: string;
  slug: string;
  description?: string;
  end_date_iso?: string;
  active: boolean;
  closed: boolean;
  markets: PolyMarket[];
  tags?: Array<{ id: string; slug: string; label: string }>;
}

/** Trade from the Data API */
export interface PolyTrade {
  id: string;
  asset_id: string;
  market: string; // condition_id
  side: "BUY" | "SELL";
  price: string;
  size: string;
  timestamp: string;
  transaction_hash: string;
}

/** Orderbook level */
export interface PolyBookLevel {
  price: string;
  size: string;
}

/** Orderbook from the CLOB API */
export interface PolyOrderbook {
  market: string;
  asset_id: string;
  bids: PolyBookLevel[];
  asks: PolyBookLevel[];
  hash: string;
  timestamp: string;
}

// ─── Gamma API (Market Discovery) ──────────────────────────────

/**
 * Fetch active events from the Gamma API with cursor pagination.
 * Returns all active, non-closed events.
 */
export async function getActiveEvents(maxPages = 10): Promise<PolyEvent[]> {
  const allEvents: PolyEvent[] = [];
  let offset = 0;
  const limit = 100;

  for (let page = 0; page < maxPages; page++) {
    const url = `${polymarketConfig.gammaUrl}/events?active=true&closed=false&limit=${limit}&offset=${offset}`;
    try {
      const events = await polyFetch<PolyEvent[]>(url, gammaLimiter);
      if (!Array.isArray(events) || events.length === 0) break;

      allEvents.push(...events);

      if (events.length < limit) break;
      offset += limit;
    } catch (err) {
      logger.error({ err, page, offset }, "Gamma API events fetch failed");
      break;
    }
  }

  logger.info({ count: allEvents.length }, "Fetched Polymarket events from Gamma API");
  return allEvents;
}

// ─── Data API (Trade History) ──────────────────────────────────

/**
 * Fetch recent trades for a specific market (condition_id) from the Data API.
 */
export async function getTradesForMarket(
  conditionId: string,
  limit = 100,
): Promise<PolyTrade[]> {
  const url = `${polymarketConfig.dataUrl}/trades?market=${conditionId}&limit=${limit}`;
  try {
    const data = await polyFetch<PolyTrade[] | { trades?: PolyTrade[] }>(url, dataLimiter);
    // Data API may wrap in an object or return array directly
    if (Array.isArray(data)) return data;
    if (data && Array.isArray(data.trades)) return data.trades;
    return [];
  } catch (err) {
    logger.warn({ err, conditionId }, "Data API trades fetch failed");
    return [];
  }
}

// ─── CLOB API (Orderbook & Prices) ─────────────────────────────

/**
 * Fetch orderbook for a specific token (asset_id).
 */
export async function getOrderbook(tokenId: string): Promise<PolyOrderbook | null> {
  const url = `${polymarketConfig.clobUrl}/book?token_id=${tokenId}`;
  try {
    return await polyFetch<PolyOrderbook>(url, clobLimiter);
  } catch (err) {
    logger.warn({ err, tokenId }, "CLOB orderbook fetch failed");
    return null;
  }
}

/**
 * Fetch midpoint price for a token.
 */
export async function getMidprice(tokenId: string): Promise<number | null> {
  const url = `${polymarketConfig.clobUrl}/midpoint?token_id=${tokenId}`;
  try {
    const data = await polyFetch<{ mid: string }>(url, clobLimiter);
    const mid = parseFloat(data.mid);
    return isNaN(mid) ? null : mid;
  } catch (err) {
    logger.debug({ err, tokenId }, "CLOB midprice fetch failed");
    return null;
  }
}

/**
 * Fetch last trade price for a token.
 */
export async function getLastTradePrice(tokenId: string): Promise<number | null> {
  const url = `${polymarketConfig.clobUrl}/last-trade-price?token_id=${tokenId}`;
  try {
    const data = await polyFetch<{ price: string }>(url, clobLimiter);
    const price = parseFloat(data.price);
    return isNaN(price) ? null : price;
  } catch (err) {
    logger.debug({ err, tokenId }, "CLOB last trade price fetch failed");
    return null;
  }
}
