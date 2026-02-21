/**
 * Ingestion service: processes live WebSocket events and REST-polled data.
 * Persists to Postgres, updates Redis, feeds feature + alert engines.
 * Supports both Kalshi and Polymarket exchanges.
 */

import { db, schema } from "../db/index.js";
import { KalshiWsClient } from "../kalshi/ws.js";
import { isAuthAvailable } from "../kalshi/auth.js";
import { PolymarketWsClient } from "../polymarket/ws.js";
import { normalizePolyTrade, normalizePolyBook } from "../polymarket/normalize.js";
import { polymarketConfig } from "../config.js";
import { parseOrderbook, type BookState } from "@kalshi-monitor/shared";
import { setBookState, type RedisBookState } from "./redis-windows.js";
import { computeFeatures, scheduleImpactCheck, type IncomingTrade } from "./feature-engine.js";
import { evaluateTrade } from "./alert-engine.js";
import { sseBroadcaster } from "./sse.js";
import { logger } from "../logger.js";
import { eq } from "drizzle-orm";

let wsClient: KalshiWsClient | null = null;
let polyWsClient: PolymarketWsClient | null = null;
let _tradesIngested = 0;
let _alertsGenerated = 0;

export function getIngestionStats() {
  return {
    tradesIngested: _tradesIngested,
    alertsGenerated: _alertsGenerated,
    ws: wsClient?.stats ?? { connected: false, reconnects: 0, messagesReceived: 0 },
    polyWs: polyWsClient?.stats ?? { connected: false, reconnects: 0, messagesReceived: 0 },
    sseClients: sseBroadcaster.clientCount,
  };
}

/**
 * Start the live ingestion pipeline.
 */
export async function startIngestion(): Promise<KalshiWsClient> {
  wsClient = new KalshiWsClient();

  // ─── Trade handler ───────────────────────────────────────

  wsClient.on("trade", async (msg: Record<string, unknown>) => {
    try {
      const trade = normalizeWsTrade(msg);
      if (!trade) return;
      await processTrade(trade, "kalshi");
    } catch (err) {
      logger.error({ err, msg }, "Error processing Kalshi trade");
    }
  });

  // ─── Ticker handler ──────────────────────────────────────

  wsClient.on("ticker", async (msg: Record<string, unknown>) => {
    try {
      const ticker = (msg.market_ticker ?? msg.ticker) as string;
      if (!ticker) return;

      // Update market in DB
      const updates: Record<string, unknown> = { updated_at: new Date() };
      if (msg.yes_bid !== undefined) updates.yes_bid_cents = msg.yes_bid as number;
      if (msg.yes_ask !== undefined) updates.yes_ask_cents = msg.yes_ask as number;
      if (msg.no_bid !== undefined) updates.no_bid_cents = msg.no_bid as number;
      if (msg.no_ask !== undefined) updates.no_ask_cents = msg.no_ask as number;
      if (msg.last_price !== undefined) updates.last_price_cents = msg.last_price as number;
      if (msg.volume !== undefined) updates.volume = msg.volume as number;
      if (msg.open_interest !== undefined) updates.open_interest = msg.open_interest as number;

      await db
        .update(schema.markets)
        .set(updates)
        .where(eq(schema.markets.ticker, ticker))
        .catch(() => {}); // Ignore if market not yet in DB

      // Broadcast to SSE
      sseBroadcaster.broadcastTicker(ticker, msg);
    } catch (err) {
      logger.debug({ err }, "Ticker update error");
    }
  });

  // ─── Orderbook snapshot handler ──────────────────────────

  wsClient.on("orderbook_snapshot", async (msg: Record<string, unknown>) => {
    try {
      const ticker = msg.market_ticker as string;
      if (!ticker) return;

      const yesBids = (msg.yes ?? []) as [number, number][];
      const noBids = (msg.no ?? []) as [number, number][];

      // Update Redis book state
      const sorted_yes = [...yesBids].sort((a, b) => b[0] - a[0]);
      const sorted_no = [...noBids].sort((a, b) => b[0] - a[0]);

      const bestYesBid = sorted_yes.length > 0 ? sorted_yes[0][0] : null;
      const bestNoBid = sorted_no.length > 0 ? sorted_no[0][0] : null;
      const bestYesAsk = bestNoBid !== null ? 100 - bestNoBid : null;
      const mid = bestYesBid !== null && bestYesAsk !== null ? (bestYesBid + bestYesAsk) / 2 : null;

      const redisState: RedisBookState = {
        mid,
        best_yes_bid: bestYesBid,
        best_yes_ask: bestYesAsk,
        best_yes_bid_qty: sorted_yes.length > 0 ? sorted_yes[0][1] : null,
        best_yes_ask_qty: sorted_no.length > 0 ? sorted_no[0][1] : null,
        yes_bids_json: JSON.stringify(sorted_yes.slice(0, 10)),
        no_bids_json: JSON.stringify(sorted_no.slice(0, 10)),
      };

      await setBookState(ticker, redisState);

      logger.debug({ ticker, levels: { yes: sorted_yes.length, no: sorted_no.length } }, "Orderbook snapshot processed");
    } catch (err) {
      logger.debug({ err }, "Orderbook snapshot error");
    }
  });

  // ─── Orderbook delta handler ─────────────────────────────

  wsClient.on("orderbook_delta", async (msg: Record<string, unknown>) => {
    // Delta updates are applied to the existing book state in Redis
    // For now we log; full delta application requires maintaining full book
    logger.debug({ msg }, "Orderbook delta (incremental update)");
  });

  // ─── Safety-net error listener (prevent process crash) ───

  wsClient.on("error", (err: unknown) => {
    logger.error({ err }, "Kalshi WS client error (handled, will reconnect)");
  });

  // ─── Connect and subscribe ───────────────────────────────

  try {
    await wsClient.connect();
  } catch (err) {
    logger.error({ err }, "Initial WS connection failed, will retry...");
  }

  // Subscribe to public channels (trade + ticker for all markets)
  wsClient.subscribe(["trade"]);
  wsClient.subscribe(["ticker"]);

  // Subscribe to orderbook_delta if authenticated
  if (isAuthAvailable()) {
    logger.info("Auth available: subscribing to orderbook_delta");
    // We'll subscribe to specific markets after discovery
    // For now subscribe without market filter (if supported) or wait
  }

  return wsClient;
}

// ─── Trade normalization ─────────────────────────────────────

function normalizeWsTrade(msg: Record<string, unknown>): IncomingTrade | null {
  const trade_id = msg.trade_id as string;
  const ticker = (msg.market_ticker ?? msg.ticker) as string;
  const taker_side = msg.taker_side as "yes" | "no";

  if (!trade_id || !ticker || !taker_side) {
    logger.debug({ msg }, "Incomplete trade message, skipping");
    return null;
  }

  const yes_price = (msg.yes_price as number) ?? 0;
  const no_price = (msg.no_price as number) ?? (100 - yes_price);
  const count = (msg.count as number) ?? 1;
  const created_time = (msg.created_time as string) ?? new Date().toISOString();

  return {
    trade_id,
    market_ticker: ticker,
    yes_price_cents: yes_price,
    no_price_cents: no_price,
    count,
    taker_side,
    created_time,
  };
}

/**
 * Subscribe to orderbook updates for specific high-interest markets.
 */
export function subscribeToOrderbooks(tickers: string[]): void {
  if (!wsClient || !isAuthAvailable()) return;
  if (tickers.length === 0) return;

  wsClient.subscribe(["orderbook_delta"], tickers);
  logger.info({ count: tickers.length }, "Subscribed to orderbook_delta for markets");
}

// ═══════════════════════════════════════════════════════════════
// Polymarket Ingestion
// ═══════════════════════════════════════════════════════════════

/**
 * Process a normalized trade through the shared pipeline.
 * Used by both Kalshi and Polymarket.
 */
const MIN_TRADE_COUNT = 10; // Skip small trades — whale detection only

async function processTrade(trade: IncomingTrade, exchange: "kalshi" | "polymarket"): Promise<void> {
  // Skip tiny trades that can't be whales
  if (trade.count < MIN_TRADE_COUNT) return;

  // Idempotent insert (skip if trade_id already exists)
  try {
    await db.insert(schema.tradeEvents).values({
      trade_id: trade.trade_id,
      market_ticker: trade.market_ticker,
      yes_price_cents: trade.yes_price_cents,
      no_price_cents: trade.no_price_cents,
      count: trade.count,
      taker_side: trade.taker_side,
      created_time: new Date(trade.created_time),
      exchange,
    }).onConflictDoNothing();
  } catch (err) {
    logger.debug({ tradeId: trade.trade_id, ticker: trade.market_ticker }, "Trade insert skipped (likely unknown market)");
    return;
  }

  _tradesIngested++;

  // Broadcast raw trade to SSE clients
  sseBroadcaster.broadcastTrade({
    trade_id: trade.trade_id,
    market_ticker: trade.market_ticker,
    yes_price_cents: trade.yes_price_cents,
    count: trade.count,
    taker_side: trade.taker_side,
    created_time: trade.created_time,
  });

  // Compute features
  const features = await computeFeatures(trade);

  // Schedule delayed price impact checks
  if (features.mid_before !== null) {
    scheduleImpactCheck(trade.market_ticker, features.mid_before, 10_000).then((impact10s) => {
      if (impact10s !== null) {
        features.price_impact_10s = impact10s;
      }
    });
    scheduleImpactCheck(trade.market_ticker, features.mid_before, 30_000).then((impact30s) => {
      if (impact30s !== null) {
        features.price_impact_30s = impact30s;
      }
    });
  }

  // Evaluate for alerts (using features available now)
  const alert = await evaluateTrade(features);
  if (alert) {
    _alertsGenerated++;
  }
}

/**
 * Start the Polymarket live ingestion pipeline.
 */
export async function startPolymarketIngestion(): Promise<PolymarketWsClient> {
  polyWsClient = new PolymarketWsClient();

  // ─── Trade handler ───────────────────────────────────────
  polyWsClient.on("trade", async (msg: Record<string, unknown>) => {
    try {
      const trade = await normalizePolyTrade(msg);
      if (!trade) return;
      await processTrade(trade, "polymarket");
    } catch (err) {
      logger.error({ err, msg }, "Error processing Polymarket trade");
    }
  });

  // ─── Book handler ────────────────────────────────────────
  polyWsClient.on("book", async (msg: Record<string, unknown>) => {
    try {
      const result = normalizePolyBook(msg);
      if (!result) return;
      await setBookState(result.ticker, result.state);
      logger.debug({ ticker: result.ticker }, "Polymarket book snapshot processed");
    } catch (err) {
      logger.debug({ err }, "Polymarket book error");
    }
  });

  // ─── Price change handler ────────────────────────────────
  polyWsClient.on("price_change", async (msg: Record<string, unknown>) => {
    try {
      const market = msg.market as string;
      if (!market) return;

      const updates: Record<string, unknown> = { updated_at: new Date() };
      if (msg.best_bid !== undefined) {
        updates.yes_bid_cents = Math.round(parseFloat(msg.best_bid as string) * 100);
      }
      if (msg.best_ask !== undefined) {
        updates.yes_ask_cents = Math.round(parseFloat(msg.best_ask as string) * 100);
      }

      await db
        .update(schema.markets)
        .set(updates)
        .where(eq(schema.markets.ticker, market))
        .catch(() => {});

      sseBroadcaster.broadcastTicker(market, msg);
    } catch (err) {
      logger.debug({ err }, "Polymarket price_change error");
    }
  });

  // ─── Best bid/ask handler ────────────────────────────────
  polyWsClient.on("best_bid_ask", async (msg: Record<string, unknown>) => {
    try {
      const market = msg.market as string;
      if (!market) return;
      sseBroadcaster.broadcastTicker(market, msg);
    } catch (err) {
      logger.debug({ err }, "Polymarket best_bid_ask error");
    }
  });

  // ─── Safety-net error listener (prevent process crash) ───
  polyWsClient.on("error", (err: unknown) => {
    logger.error({ err }, "Polymarket WS client error (handled, will reconnect)");
  });

  // ─── Connect ─────────────────────────────────────────────
  try {
    await polyWsClient.connect();
    logger.info("Polymarket WS connected and ingesting");
  } catch (err) {
    logger.error({ err }, "Initial Polymarket WS connection failed, will retry...");
  }

  return polyWsClient;
}

/**
 * Subscribe to Polymarket tokens on the WS client.
 * Called from market-discovery after discovering markets.
 */
export function subscribeToPolymarketTokens(assetIds: string[]): void {
  if (!polyWsClient || assetIds.length === 0) return;
  polyWsClient.subscribe(assetIds);
  logger.info({ count: assetIds.length }, "Subscribed to Polymarket token feeds");
}
