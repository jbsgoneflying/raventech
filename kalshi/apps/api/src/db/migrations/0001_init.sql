-- Initial schema for Kalshi Flow Monitor
-- Tables: markets, trade_events, alerts, orderbook_snapshots

CREATE TABLE IF NOT EXISTS markets (
  ticker TEXT PRIMARY KEY,
  event_ticker TEXT NOT NULL,
  title TEXT NOT NULL,
  yes_sub_title TEXT,
  no_sub_title TEXT,
  status TEXT NOT NULL,
  close_time TIMESTAMPTZ,
  last_price_cents INTEGER,
  yes_bid_cents INTEGER,
  yes_ask_cents INTEGER,
  no_bid_cents INTEGER,
  no_ask_cents INTEGER,
  volume BIGINT,
  open_interest BIGINT,
  category TEXT,
  exchange TEXT NOT NULL DEFAULT 'kalshi',
  updated_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS trade_events (
  trade_id TEXT PRIMARY KEY,
  market_ticker TEXT NOT NULL REFERENCES markets(ticker) ON DELETE CASCADE,
  yes_price_cents INTEGER NOT NULL,
  no_price_cents INTEGER NOT NULL,
  count INTEGER NOT NULL,
  taker_side TEXT NOT NULL,
  created_time TIMESTAMPTZ NOT NULL,
  ingested_at TIMESTAMPTZ DEFAULT NOW(),
  exchange TEXT NOT NULL DEFAULT 'kalshi'
);

CREATE INDEX IF NOT EXISTS idx_trade_events_ticker_time
  ON trade_events(market_ticker, created_time);
CREATE INDEX IF NOT EXISTS idx_trade_events_created
  ON trade_events(created_time);

CREATE TABLE IF NOT EXISTS alerts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  market_ticker TEXT NOT NULL REFERENCES markets(ticker) ON DELETE CASCADE,
  alert_type TEXT NOT NULL,
  anomaly_score REAL NOT NULL,
  trade_id TEXT REFERENCES trade_events(trade_id),
  explanation JSONB NOT NULL,
  reason TEXT NOT NULL,
  exchange TEXT NOT NULL DEFAULT 'kalshi',
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_alerts_score ON alerts(anomaly_score DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_ticker ON alerts(market_ticker, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_alerts_created ON alerts(created_at DESC);

CREATE TABLE IF NOT EXISTS orderbook_snapshots (
  id SERIAL PRIMARY KEY,
  market_ticker TEXT NOT NULL REFERENCES markets(ticker) ON DELETE CASCADE,
  yes_bids JSONB NOT NULL,
  no_bids JSONB NOT NULL,
  captured_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_orderbook_snapshots_ticker
  ON orderbook_snapshots(market_ticker, captured_at DESC);
