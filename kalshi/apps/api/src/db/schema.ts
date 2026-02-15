/**
 * Drizzle ORM schema for Kalshi monitor.
 * Tables: markets, trade_events, alerts, orderbook_snapshots
 */

import {
  pgTable,
  text,
  integer,
  bigint,
  real,
  timestamp,
  jsonb,
  serial,
  uuid,
  index,
} from "drizzle-orm/pg-core";

// ─── Markets ─────────────────────────────────────────────────

export const markets = pgTable("markets", {
  ticker: text("ticker").primaryKey(),
  event_ticker: text("event_ticker").notNull(),
  title: text("title").notNull(),
  yes_sub_title: text("yes_sub_title"),
  no_sub_title: text("no_sub_title"),
  status: text("status").notNull(),
  close_time: timestamp("close_time", { withTimezone: true }),
  last_price_cents: integer("last_price_cents"),
  yes_bid_cents: integer("yes_bid_cents"),
  yes_ask_cents: integer("yes_ask_cents"),
  no_bid_cents: integer("no_bid_cents"),
  no_ask_cents: integer("no_ask_cents"),
  volume: bigint("volume", { mode: "number" }),
  open_interest: bigint("open_interest", { mode: "number" }),
  category: text("category"),
  exchange: text("exchange").notNull().default("kalshi"),
  exchange_market_id: text("exchange_market_id"),
  clob_token_ids: jsonb("clob_token_ids"),
  event_slug: text("event_slug"),
  updated_at: timestamp("updated_at", { withTimezone: true }).defaultNow(),
});

// ─── Trade Events ────────────────────────────────────────────

export const tradeEvents = pgTable("trade_events", {
  trade_id: text("trade_id").primaryKey(),
  market_ticker: text("market_ticker").notNull().references(() => markets.ticker),
  yes_price_cents: integer("yes_price_cents").notNull(),
  no_price_cents: integer("no_price_cents").notNull(),
  count: integer("count").notNull(),
  taker_side: text("taker_side").notNull(),
  created_time: timestamp("created_time", { withTimezone: true }).notNull(),
  ingested_at: timestamp("ingested_at", { withTimezone: true }).defaultNow(),
  exchange: text("exchange").notNull().default("kalshi"),
}, (table) => [
  index("idx_trade_events_ticker_time").on(table.market_ticker, table.created_time),
]);

// ─── Alerts ──────────────────────────────────────────────────

export const alerts = pgTable("alerts", {
  id: uuid("id").primaryKey().defaultRandom(),
  market_ticker: text("market_ticker").notNull().references(() => markets.ticker),
  alert_type: text("alert_type").notNull(),
  anomaly_score: real("anomaly_score").notNull(),
  trade_id: text("trade_id").references(() => tradeEvents.trade_id),
  explanation: jsonb("explanation").notNull(),
  reason: text("reason").notNull(),
  exchange: text("exchange").notNull().default("kalshi"),
  created_at: timestamp("created_at", { withTimezone: true }).defaultNow(),
}, (table) => [
  index("idx_alerts_score").on(table.anomaly_score),
  index("idx_alerts_ticker").on(table.market_ticker, table.created_at),
]);

// ─── Orderbook Snapshots ─────────────────────────────────────

export const orderbookSnapshots = pgTable("orderbook_snapshots", {
  id: serial("id").primaryKey(),
  market_ticker: text("market_ticker").notNull().references(() => markets.ticker),
  yes_bids: jsonb("yes_bids").notNull(),
  no_bids: jsonb("no_bids").notNull(),
  captured_at: timestamp("captured_at", { withTimezone: true }).defaultNow(),
});
