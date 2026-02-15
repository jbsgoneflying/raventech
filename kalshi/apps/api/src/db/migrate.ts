/**
 * Database migration script.
 * Runs raw SQL migration files in order.
 * Usage: tsx src/db/migrate.ts
 */

import pg from "pg";
import fs from "node:fs";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { config as dotenvConfig } from "dotenv";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
dotenvConfig({ path: path.resolve(__dirname, "../../../../.env") });

const DATABASE_URL = process.env.DATABASE_URL ?? "postgresql://kalshi:kalshi_dev@localhost:5433/kalshi_monitor";

async function migrate() {
  const client = new pg.Client({ connectionString: DATABASE_URL });
  await client.connect();

  console.log("Running migrations...");

  // Create migrations tracking table
  await client.query(`
    CREATE TABLE IF NOT EXISTS _migrations (
      name TEXT PRIMARY KEY,
      applied_at TIMESTAMPTZ DEFAULT NOW()
    );
  `);

  // Read migration files
  const migrationsDir = path.resolve(__dirname, "migrations");
  const files = fs.readdirSync(migrationsDir)
    .filter((f) => f.endsWith(".sql"))
    .sort();

  for (const file of files) {
    // Check if already applied
    const { rows } = await client.query("SELECT 1 FROM _migrations WHERE name = $1", [file]);
    if (rows.length > 0) {
      console.log(`  ✓ ${file} (already applied)`);
      continue;
    }

    const sql = fs.readFileSync(path.join(migrationsDir, file), "utf-8");
    console.log(`  → Applying ${file}...`);

    await client.query("BEGIN");
    try {
      await client.query(sql);
      await client.query("INSERT INTO _migrations (name) VALUES ($1)", [file]);
      await client.query("COMMIT");
      console.log(`  ✓ ${file}`);
    } catch (err) {
      await client.query("ROLLBACK");
      console.error(`  ✗ ${file} FAILED:`, err);
      throw err;
    }
  }

  console.log("Migrations complete.");
  await client.end();
}

migrate().catch((err) => {
  console.error("Migration failed:", err);
  process.exit(1);
});
