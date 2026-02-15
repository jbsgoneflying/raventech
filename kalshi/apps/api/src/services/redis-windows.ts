/**
 * Redis rolling window helpers for per-market state.
 *
 * Keys:
 *   trades:{ticker}:5m    - sorted set (score=timestamp_ms, member=trade_json)
 *   trades:{ticker}:15m   - sorted set
 *   trades:{ticker}:60m   - sorted set
 *   book:{ticker}          - hash with book state
 *   baseline:{ticker}      - hash with rolling stats (median, mad, mean, std, count)
 *   cooldown:{ticker}      - hash with last alert ts + score
 *   flow:{ticker}:buy:1m   - total aggressive buy qty in last 1m
 *   flow:{ticker}:sell:1m  - total aggressive sell qty in last 1m
 */

import { Redis } from "ioredis";
import { redisConfig } from "../config.js";
import { computeBaseline, type Baseline } from "@kalshi-monitor/shared";
import { logger } from "../logger.js";

let redis: Redis | null = null;

export function getRedis(): Redis {
  if (!redis) {
    redis = new Redis(redisConfig.url, {
      maxRetriesPerRequest: 3,
      lazyConnect: true,
    });
    redis.on("error", (err: Error) => logger.error({ err }, "Redis error"));
  }
  return redis;
}

export async function connectRedis(): Promise<void> {
  const r = getRedis();
  await r.connect();
  logger.info("Redis connected");
}

// ─── Trade windows ───────────────────────────────────────────

const WINDOWS = [
  { suffix: "5m", ttlMs: 5 * 60 * 1000 },
  { suffix: "15m", ttlMs: 15 * 60 * 1000 },
  { suffix: "60m", ttlMs: 60 * 60 * 1000 },
];

export interface TradeRecord {
  trade_id: string;
  count: number;
  taker_side: "yes" | "no";
  yes_price_cents: number;
  timestamp_ms: number;
}

/**
 * Add a trade to all rolling windows and update baseline.
 */
export async function addTradeToWindows(
  ticker: string,
  trade: TradeRecord,
): Promise<void> {
  const r = getRedis();
  const pipe = r.pipeline();
  const member = JSON.stringify(trade);
  const now = Date.now();

  for (const w of WINDOWS) {
    const key = `trades:${ticker}:${w.suffix}`;
    pipe.zadd(key, trade.timestamp_ms, member);
    // Trim old entries
    pipe.zremrangebyscore(key, 0, now - w.ttlMs);
    // Set key expiry slightly beyond window
    pipe.expire(key, Math.ceil(w.ttlMs / 1000) + 60);
  }

  await pipe.exec();
}

/**
 * Get trades from a specific window.
 */
export async function getTradesInWindow(
  ticker: string,
  windowSuffix: "5m" | "15m" | "60m",
): Promise<TradeRecord[]> {
  const r = getRedis();
  const w = WINDOWS.find((x) => x.suffix === windowSuffix)!;
  const key = `trades:${ticker}:${w.suffix}`;
  const now = Date.now();

  // Trim and fetch
  await r.zremrangebyscore(key, 0, now - w.ttlMs);
  const members = await r.zrange(key, 0, -1);
  return members.map((m) => JSON.parse(m) as TradeRecord);
}

// ─── Baseline ────────────────────────────────────────────────

/**
 * Update and return the rolling baseline for a market.
 * Uses the 60m window of trade quantities.
 */
export async function updateBaseline(ticker: string): Promise<Baseline> {
  const trades = await getTradesInWindow(ticker, "60m");
  const quantities = trades.map((t) => t.count);
  const baseline = computeBaseline(quantities);

  const r = getRedis();
  await r.hmset(`baseline:${ticker}`, {
    median: baseline.median.toString(),
    mad: baseline.mad.toString(),
    mean: baseline.mean.toString(),
    std: baseline.std.toString(),
    count: baseline.count.toString(),
  });
  await r.expire(`baseline:${ticker}`, 7200); // 2h TTL

  return baseline;
}

export async function getBaseline(ticker: string): Promise<Baseline | null> {
  const r = getRedis();
  const data = await r.hgetall(`baseline:${ticker}`);
  if (!data.count) return null;
  return {
    median: parseFloat(data.median),
    mad: parseFloat(data.mad),
    mean: parseFloat(data.mean),
    std: parseFloat(data.std),
    count: parseInt(data.count),
  };
}

// ─── Book state ──────────────────────────────────────────────

export interface RedisBookState {
  mid: number | null;
  best_yes_bid: number | null;
  best_yes_ask: number | null;
  best_yes_bid_qty: number | null;
  best_yes_ask_qty: number | null;
  yes_bids_json: string; // JSON array of [price, qty][]
  no_bids_json: string;
}

export async function setBookState(ticker: string, state: RedisBookState): Promise<void> {
  const r = getRedis();
  await r.hmset(`book:${ticker}`, {
    mid: state.mid?.toString() ?? "",
    best_yes_bid: state.best_yes_bid?.toString() ?? "",
    best_yes_ask: state.best_yes_ask?.toString() ?? "",
    best_yes_bid_qty: state.best_yes_bid_qty?.toString() ?? "",
    best_yes_ask_qty: state.best_yes_ask_qty?.toString() ?? "",
    yes_bids_json: state.yes_bids_json,
    no_bids_json: state.no_bids_json,
  });
  await r.expire(`book:${ticker}`, 3600);
}

export async function getBookState(ticker: string): Promise<RedisBookState | null> {
  const r = getRedis();
  const data = await r.hgetall(`book:${ticker}`);
  if (!data.mid && data.mid !== "0") return null;

  return {
    mid: data.mid ? parseFloat(data.mid) : null,
    best_yes_bid: data.best_yes_bid ? parseFloat(data.best_yes_bid) : null,
    best_yes_ask: data.best_yes_ask ? parseFloat(data.best_yes_ask) : null,
    best_yes_bid_qty: data.best_yes_bid_qty ? parseFloat(data.best_yes_bid_qty) : null,
    best_yes_ask_qty: data.best_yes_ask_qty ? parseFloat(data.best_yes_ask_qty) : null,
    yes_bids_json: data.yes_bids_json ?? "[]",
    no_bids_json: data.no_bids_json ?? "[]",
  };
}

// ─── Flow tracking ───────────────────────────────────────────

export async function addFlow(
  ticker: string,
  side: "yes" | "no",
  qty: number,
): Promise<void> {
  const r = getRedis();
  const key = `flow:${ticker}:${side}:1m`;
  const now = Date.now();
  const pipe = r.pipeline();
  pipe.zadd(key, now, `${now}:${qty}`);
  pipe.zremrangebyscore(key, 0, now - 60_000);
  pipe.expire(key, 120);
  await pipe.exec();
}

export async function getFlowImbalance(ticker: string): Promise<{ buy: number; sell: number }> {
  const r = getRedis();
  const now = Date.now();

  const [buyMembers, sellMembers] = await Promise.all([
    r.zrangebyscore(`flow:${ticker}:yes:1m`, now - 60_000, "+inf"),
    r.zrangebyscore(`flow:${ticker}:no:1m`, now - 60_000, "+inf"),
  ]);

  const sumQty = (members: string[]) =>
    members.reduce((sum, member) => sum + parseFloat(member.split(":")[1] ?? "0"), 0);

  return { buy: sumQty(buyMembers), sell: sumQty(sellMembers) };
}

// ─── Cooldown ────────────────────────────────────────────────

export async function getCooldown(ticker: string): Promise<{ ts: number; score: number } | null> {
  const r = getRedis();
  const data = await r.hgetall(`cooldown:${ticker}`);
  if (!data.ts) return null;
  return { ts: parseInt(data.ts), score: parseFloat(data.score) };
}

export async function setCooldown(ticker: string, score: number): Promise<void> {
  const r = getRedis();
  await r.hmset(`cooldown:${ticker}`, {
    ts: Date.now().toString(),
    score: score.toString(),
  });
  await r.expire(`cooldown:${ticker}`, 600); // 10m max cooldown tracking
}

// ─── Last large print tracking ───────────────────────────────

export async function setLastLargePrint(ticker: string): Promise<void> {
  const r = getRedis();
  await r.set(`last_large:${ticker}`, Date.now().toString(), "EX", 3600);
}

export async function getMinutesSinceLastLarge(ticker: string): Promise<number | null> {
  const r = getRedis();
  const ts = await r.get(`last_large:${ticker}`);
  if (!ts) return null;
  return (Date.now() - parseInt(ts)) / 60_000;
}

// ─── Cleanup ─────────────────────────────────────────────────

export async function disconnectRedis(): Promise<void> {
  if (redis) {
    await redis.quit();
    redis = null;
  }
}
