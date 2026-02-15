/**
 * Kalshi REST API client.
 * Typed wrappers around fetch with auth signing and rate limiting.
 */

import {
  KalshiGetMarketsResponseSchema,
  KalshiGetTradesResponseSchema,
  KalshiOrderbookSchema,
  type KalshiMarket,
  type KalshiTrade,
  type KalshiOrderbook,
} from "@kalshi-monitor/shared";
import { kalshiConfig } from "../config.js";
import { createAuthHeaders, isAuthAvailable } from "./auth.js";
import { logger } from "../logger.js";

// ─── Rate limiter (token bucket) ─────────────────────────────

class TokenBucket {
  private tokens: number;
  private lastRefill: number;

  constructor(
    private readonly maxTokens: number = 15,
    private readonly refillRate: number = 15, // tokens per second
  ) {
    this.tokens = maxTokens;
    this.lastRefill = Date.now();
  }

  async acquire(): Promise<void> {
    this.refill();
    if (this.tokens <= 0) {
      const waitMs = (1 / this.refillRate) * 1000;
      await new Promise((r) => setTimeout(r, waitMs));
      this.refill();
    }
    this.tokens--;
  }

  private refill() {
    const now = Date.now();
    const elapsed = (now - this.lastRefill) / 1000;
    this.tokens = Math.min(this.maxTokens, this.tokens + elapsed * this.refillRate);
    this.lastRefill = now;
  }
}

const bucket = new TokenBucket(15, 15);

// ─── Base request ────────────────────────────────────────────

async function kalshiGet<T>(
  path: string,
  needsAuth: boolean = false,
): Promise<T> {
  if (needsAuth && !isAuthAvailable()) {
    throw new Error(`Auth required for ${path} but no API key configured`);
  }

  await bucket.acquire();

  const url = `${kalshiConfig.baseUrl}/trade-api/v2${path}`;
  const authHeaders = needsAuth ? createAuthHeaders("GET", `/trade-api/v2${path}`) : {};

  const controller = new AbortController();
  const timeout = setTimeout(() => controller.abort(), 30_000);

  const res = await fetch(url, {
    method: "GET",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
      ...authHeaders,
    },
    signal: controller.signal,
  });

  clearTimeout(timeout);

  if (!res.ok) {
    const body = await res.text().catch(() => "");
    throw new Error(`Kalshi API ${res.status} on GET ${path}: ${body}`);
  }

  return res.json() as Promise<T>;
}

// ─── Markets ─────────────────────────────────────────────────

export interface GetMarketsParams {
  status?: "unopened" | "open" | "closed" | "settled";
  limit?: number;
  cursor?: string;
  event_ticker?: string;
  series_ticker?: string;
}

export async function getMarkets(params: GetMarketsParams = {}): Promise<{
  markets: KalshiMarket[];
  cursor: string;
}> {
  const qs = new URLSearchParams();
  if (params.status) qs.set("status", params.status);
  if (params.limit) qs.set("limit", params.limit.toString());
  if (params.cursor) qs.set("cursor", params.cursor);
  if (params.event_ticker) qs.set("event_ticker", params.event_ticker);
  if (params.series_ticker) qs.set("series_ticker", params.series_ticker);

  const path = `/markets${qs.toString() ? "?" + qs.toString() : ""}`;
  const raw = await kalshiGet(path, isAuthAvailable());
  const parsed = KalshiGetMarketsResponseSchema.safeParse(raw);

  if (!parsed.success) {
    logger.warn({ errors: parsed.error.issues }, "Kalshi markets response validation warning");
    // Return raw data anyway (passthrough schema)
    const fallback = raw as { markets: KalshiMarket[]; cursor: string };
    return fallback;
  }

  return parsed.data;
}

/**
 * Fetch ALL open markets, paginating automatically.
 */
export async function getAllOpenMarkets(): Promise<KalshiMarket[]> {
  const all: KalshiMarket[] = [];
  let cursor = "";

  do {
    const result = await getMarkets({ status: "open", limit: 1000, cursor: cursor || undefined });
    all.push(...result.markets);
    cursor = result.cursor;
  } while (cursor);

  return all;
}

// ─── Trades ──────────────────────────────────────────────────

export interface GetTradesParams {
  ticker?: string;
  min_ts?: number;
  max_ts?: number;
  limit?: number;
  cursor?: string;
}

export async function getTrades(params: GetTradesParams = {}): Promise<{
  trades: KalshiTrade[];
  cursor: string;
}> {
  const qs = new URLSearchParams();
  if (params.ticker) qs.set("ticker", params.ticker);
  if (params.min_ts) qs.set("min_ts", params.min_ts.toString());
  if (params.max_ts) qs.set("max_ts", params.max_ts.toString());
  if (params.limit) qs.set("limit", params.limit.toString());
  if (params.cursor) qs.set("cursor", params.cursor);

  const path = `/markets/trades${qs.toString() ? "?" + qs.toString() : ""}`;
  const raw = await kalshiGet(path, false);
  const parsed = KalshiGetTradesResponseSchema.safeParse(raw);

  if (!parsed.success) {
    logger.warn({ errors: parsed.error.issues }, "Kalshi trades response validation warning");
    const fallback = raw as { trades: KalshiTrade[]; cursor: string };
    return fallback;
  }

  return parsed.data;
}

// ─── Orderbook ───────────────────────────────────────────────

export async function getOrderbook(
  ticker: string,
  depth: number = 10,
): Promise<KalshiOrderbook | null> {
  if (!isAuthAvailable()) {
    logger.debug("Skipping orderbook fetch -- no auth");
    return null;
  }

  try {
    const path = `/markets/${ticker}/orderbook?depth=${depth}`;
    const raw = await kalshiGet(path, true);
    const parsed = KalshiOrderbookSchema.safeParse(raw);

    if (!parsed.success) {
      logger.warn({ ticker, errors: parsed.error.issues }, "Orderbook validation warning");
      return raw as KalshiOrderbook;
    }

    return parsed.data;
  } catch (err) {
    logger.warn({ ticker, err }, "Failed to fetch orderbook");
    return null;
  }
}
