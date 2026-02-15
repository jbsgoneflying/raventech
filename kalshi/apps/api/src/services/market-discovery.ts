/**
 * Market discovery: periodically pull active markets from Kalshi
 * and Polymarket, and upsert into Postgres.
 */

import { eq } from "drizzle-orm";
import { db, schema } from "../db/index.js";
import { getAllOpenMarkets } from "../kalshi/rest.js";
import { getActiveEvents } from "../polymarket/rest.js";
import { normalizePolyMarket, populateTokenMap } from "../polymarket/normalize.js";
import { subscribeToPolymarketTokens } from "./ingestion.js";
import { discoveryConfig, polymarketConfig } from "../config.js";
import { logger } from "../logger.js";
import type { KalshiMarket } from "@kalshi-monitor/shared";

/**
 * Convert Kalshi dollar string to cents integer.
 * "0.5600" -> 56
 */
function dollarsToCents(d: string | undefined): number | null {
  if (!d) return null;
  const val = parseFloat(d);
  return isNaN(val) ? null : Math.round(val * 100);
}

/**
 * Upsert a batch of markets from Kalshi API response.
 */
export async function upsertMarkets(kalshiMarkets: KalshiMarket[]): Promise<number> {
  let upserted = 0;

  for (const m of kalshiMarkets) {
    try {
      const values = {
        ticker: m.ticker,
        event_ticker: m.event_ticker,
        title: m.title ?? m.yes_sub_title ?? m.ticker,
        yes_sub_title: m.yes_sub_title ?? null,
        no_sub_title: m.no_sub_title ?? null,
        status: m.status,
        close_time: m.close_time ? new Date(m.close_time) : null,
        last_price_cents: dollarsToCents(m.last_price_dollars) ?? m.last_price ?? null,
        yes_bid_cents: dollarsToCents(m.yes_bid_dollars) ?? m.yes_bid ?? null,
        yes_ask_cents: dollarsToCents(m.yes_ask_dollars) ?? m.yes_ask ?? null,
        no_bid_cents: dollarsToCents(m.no_bid_dollars) ?? m.no_bid ?? null,
        no_ask_cents: dollarsToCents(m.no_ask_dollars) ?? m.no_ask ?? null,
        volume: m.volume ?? null,
        open_interest: m.open_interest ?? null,
        category: m.category ?? null,
        exchange: "kalshi" as const,
        updated_at: new Date(),
      };

      // Upsert: insert or update
      await db
        .insert(schema.markets)
        .values(values)
        .onConflictDoUpdate({
          target: schema.markets.ticker,
          set: {
            ...values,
            ticker: undefined, // don't update PK
          },
        });

      upserted++;
    } catch (err) {
      logger.warn({ ticker: m.ticker, err }, "Failed to upsert market");
    }
  }

  return upserted;
}

// ─── Polymarket Discovery ────────────────────────────────────

/**
 * Discover and upsert Polymarket markets from Gamma API.
 * Also populates the Redis token map and subscribes to WS feeds.
 */
export async function discoverPolymarketMarkets(): Promise<void> {
  if (!polymarketConfig.enabled) return;

  const start = Date.now();
  try {
    const events = await getActiveEvents();
    let upserted = 0;
    const tokenMapEntries: Array<{ assetId: string; conditionId: string }> = [];
    const allAssetIds: string[] = [];

    // Log first event structure for debugging
    if (events.length > 0) {
      const sampleEvent = events[0];
      const sampleMarket = (sampleEvent.markets ?? [])[0];
      logger.info({
        eventKeys: Object.keys(sampleEvent),
        hasMarkets: Array.isArray(sampleEvent.markets),
        marketsCount: sampleEvent.markets?.length ?? 0,
        sampleMarketKeys: sampleMarket ? Object.keys(sampleMarket) : [],
        sampleConditionId: sampleMarket?.condition_id,
        sampleActive: sampleMarket?.active,
      }, "Polymarket sample event structure");
    }

    for (const event of events) {
      for (const market of event.markets ?? []) {
        if (!market.condition_id) continue;

        const normalized = normalizePolyMarket(event, market);

        try {
          await db
            .insert(schema.markets)
            .values({
              ...normalized,
              updated_at: new Date(),
            })
            .onConflictDoUpdate({
              target: schema.markets.ticker,
              set: {
                title: normalized.title,
                status: normalized.status,
                close_time: normalized.close_time,
                last_price_cents: normalized.last_price_cents,
                category: normalized.category,
                exchange_market_id: normalized.exchange_market_id,
                clob_token_ids: normalized.clob_token_ids,
                event_slug: normalized.event_slug,
                updated_at: new Date(),
              },
            });
          upserted++;

          // Collect token map entries and asset IDs for WS subscription
          for (const [outcome, tokenId] of Object.entries(normalized.clob_token_ids)) {
            tokenMapEntries.push({ assetId: tokenId, conditionId: market.condition_id });
            allAssetIds.push(tokenId);
          }
        } catch (err) {
          logger.warn({ conditionId: market.condition_id, err }, "Failed to upsert Polymarket market");
        }
      }
    }

    // Populate Redis token map for asset_id -> condition_id resolution
    if (tokenMapEntries.length > 0) {
      await populateTokenMap(tokenMapEntries);
    }

    // Subscribe to all discovered asset IDs on the WS
    if (allAssetIds.length > 0) {
      subscribeToPolymarketTokens(allAssetIds);
    }

    logger.info(
      { upserted, events: events.length, tokens: allAssetIds.length, durationMs: Date.now() - start },
      "Polymarket discovery complete"
    );
  } catch (err) {
    logger.error({ err }, "Polymarket discovery failed");
  }
}

// ─── Combined Discovery ──────────────────────────────────────

/**
 * Run a single discovery cycle for all exchanges.
 */
export async function discoverMarkets(): Promise<void> {
  const start = Date.now();

  // Run Kalshi and Polymarket discovery in parallel
  const [kalshiResult, polyResult] = await Promise.allSettled([
    (async () => {
      try {
        logger.info("Fetching Kalshi markets from REST API...");
        const markets = await getAllOpenMarkets();
        logger.info({ total: markets.length }, "Fetched Kalshi markets, upserting...");
        const count = await upsertMarkets(markets);
        logger.info(
          { count, total: markets.length, durationMs: Date.now() - start },
          "Kalshi market discovery complete"
        );
      } catch (err) {
        logger.error({ err }, "Kalshi market discovery failed");
      }
    })(),
    discoverPolymarketMarkets(),
  ]);
}

/**
 * Start periodic market discovery.
 */
export function startMarketDiscovery(): ReturnType<typeof setInterval> {
  logger.info({ intervalMs: discoveryConfig.intervalMs }, "Starting market discovery");

  // Run immediately on start
  discoverMarkets();

  return setInterval(discoverMarkets, discoveryConfig.intervalMs);
}
