#!/usr/bin/env node
/**
 * Replay CLI for Kalshi Flow Monitor.
 *
 * Usage:
 *   npx tsx src/replay-cli.ts --from "2026-02-13T00:00:00Z" --to "2026-02-14T00:00:00Z" --speed 5
 *
 * Options:
 *   --from     Start time (ISO 8601)
 *   --to       End time (ISO 8601), defaults to now
 *   --speed    Playback speed multiplier (default: 5)
 *   --json     Output alerts as JSON lines to stdout
 */

import { config as dotenvConfig } from "dotenv";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenvConfig({ path: path.resolve(__dirname, "../../../.env") });

import { connectRedis, disconnectRedis } from "./services/redis-windows.js";
import { pool } from "./db/index.js";
import { runReplay } from "./services/replay.js";
import { logger } from "./logger.js";

function parseArgs(args: string[]): Record<string, string> {
  const result: Record<string, string> = {};
  for (let i = 0; i < args.length; i++) {
    if (args[i].startsWith("--")) {
      const key = args[i].slice(2);
      const val = args[i + 1] && !args[i + 1].startsWith("--") ? args[i + 1] : "true";
      result[key] = val;
      if (val !== "true") i++;
    }
  }
  return result;
}

async function main() {
  const args = parseArgs(process.argv.slice(2));

  if (!args.from) {
    console.error("Usage: tsx src/replay-cli.ts --from <ISO_DATE> [--to <ISO_DATE>] [--speed <N>] [--json]");
    process.exit(1);
  }

  const from = new Date(args.from);
  const to = args.to ? new Date(args.to) : new Date();
  const speed = parseInt(args.speed ?? "5");
  const outputAlerts = args.json === "true";

  if (isNaN(from.getTime())) {
    console.error("Invalid --from date");
    process.exit(1);
  }

  // Connect infrastructure
  await connectRedis();

  console.log(`\n  Replay: ${from.toISOString()} → ${to.toISOString()} at ${speed}x\n`);

  const result = await runReplay({ from, to, speed, outputAlerts });

  console.log(`\n  ═══ Replay Summary ═══`);
  console.log(`  Trades processed: ${result.tradesProcessed}`);
  console.log(`  Alerts generated: ${result.alertsGenerated}`);
  console.log(`  Duration: ${(result.durationMs / 1000).toFixed(1)}s`);

  if (result.alerts.length > 0 && !outputAlerts) {
    console.log(`\n  Top alerts:`);
    result.alerts
      .sort((a, b) => b.anomaly_score - a.anomaly_score)
      .slice(0, 10)
      .forEach((a) => {
        console.log(`    [${a.anomaly_score}] ${a.alert_type} — ${a.market_ticker}: ${a.reason}`);
      });
  }

  // Cleanup
  await disconnectRedis();
  await pool.end();
  process.exit(0);
}

main().catch((err) => {
  logger.fatal({ err }, "Replay CLI error");
  process.exit(1);
});
