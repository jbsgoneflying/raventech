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
import pg from "pg";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { serverConfig, kalshiConfig, polymarketConfig, dbConfig } from "./config.js";
import { pool } from "./db/index.js";
import { connectRedis, disconnectRedis } from "./services/redis-windows.js";
import { startMarketDiscovery } from "./services/market-discovery.js";
import { startIngestion, startPolymarketIngestion, getIngestionStats } from "./services/ingestion.js";
import { alertsRouter } from "./routes/alerts.js";
import { marketsRouter } from "./routes/markets.js";
import { configRouter } from "./routes/config.js";
import { isAuthAvailable } from "./kalshi/auth.js";
import { initEmailAlerts } from "./services/email-alerts.js";
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

  // 2. Run database migrations
  logger.info("Running database migrations...");
  try {
    const migClient = new pg.Client({ connectionString: dbConfig.url });
    await migClient.connect();

    await migClient.query(`
      CREATE TABLE IF NOT EXISTS _migrations (
        name TEXT PRIMARY KEY,
        applied_at TIMESTAMPTZ DEFAULT NOW()
      );
    `);

    const __dirname = path.dirname(fileURLToPath(import.meta.url));
    // In Docker, __dirname is /app/apps/api/dist/ and migrations are at ../src/db/migrations
    // Locally, __dirname is .../src/ and migrations are at ./db/migrations
    const candidates = [
      path.resolve(__dirname, "../src/db/migrations"),
      path.resolve(__dirname, "db/migrations"),
    ];
    const actualDir = candidates.find((d) => fs.existsSync(d)) ?? candidates[0];
    const migrationFiles = fs.existsSync(actualDir)
      ? fs.readdirSync(actualDir).filter((f) => f.endsWith(".sql")).sort()
      : [];

    for (const file of migrationFiles) {
      const { rows } = await migClient.query("SELECT 1 FROM _migrations WHERE name = $1", [file]);
      if (rows.length > 0) {
        logger.info({ file }, "Migration already applied");
        continue;
      }

      const sql = fs.readFileSync(path.join(actualDir, file), "utf-8");
      logger.info({ file }, "Applying migration...");

      await migClient.query("BEGIN");
      try {
        await migClient.query(sql);
        await migClient.query("INSERT INTO _migrations (name) VALUES ($1)", [file]);
        await migClient.query("COMMIT");
        logger.info({ file }, "Migration applied successfully");
      } catch (err) {
        await migClient.query("ROLLBACK");
        logger.error({ err, file }, "Migration FAILED");
        throw err;
      }
    }

    await migClient.end();
    logger.info("Database migrations complete");
  } catch (err) {
    logger.error({ err }, "Migration failed");
    process.exit(1);
  }

  // 3. Log auth status
  const authAvailable = isAuthAvailable();
  logger.info({
    auth: authAvailable,
    env: kalshiConfig.environment,
    baseUrl: kalshiConfig.baseUrl,
  }, authAvailable
    ? "Kalshi auth ACTIVE (full API access)"
    : "Kalshi auth NOT CONFIGURED (public channels only)"
  );

  // 3a. Initialize email alerts
  initEmailAlerts();

  // 3. Start Express server
  const app = express();
  app.use(cors());
  app.use(express.json());

  // Health check (includes memory stats for monitoring)
  app.get("/health", (_req, res) => {
    const mem = process.memoryUsage();
    res.json({
      status: "ok",
      ...getIngestionStats(),
      memory: {
        rss_mb: Math.round(mem.rss / 1024 / 1024),
        heap_used_mb: Math.round(mem.heapUsed / 1024 / 1024),
        heap_total_mb: Math.round(mem.heapTotal / 1024 / 1024),
      },
    });
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

  // 6. Periodic metrics logging (with memory tracking)
  const metricsInterval = setInterval(() => {
    const stats = getIngestionStats();
    const mem = process.memoryUsage();
    logger.info({
      ...stats,
      memory: {
        rss_mb: Math.round(mem.rss / 1024 / 1024),
        heap_used_mb: Math.round(mem.heapUsed / 1024 / 1024),
        heap_total_mb: Math.round(mem.heapTotal / 1024 / 1024),
      },
    }, "Ingestion metrics");
  }, 60_000);

  // 7. Auto-cleanup: purge stale trades/alerts/snapshots every hour
  const RETENTION_HOURS = 24;
  const cleanupInterval = setInterval(async () => {
    const cutoff = new Date(Date.now() - RETENTION_HOURS * 60 * 60 * 1000);
    try {
      const { rowCount: trades } = await pool.query(
        "DELETE FROM trade_events WHERE created_time < $1", [cutoff]
      );
      const { rowCount: alertRows } = await pool.query(
        "DELETE FROM alerts WHERE created_at < $1", [cutoff]
      );
      const { rowCount: snaps } = await pool.query(
        "DELETE FROM orderbook_snapshots WHERE captured_at < $1", [cutoff]
      );
      if ((trades ?? 0) > 0 || (alertRows ?? 0) > 0 || (snaps ?? 0) > 0) {
        logger.info(
          { trades, alerts: alertRows, snapshots: snaps, cutoff: cutoff.toISOString() },
          "Auto-cleanup: purged stale data"
        );
      }
    } catch (err) {
      logger.warn({ err }, "Auto-cleanup failed");
    }
  }, 60 * 60 * 1000); // every hour

  // Run cleanup once on startup to clear any existing backlog
  setTimeout(async () => {
    const cutoff = new Date(Date.now() - RETENTION_HOURS * 60 * 60 * 1000);
    try {
      const { rowCount: trades } = await pool.query(
        "DELETE FROM trade_events WHERE created_time < $1", [cutoff]
      );
      const { rowCount: alertRows } = await pool.query(
        "DELETE FROM alerts WHERE created_at < $1", [cutoff]
      );
      const { rowCount: snaps } = await pool.query(
        "DELETE FROM orderbook_snapshots WHERE captured_at < $1", [cutoff]
      );
      logger.info(
        { trades, alerts: alertRows, snapshots: snaps, cutoff: cutoff.toISOString() },
        "Startup cleanup: purged stale data"
      );
    } catch (err) {
      logger.warn({ err }, "Startup cleanup failed");
    }
  }, 15_000); // 15s after boot

  // ─── Graceful shutdown ─────────────────────────────────────

  const shutdown = async (signal: string) => {
    logger.info({ signal }, "Shutting down...");
    clearInterval(discoveryInterval);
    clearInterval(metricsInterval);
    clearInterval(cleanupInterval);

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
