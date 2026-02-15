-- Polymarket integration: add exchange-specific columns to markets table

ALTER TABLE markets ADD COLUMN IF NOT EXISTS exchange_market_id TEXT;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS clob_token_ids JSONB;
ALTER TABLE markets ADD COLUMN IF NOT EXISTS event_slug TEXT;

CREATE INDEX IF NOT EXISTS idx_markets_exchange ON markets(exchange);
