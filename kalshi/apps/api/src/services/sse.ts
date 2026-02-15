/**
 * SSE (Server-Sent Events) broadcaster.
 * Manages connected clients and broadcasts alerts + ticker updates.
 */

import { EventEmitter } from "node:events";
import type { Response } from "express";
import { logger } from "../logger.js";

interface SseClient {
  id: string;
  res: Response;
  filters?: {
    min_score?: number;
    alert_type?: string;
    market_ticker?: string;
  };
}

class SseBroadcaster extends EventEmitter {
  private clients = new Map<string, SseClient>();
  private clientCounter = 0;
  private heartbeatInterval: ReturnType<typeof setInterval> | null = null;

  constructor() {
    super();
    this.setMaxListeners(1000);
  }

  /**
   * Register a new SSE client connection.
   */
  addClient(res: Response, filters?: SseClient["filters"]): string {
    const id = `sse-${++this.clientCounter}`;

    // SSE headers
    res.writeHead(200, {
      "Content-Type": "text/event-stream",
      "Cache-Control": "no-cache",
      Connection: "keep-alive",
      "X-Accel-Buffering": "no",
    });

    // Send initial connection event
    res.write(`event: connected\ndata: ${JSON.stringify({ id })}\n\n`);

    const client: SseClient = { id, res, filters };
    this.clients.set(id, client);

    // Remove on disconnect
    res.on("close", () => {
      this.clients.delete(id);
      logger.debug({ clientId: id }, "SSE client disconnected");
    });

    logger.debug({ clientId: id, totalClients: this.clients.size }, "SSE client connected");

    // Start heartbeat if first client
    if (this.clients.size === 1) {
      this.startHeartbeat();
    }

    return id;
  }

  /**
   * Broadcast an alert to all connected clients.
   */
  broadcastAlert(alert: Record<string, unknown>): void {
    const data = JSON.stringify(alert);

    for (const client of this.clients.values()) {
      try {
        // Apply client-side filters
        if (client.filters?.min_score && (alert.anomaly_score as number) < client.filters.min_score) continue;
        if (client.filters?.alert_type && alert.alert_type !== client.filters.alert_type) continue;
        if (client.filters?.market_ticker && alert.market_ticker !== client.filters.market_ticker) continue;

        client.res.write(`event: alert\ndata: ${data}\n\n`);
      } catch {
        this.clients.delete(client.id);
      }
    }
  }

  /**
   * Broadcast a ticker update (throttled upstream).
   */
  broadcastTicker(ticker: string, data: Record<string, unknown>): void {
    const payload = JSON.stringify({ ticker, ...data });

    for (const client of this.clients.values()) {
      try {
        if (client.filters?.market_ticker && client.filters.market_ticker !== ticker) continue;
        client.res.write(`event: ticker\ndata: ${payload}\n\n`);
      } catch {
        this.clients.delete(client.id);
      }
    }
  }

  /**
   * Broadcast a trade event.
   */
  broadcastTrade(trade: Record<string, unknown>): void {
    const data = JSON.stringify(trade);

    for (const client of this.clients.values()) {
      try {
        if (client.filters?.market_ticker && trade.market_ticker !== client.filters.market_ticker) continue;
        client.res.write(`event: trade\ndata: ${data}\n\n`);
      } catch {
        this.clients.delete(client.id);
      }
    }
  }

  private startHeartbeat(): void {
    if (this.heartbeatInterval) return;
    this.heartbeatInterval = setInterval(() => {
      for (const client of this.clients.values()) {
        try {
          client.res.write(`event: heartbeat\ndata: ${JSON.stringify({ ts: Date.now() })}\n\n`);
        } catch {
          this.clients.delete(client.id);
        }
      }
      // Stop heartbeat if no clients
      if (this.clients.size === 0 && this.heartbeatInterval) {
        clearInterval(this.heartbeatInterval);
        this.heartbeatInterval = null;
      }
    }, 15_000);
  }

  get clientCount(): number {
    return this.clients.size;
  }
}

export const sseBroadcaster = new SseBroadcaster();
