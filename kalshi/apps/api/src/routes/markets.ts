import { Router } from "express";
import { db, schema } from "../db/index.js";
import { desc, eq, and, gte, sql } from "drizzle-orm";
import { getBookState } from "../services/redis-windows.js";

export const marketsRouter = Router();

/**
 * GET /api/markets
 * List all monitored markets.
 */
marketsRouter.get("/", async (req, res) => {
  try {
    const { status, exchange, limit: limitStr, offset: offsetStr, search } = req.query;
    const limit = Math.min(parseInt(limitStr as string) || 100, 500);
    const offset = parseInt(offsetStr as string) || 0;

    const conditions = [];
    if (status) {
      conditions.push(eq(schema.markets.status, status as string));
    }
    if (exchange) {
      conditions.push(eq(schema.markets.exchange, exchange as string));
    }
    if (search) {
      conditions.push(sql`${schema.markets.title} ILIKE ${"%" + search + "%"}`);
    }

    const where = conditions.length > 0 ? and(...conditions) : undefined;

    const rows = await db
      .select()
      .from(schema.markets)
      .where(where)
      .orderBy(desc(schema.markets.volume))
      .limit(limit)
      .offset(offset);

    const markets = rows.map((r) => ({
      ...r,
      close_time: r.close_time?.toISOString() ?? null,
      updated_at: r.updated_at?.toISOString() ?? null,
    }));

    res.json({ markets, limit, offset });
  } catch (err) {
    res.status(500).json({ error: "Failed to fetch markets" });
  }
});

/**
 * GET /api/markets/:ticker
 * Market detail including book state and recent alerts.
 */
marketsRouter.get("/:ticker", async (req, res) => {
  try {
    const { ticker } = req.params;

    // Market data
    const [market] = await db
      .select()
      .from(schema.markets)
      .where(eq(schema.markets.ticker, ticker))
      .limit(1);

    if (!market) {
      return res.status(404).json({ error: "Market not found" });
    }

    // Recent trades
    const trades = await db
      .select()
      .from(schema.tradeEvents)
      .where(eq(schema.tradeEvents.market_ticker, ticker))
      .orderBy(desc(schema.tradeEvents.created_time))
      .limit(100);

    // Recent alerts
    const alerts = await db
      .select()
      .from(schema.alerts)
      .where(eq(schema.alerts.market_ticker, ticker))
      .orderBy(desc(schema.alerts.created_at))
      .limit(50);

    // Book state from Redis
    const bookState = await getBookState(ticker);

    res.json({
      market: {
        ...market,
        close_time: market.close_time?.toISOString() ?? null,
        updated_at: market.updated_at?.toISOString() ?? null,
      },
      trades: trades.map((t) => ({
        ...t,
        created_time: t.created_time.toISOString(),
        ingested_at: t.ingested_at?.toISOString() ?? null,
      })),
      alerts: alerts.map((a) => ({
        ...a,
        created_at: a.created_at?.toISOString() ?? null,
      })),
      book: bookState
        ? {
            mid: bookState.mid,
            best_yes_bid: bookState.best_yes_bid,
            best_yes_ask: bookState.best_yes_ask,
            yes_bids: JSON.parse(bookState.yes_bids_json),
            no_bids: JSON.parse(bookState.no_bids_json),
          }
        : null,
    });
  } catch (err) {
    res.status(500).json({ error: "Failed to fetch market detail" });
  }
});

/**
 * GET /api/markets/:ticker/trades
 * Recent trades for a specific market.
 */
marketsRouter.get("/:ticker/trades", async (req, res) => {
  try {
    const { ticker } = req.params;
    const limit = Math.min(parseInt(req.query.limit as string) || 100, 1000);

    const trades = await db
      .select()
      .from(schema.tradeEvents)
      .where(eq(schema.tradeEvents.market_ticker, ticker))
      .orderBy(desc(schema.tradeEvents.created_time))
      .limit(limit);

    res.json({
      trades: trades.map((t) => ({
        ...t,
        created_time: t.created_time.toISOString(),
        ingested_at: t.ingested_at?.toISOString() ?? null,
      })),
    });
  } catch (err) {
    res.status(500).json({ error: "Failed to fetch trades" });
  }
});
