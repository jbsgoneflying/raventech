/**
 * Replay service: reads historical trade events from Postgres
 * and feeds them through the feature + alert pipeline at configurable speed.
 */

import { db, schema } from "../db/index.js";
import { and, gte, lte, asc } from "drizzle-orm";
import { computeFeatures } from "./feature-engine.js";
import { evaluateTrade } from "./alert-engine.js";
import { logger } from "../logger.js";

export interface ReplayOptions {
  from: Date;
  to: Date;
  speed: number;       // 1 = real-time, 5 = 5x, 20 = 20x
  outputAlerts?: boolean;
}

export interface ReplayResult {
  tradesProcessed: number;
  alertsGenerated: number;
  durationMs: number;
  alerts: Array<{
    market_ticker: string;
    alert_type: string;
    anomaly_score: number;
    reason: string;
    timestamp: string;
  }>;
}

/**
 * Run a replay of historical events.
 */
export async function runReplay(options: ReplayOptions): Promise<ReplayResult> {
  const { from, to, speed } = options;

  logger.info({ from, to, speed }, "Starting replay...");

  // Fetch all trades in the window
  const trades = await db
    .select()
    .from(schema.tradeEvents)
    .where(
      and(
        gte(schema.tradeEvents.created_time, from),
        lte(schema.tradeEvents.created_time, to),
      )
    )
    .orderBy(asc(schema.tradeEvents.created_time));

  logger.info({ count: trades.length }, "Loaded trades for replay");

  const startReal = Date.now();
  const alerts: ReplayResult["alerts"] = [];
  let tradesProcessed = 0;

  for (let i = 0; i < trades.length; i++) {
    const trade = trades[i];

    // Respect relative timing between events
    if (i > 0 && speed < 100) {
      const prevTime = trades[i - 1].created_time.getTime();
      const currTime = trade.created_time.getTime();
      const gap = currTime - prevTime;
      const waitMs = gap / speed;

      if (waitMs > 10 && waitMs < 10_000) {
        await new Promise((r) => setTimeout(r, waitMs));
      }
    }

    try {
      const features = await computeFeatures({
        trade_id: trade.trade_id,
        market_ticker: trade.market_ticker,
        yes_price_cents: trade.yes_price_cents,
        no_price_cents: trade.no_price_cents,
        count: trade.count,
        taker_side: trade.taker_side as "yes" | "no",
        created_time: trade.created_time.toISOString(),
      });

      const alert = await evaluateTrade(features);
      if (alert) {
        const summary = {
          market_ticker: alert.market_ticker,
          alert_type: alert.alert_type,
          anomaly_score: alert.anomaly_score,
          reason: alert.reason,
          timestamp: features.timestamp,
        };
        alerts.push(summary);

        if (options.outputAlerts) {
          // JSON lines to stdout
          process.stdout.write(JSON.stringify(summary) + "\n");
        }
      }

      tradesProcessed++;

      // Progress logging every 1000 trades
      if (tradesProcessed % 1000 === 0) {
        logger.info({ tradesProcessed, alertsSoFar: alerts.length }, "Replay progress");
      }
    } catch (err) {
      logger.debug({ tradeId: trade.trade_id, err }, "Replay trade processing error");
    }
  }

  const durationMs = Date.now() - startReal;

  logger.info({
    tradesProcessed,
    alertsGenerated: alerts.length,
    durationMs,
  }, "Replay complete");

  return {
    tradesProcessed,
    alertsGenerated: alerts.length,
    durationMs,
    alerts,
  };
}
