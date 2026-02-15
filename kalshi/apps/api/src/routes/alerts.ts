import { Router } from "express";
import { db, schema } from "../db/index.js";
import { desc, and, gte, eq, like, sql } from "drizzle-orm";
import { sseBroadcaster } from "../services/sse.js";

export const alertsRouter = Router();

/**
 * GET /api/alerts
 * Paginated alerts list with filters.
 */
alertsRouter.get("/", async (req, res) => {
  try {
    const {
      min_score,
      alert_type,
      category,
      market_ticker,
      exchange,
      limit: limitStr,
      offset: offsetStr,
    } = req.query;

    const limit = Math.min(parseInt(limitStr as string) || 50, 200);
    const offset = parseInt(offsetStr as string) || 0;

    const conditions = [];

    if (min_score) {
      conditions.push(gte(schema.alerts.anomaly_score, parseFloat(min_score as string)));
    }
    if (alert_type) {
      conditions.push(eq(schema.alerts.alert_type, alert_type as string));
    }
    if (market_ticker) {
      conditions.push(eq(schema.alerts.market_ticker, market_ticker as string));
    }
    if (exchange) {
      conditions.push(eq(schema.alerts.exchange, exchange as string));
    }

    const where = conditions.length > 0 ? and(...conditions) : undefined;

    const rows = await db
      .select({
        id: schema.alerts.id,
        market_ticker: schema.alerts.market_ticker,
        alert_type: schema.alerts.alert_type,
        anomaly_score: schema.alerts.anomaly_score,
        trade_id: schema.alerts.trade_id,
        explanation: schema.alerts.explanation,
        reason: schema.alerts.reason,
        exchange: schema.alerts.exchange,
        created_at: schema.alerts.created_at,
        market_title: schema.markets.title,
        close_time: schema.markets.close_time,
        last_price_cents: schema.markets.last_price_cents,
      })
      .from(schema.alerts)
      .leftJoin(schema.markets, eq(schema.alerts.market_ticker, schema.markets.ticker))
      .where(where)
      .orderBy(desc(schema.alerts.created_at))
      .limit(limit)
      .offset(offset);

    const alerts = rows.map((r) => ({
      ...r,
      created_at: r.created_at?.toISOString(),
      close_time: r.close_time?.toISOString() ?? null,
    }));

    res.json({ alerts, limit, offset });
  } catch (err) {
    res.status(500).json({ error: "Failed to fetch alerts" });
  }
});

/**
 * GET /api/alerts/stream
 * SSE endpoint for real-time alerts.
 */
alertsRouter.get("/stream", (req, res) => {
  const { min_score, alert_type, market_ticker } = req.query;

  sseBroadcaster.addClient(res, {
    min_score: min_score ? parseFloat(min_score as string) : undefined,
    alert_type: alert_type as string | undefined,
    market_ticker: market_ticker as string | undefined,
  });
});
