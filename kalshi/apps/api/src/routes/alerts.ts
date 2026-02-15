import { Router } from "express";
import { db, schema } from "../db/index.js";
import { desc, and, gte, eq, like, sql } from "drizzle-orm";
import { sseBroadcaster } from "../services/sse.js";
import { maybeSendEmailAlert } from "../services/email-alerts.js";
import { logger } from "../logger.js";

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
 * POST /api/alerts/test
 * Inject a synthetic high-score alert to test email delivery and SSE.
 * The alert is persisted, broadcast, and emailed just like a real one.
 */
alertsRouter.post("/test", async (req, res) => {
  try {
    const score = parseInt(req.body?.score as string) || 85;
    const exchange = (req.body?.exchange as string) || "kalshi";

    const explanation = {
      trade_size_z: 4.9,
      sweep_score: 0.08,
      price_impact_10s: null,
      late_factor: 0.0,
      depth_ratio: null,
      flow_imbalance_1m: 1.0,
      aggressiveness: 0.58,
      novelty: 1.0,
    };

    const alertRow = {
      market_ticker: "TEST-ALERT-PROBE",
      alert_type: "LARGE_LATE_PRINT",
      anomaly_score: score,
      trade_id: `test-${Date.now()}`,
      explanation,
      reason: `[TEST] Synthetic alert with score ${score} to verify email delivery pipeline.`,
      exchange: exchange as "kalshi" | "polymarket",
    };

    const [inserted] = await db
      .insert(schema.alerts)
      .values(alertRow)
      .returning();

    const alert = {
      id: inserted.id,
      market_ticker: alertRow.market_ticker,
      alert_type: alertRow.alert_type,
      anomaly_score: alertRow.anomaly_score,
      trade_id: alertRow.trade_id,
      explanation: alertRow.explanation,
      reason: alertRow.reason,
      created_at: inserted.created_at?.toISOString() ?? new Date().toISOString(),
      market_title: "Test Alert — Email Delivery Probe",
      close_time: new Date(Date.now() + 3_600_000).toISOString(),
      last_price_cents: 72,
      exchange: alertRow.exchange,
    };

    // Broadcast to SSE (will appear on dashboard)
    sseBroadcaster.broadcastAlert(alert);

    // Fire email (the whole point of this endpoint)
    await maybeSendEmailAlert({
      id: alert.id,
      market_ticker: alert.market_ticker,
      market_title: alert.market_title,
      alert_type: alert.alert_type,
      anomaly_score: alert.anomaly_score,
      reason: alert.reason,
      exchange: alert.exchange,
      close_time: alert.close_time,
      last_price_cents: alert.last_price_cents,
      explanation: alert.explanation as Record<string, unknown>,
    });

    logger.info({ alertId: alert.id, score }, "TEST alert injected");
    res.json({ ok: true, alert });
  } catch (err) {
    logger.error({ err }, "Test alert failed");
    res.status(500).json({ error: "Failed to inject test alert" });
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
