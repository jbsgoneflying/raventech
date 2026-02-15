/**
 * Kalshi Flow Monitor - API Server
 *
 * Boot sequence:
 * 1. Connect to Postgres + Redis
 * 2. Run migrations
 * 3. Start Express server
 * 4. Start market discovery
 * 5. Start live ingestion (WebSocket)
 * 6. Log metrics periodically
 */

import express from "express";
import cors from "cors";
import { serverConfig, kalshiConfig, polymarketConfig } from "./config.js";
import { pool } from "./db/index.js";
import { connectRedis, disconnectRedis } from "./services/redis-windows.js";
import { startMarketDiscovery } from "./services/market-discovery.js";
import { startIngestion, startPolymarketIngestion, getIngestionStats } from "./services/ingestion.js";
import { alertsRouter } from "./routes/alerts.js";
import { marketsRouter } from "./routes/markets.js";
import { configRouter } from "./routes/config.js";
import { isAuthAvailable } from "./kalshi/auth.js";
import { logger } from "./logger.js";

async function main() {
  logger.info("═══════════════════════════════════════════════════");
  logger.info("  Kalshi Flow Monitor - Starting...");
  logger.info("═══════════════════════════════════════════════════");

  // 1. Connect to infrastructure
  logger.info("Connecting to Postgres...");
  try {
    const client = await pool.connect();
    client.release();
    logger.info("Postgres connected");
  } catch (err) {
    logger.error({ err }, "Postgres connection failed");
    process.exit(1);
  }

  logger.info("Connecting to Redis...");
  try {
    await connectRedis();
  } catch (err) {
    logger.error({ err }, "Redis connection failed");
    process.exit(1);
  }

  // 2. Log auth status
  const authAvailable = isAuthAvailable();
  logger.info({
    auth: authAvailable,
    env: kalshiConfig.environment,
    baseUrl: kalshiConfig.baseUrl,
  }, authAvailable
    ? "Kalshi auth ACTIVE (full API access)"
    : "Kalshi auth NOT CONFIGURED (public channels only)"
  );

  // 3. Start Express server
  const app = express();
  app.use(cors());
  app.use(express.json());

  // Health check
  app.get("/health", (_req, res) => {
    res.json({ status: "ok", ...getIngestionStats() });
  });

  // API routes
  app.use("/api/alerts", alertsRouter);
  app.use("/api/markets", marketsRouter);
  app.use("/api/config", configRouter);

  const server = app.listen(serverConfig.port, () => {
    logger.info({ port: serverConfig.port }, "API server listening");
  });

  // 4. Start market discovery
  const discoveryInterval = startMarketDiscovery();

  // 5. Start live ingestion
  // Small delay to let initial market discovery complete
  setTimeout(async () => {
    try {
      await startIngestion();
      logger.info("Kalshi live ingestion started");
    } catch (err) {
      logger.error({ err }, "Failed to start Kalshi ingestion");
    }

    // Start Polymarket ingestion if enabled
    if (polymarketConfig.enabled) {
      try {
        await startPolymarketIngestion();
        logger.info("Polymarket live ingestion started");
      } catch (err) {
        logger.error({ err }, "Failed to start Polymarket ingestion");
      }
    } else {
      logger.info("Polymarket ingestion disabled (ENABLE_POLYMARKET=0)");
    }
  }, 5_000);

  // 6. Periodic metrics logging
  const metricsInterval = setInterval(() => {
    const stats = getIngestionStats();
    logger.info(stats, "Ingestion metrics");
  }, 60_000);

  // ─── Graceful shutdown ─────────────────────────────────────

  const shutdown = async (signal: string) => {
    logger.info({ signal }, "Shutting down...");
    clearInterval(discoveryInterval);
    clearInterval(metricsInterval);

    server.close();
    await disconnectRedis();
    await pool.end();

    logger.info("Shutdown complete");
    process.exit(0);
  };

  process.on("SIGTERM", () => shutdown("SIGTERM"));
  process.on("SIGINT", () => shutdown("SIGINT"));
}

main().catch((err) => {
  logger.fatal({ err }, "Fatal startup error");
  process.exit(1);
});
