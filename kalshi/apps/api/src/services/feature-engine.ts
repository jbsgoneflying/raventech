/**
 * Feature engine: computes per-trade feature vectors by combining
 * the trade with book state and rolling baselines from Redis.
 */

import type { TradeFeature } from "@kalshi-monitor/shared";
import {
  tradeSizeZ,
  sweepScore,
  lateFactor,
  aggressiveness as computeAggressiveness,
  depthRatio as computeDepthRatio,
  flowImbalance as computeFlowImbalance,
  novelty as computeNovelty,
} from "@kalshi-monitor/shared";
import {
  getBaseline,
  getBookState,
  getFlowImbalance,
  getMinutesSinceLastLarge,
  setLastLargePrint,
  addFlow,
  addTradeToWindows,
  updateBaseline,
  type TradeRecord,
} from "./redis-windows.js";
import { db, schema } from "../db/index.js";
import { eq } from "drizzle-orm";
import { logger } from "../logger.js";

export interface IncomingTrade {
  trade_id: string;
  market_ticker: string;
  yes_price_cents: number;
  no_price_cents: number;
  count: number;
  taker_side: "yes" | "no";
  created_time: string;
}

/**
 * Compute full feature vector for an incoming trade.
 */
export async function computeFeatures(trade: IncomingTrade): Promise<TradeFeature> {
  const ticker = trade.market_ticker;
  const timestamp = trade.created_time;
  const timestampMs = new Date(timestamp).getTime();

  // 1. Add trade to rolling windows
  const tradeRecord: TradeRecord = {
    trade_id: trade.trade_id,
    count: trade.count,
    taker_side: trade.taker_side,
    yes_price_cents: trade.yes_price_cents,
    timestamp_ms: timestampMs,
  };

  await addTradeToWindows(ticker, tradeRecord);
  await addFlow(ticker, trade.taker_side, trade.count);

  // 2. Get baseline + book state from Redis
  const [baseline, bookState, flowData, minutesSinceLarge] = await Promise.all([
    getBaseline(ticker),
    getBookState(ticker),
    getFlowImbalance(ticker),
    getMinutesSinceLastLarge(ticker),
  ]);

  // 3. Update baseline (async, non-blocking for this trade)
  updateBaseline(ticker).catch((err) =>
    logger.warn({ ticker, err }, "Baseline update failed")
  );

  // 4. Look up market close_time
  let timeToCloseS: number | null = null;
  try {
    const [market] = await db
      .select({ close_time: schema.markets.close_time })
      .from(schema.markets)
      .where(eq(schema.markets.ticker, ticker))
      .limit(1);

    if (market?.close_time) {
      timeToCloseS = (new Date(market.close_time).getTime() - timestampMs) / 1000;
      if (timeToCloseS < 0) timeToCloseS = 0;
    }
  } catch {
    // Market might not be in DB yet
  }

  // 5. Compute features
  const sizeZ = tradeSizeZ(trade.count, baseline);

  // Book-dependent features
  let sweep = 0;
  let dRatio: number | null = null;
  let mid: number | null = null;
  let spread: number | null = null;

  if (bookState) {
    mid = bookState.mid;
    const yesBids: [number, number][] = JSON.parse(bookState.yes_bids_json);
    const relevantBook = trade.taker_side === "yes" ? yesBids : JSON.parse(bookState.no_bids_json);
    sweep = sweepScore(trade.count, relevantBook, 5);
    dRatio = computeDepthRatio(
      trade.count,
      bookState.best_yes_bid_qty,
      bookState.best_yes_ask_qty
    );
    if (bookState.best_yes_bid !== null && bookState.best_yes_ask !== null) {
      spread = bookState.best_yes_ask - bookState.best_yes_bid;
    }
  }

  const tradePriceCents = trade.taker_side === "yes" ? trade.yes_price_cents : trade.no_price_cents;

  const agg = computeAggressiveness(
    trade.yes_price_cents,
    mid,
    spread,
    trade.taker_side
  );

  const flow = computeFlowImbalance(flowData.buy, flowData.sell);
  const nov = computeNovelty(minutesSinceLarge);
  const late = lateFactor(timeToCloseS);

  // Track large prints (z > 2)
  if (sizeZ > 2) {
    setLastLargePrint(ticker).catch(() => {});
  }

  return {
    trade_id: trade.trade_id,
    market_ticker: ticker,
    trade_size_z: sizeZ,
    sweep_score: sweep,
    price_impact_10s: null, // filled later via delayed check
    price_impact_30s: null,
    time_to_close_s: timeToCloseS,
    late_factor: late,
    aggressiveness: agg,
    depth_ratio: dRatio,
    flow_imbalance_1m: flow,
    novelty: nov,
    mid_before: mid,
    trade_price_cents: tradePriceCents,
    trade_count: trade.count,
    taker_side: trade.taker_side,
    timestamp,
  };
}

/**
 * Schedule a delayed price impact check.
 * After `delayMs`, reads the current mid from Redis and computes impact.
 * Returns a promise that resolves with the impact value.
 */
export function scheduleImpactCheck(
  ticker: string,
  midBefore: number | null,
  delayMs: number,
): Promise<number | null> {
  return new Promise((resolve) => {
    if (midBefore === null) {
      resolve(null);
      return;
    }

    setTimeout(async () => {
      try {
        const bookState = await getBookState(ticker);
        if (bookState?.mid !== null && bookState?.mid !== undefined) {
          resolve(bookState.mid - midBefore);
        } else {
          resolve(null);
        }
      } catch {
        resolve(null);
      }
    }, delayMs);
  });
}
