import { config as dotenvConfig } from "dotenv";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { DEFAULT_CONFIG, type AlertConfig, type ScoringWeights } from "@kalshi-monitor/shared";

// Load .env from kalshi/ root
const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenvConfig({ path: path.resolve(__dirname, "../../../.env") });

function env(key: string, fallback?: string): string {
  const val = process.env[key] ?? fallback;
  if (val === undefined) throw new Error(`Missing env var: ${key}`);
  return val;
}

function envInt(key: string, fallback: number): number {
  const raw = process.env[key];
  return raw ? parseInt(raw, 10) : fallback;
}

function envFloat(key: string, fallback: number): number {
  const raw = process.env[key];
  return raw ? parseFloat(raw) : fallback;
}

// ─── Kalshi API ──────────────────────────────────────────────

export const kalshiConfig = {
  apiKeyId: process.env.KALSHI_API_KEY_ID ?? "",
  privateKeyPath: process.env.KALSHI_PRIVATE_KEY_PATH ?? "",
  privateKeyPem: process.env.KALSHI_PRIVATE_KEY ?? "",
  baseUrl: env("KALSHI_BASE_URL", "https://api.elections.kalshi.com"),
  wsUrl: env("KALSHI_WS_URL", "wss://api.elections.kalshi.com/trade-api/ws/v2"),
  environment: env("KALSHI_ENV", "production") as "production" | "demo",
  get hasAuth(): boolean {
    return this.apiKeyId.length > 0 && (this.privateKeyPath.length > 0 || this.privateKeyPem.length > 0);
  },
};

// ─── Infrastructure ──────────────────────────────────────────

export const dbConfig = {
  url: env("DATABASE_URL", "postgresql://kalshi:kalshi_dev@localhost:5433/kalshi_monitor"),
};

export const redisConfig = {
  url: env("REDIS_URL", "redis://localhost:6380"),
};

// ─── Server ──────────────────────────────────────────────────

export const serverConfig = {
  port: envInt("API_PORT", 3100),
  logLevel: env("LOG_LEVEL", "info"),
};

// ─── Scoring & Alerts (mutable at runtime) ───────────────────

let _alertConfig: AlertConfig = {
  score_threshold: envInt("SCORE_THRESHOLD", DEFAULT_CONFIG.score_threshold),
  cooldown_seconds: envInt("COOLDOWN_SECONDS", DEFAULT_CONFIG.cooldown_seconds),
  cooldown_score_delta: envInt("COOLDOWN_SCORE_DELTA", DEFAULT_CONFIG.cooldown_score_delta),
  min_open_interest: envInt("MIN_OPEN_INTEREST", DEFAULT_CONFIG.min_open_interest),
  weights: {
    size_weight: envFloat("WEIGHT_SIZE", DEFAULT_CONFIG.weights.size_weight),
    late_weight: envFloat("WEIGHT_LATE", DEFAULT_CONFIG.weights.late_weight),
    impact_weight: envFloat("WEIGHT_IMPACT", DEFAULT_CONFIG.weights.impact_weight),
    liquidity_weight: envFloat("WEIGHT_LIQUIDITY", DEFAULT_CONFIG.weights.liquidity_weight),
    persistence_weight: envFloat("WEIGHT_PERSISTENCE", DEFAULT_CONFIG.weights.persistence_weight),
  },
};

export function getAlertConfig(): AlertConfig {
  return { ..._alertConfig, weights: { ..._alertConfig.weights } };
}

export function setAlertConfig(config: Partial<AlertConfig>): AlertConfig {
  if (config.weights) {
    _alertConfig.weights = { ..._alertConfig.weights, ...config.weights };
  }
  _alertConfig = {
    ..._alertConfig,
    ...config,
    weights: _alertConfig.weights,
  };
  return getAlertConfig();
}

// ─── Polymarket API ───────────────────────────────────────────

export const polymarketConfig = {
  gammaUrl: env("POLYMARKET_GAMMA_URL", "https://gamma-api.polymarket.com"),
  dataUrl: env("POLYMARKET_DATA_URL", "https://data-api.polymarket.com"),
  clobUrl: env("POLYMARKET_CLOB_URL", "https://clob.polymarket.com"),
  wsUrl: env("POLYMARKET_WS_URL", "wss://ws-subscriptions-clob.polymarket.com/ws/market"),
  enabled: process.env.ENABLE_POLYMARKET !== "0",
};

// ─── Market Discovery ────────────────────────────────────────

export const discoveryConfig = {
  intervalMs: envInt("MARKET_DISCOVERY_INTERVAL_MS", 300_000),
};
