/**
 * Alert engine: scores trades, applies gating rules, persists and broadcasts alerts.
 */

import { anomalyScore, type TradeFeature, type Alert } from "@kalshi-monitor/shared";
import { getAlertConfig } from "../config.js";
import { getCooldown, setCooldown } from "./redis-windows.js";
import { db, schema } from "../db/index.js";
import { eq, and, gte } from "drizzle-orm";
import { sseBroadcaster } from "./sse.js";
import { logger } from "../logger.js";

/**
 * Evaluate a trade's features and potentially generate an alert.
 * Returns the alert if one was generated, null otherwise.
 */
export async function evaluateTrade(features: TradeFeature): Promise<Alert | null> {
  const config = getAlertConfig();

  // 1. Score
  const result = anomalyScore(features, config.weights);

  if (result.score < config.score_threshold) {
    return null;
  }

  // 2. Minimum liquidity check
  try {
    const [market] = await db
      .select({ open_interest: schema.markets.open_interest })
      .from(schema.markets)
      .where(eq(schema.markets.ticker, features.market_ticker))
      .limit(1);

    if (market?.open_interest !== null && market?.open_interest !== undefined) {
      if (market.open_interest < config.min_open_interest) {
        logger.debug(
          { ticker: features.market_ticker, oi: market.open_interest },
          "Skipping alert: below min_open_interest"
        );
        return null;
      }
    }
  } catch {
    // Proceed if we can't check OI
  }

  // 3. Cooldown gating
  const cooldown = await getCooldown(features.market_ticker);
  if (cooldown) {
    const elapsed = (Date.now() - cooldown.ts) / 1000;
    if (elapsed < config.cooldown_seconds) {
      // Allow if score jumped significantly
      if (result.score - cooldown.score < config.cooldown_score_delta) {
        logger.debug(
          { ticker: features.market_ticker, elapsed, prevScore: cooldown.score, newScore: result.score },
          "Skipping alert: in cooldown"
        );
        return null;
      }
    }
  }

  // 4. Persist alert
  const alertRow = {
    market_ticker: features.market_ticker,
    alert_type: result.alert_type,
    anomaly_score: result.score,
    trade_id: features.trade_id,
    explanation: result.explanation,
    reason: result.reason,
    exchange: "kalshi" as const,
  };

  const [inserted] = await db
    .insert(schema.alerts)
    .values(alertRow)
    .returning();

  // 5. Update cooldown
  await setCooldown(features.market_ticker, result.score);

  // 6. Build full alert for broadcast
  let marketTitle = features.market_ticker;
  let closeTime: string | undefined;
  let lastPriceCents: number | undefined;

  try {
    const [market] = await db
      .select({
        title: schema.markets.title,
        close_time: schema.markets.close_time,
        last_price_cents: schema.markets.last_price_cents,
      })
      .from(schema.markets)
      .where(eq(schema.markets.ticker, features.market_ticker))
      .limit(1);

    if (market) {
      marketTitle = market.title;
      closeTime = market.close_time?.toISOString();
      lastPriceCents = market.last_price_cents ?? undefined;
    }
  } catch {
    // Non-critical
  }

  const alert: Alert = {
    id: inserted.id,
    market_ticker: features.market_ticker,
    alert_type: result.alert_type as Alert["alert_type"],
    anomaly_score: result.score,
    trade_id: features.trade_id,
    explanation: result.explanation,
    reason: result.reason,
    created_at: inserted.created_at?.toISOString() ?? new Date().toISOString(),
    market_title: marketTitle,
    close_time: closeTime,
    last_price_cents: lastPriceCents,
    exchange: "kalshi",
  };

  // 7. Broadcast via SSE
  sseBroadcaster.broadcastAlert(alert);

  logger.info(
    {
      alertId: alert.id,
      ticker: alert.market_ticker,
      type: alert.alert_type,
      score: alert.anomaly_score,
      reason: alert.reason,
    },
    "ALERT generated"
  );

  return alert;
}
