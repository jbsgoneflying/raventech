import { drizzle } from "drizzle-orm/node-postgres";
import pg from "pg";
import { dbConfig } from "../config.js";
import * as schema from "./schema.js";

const pool = new pg.Pool({
  connectionString: dbConfig.url,
  max: 10,
});

export const db = drizzle(pool, { schema });
export { schema };
export { pool };
