/**
 * Polymarket WebSocket client for real-time market data.
 * Connects to the CLOB market channel and subscribes by asset_ids.
 *
 * Events emitted:
 *  - "trade"              : last_trade_price message
 *  - "book"               : full orderbook snapshot
 *  - "price_change"       : bid/ask updates
 *  - "best_bid_ask"       : best quote updates (requires custom_feature_enabled)
 *  - "market_resolved"    : lifecycle event
 *  - "error"              : connection errors
 *  - "connected"          : successfully connected
 *  - "disconnected"       : connection lost
 */

import { EventEmitter } from "events";
import WebSocket from "ws";
import { polymarketConfig } from "../config.js";
import { logger } from "../logger.js";

const PING_INTERVAL_MS = 10_000; // Polymarket requires manual pings
const MAX_RECONNECT_DELAY_MS = 30_000;
const INITIAL_RECONNECT_DELAY_MS = 1_000;

export class PolymarketWsClient extends EventEmitter {
  private ws: WebSocket | null = null;
  private subscribedAssets: Set<string> = new Set();
  private pingTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
  private intentionallyClosed = false;
  private _stats = {
    connected: false,
    reconnects: 0,
    messagesReceived: 0,
  };

  get stats() {
    return { ...this._stats };
  }

  // ─── Connection ─────────────────────────────────────────────

  async connect(): Promise<void> {
    this.intentionallyClosed = false;
    return new Promise((resolve, reject) => {
      try {
        this.ws = new WebSocket(polymarketConfig.wsUrl);

        this.ws.on("open", () => {
          logger.info("Polymarket WS connected");
          this._stats.connected = true;
          this.reconnectDelay = INITIAL_RECONNECT_DELAY_MS;
          this.startPing();

          // Re-subscribe to all tracked assets
          if (this.subscribedAssets.size > 0) {
            this.sendSubscribe([...this.subscribedAssets]);
          }

          this.emit("connected");
          resolve();
        });

        this.ws.on("message", (data: Buffer | string) => {
          this._stats.messagesReceived++;
          try {
            const msg = JSON.parse(data.toString());
            this.handleMessage(msg);
          } catch (err) {
            logger.debug({ err }, "Polymarket WS parse error");
          }
        });

        this.ws.on("close", (code, reason) => {
          this._stats.connected = false;
          this.stopPing();
          this.emit("disconnected", { code, reason: reason?.toString() });

          if (!this.intentionallyClosed) {
            logger.warn({ code, reason: reason?.toString() }, "Polymarket WS disconnected, scheduling reconnect");
            this.scheduleReconnect();
          }
        });

        this.ws.on("error", (err) => {
          logger.error({ err }, "Polymarket WS error");
          this.emit("error", err);
          // First connection attempt: reject the promise
          reject(err);
        });
      } catch (err) {
        reject(err);
      }
    });
  }

  disconnect(): void {
    this.intentionallyClosed = true;
    this.stopPing();
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
    if (this.ws) {
      this.ws.close();
      this.ws = null;
    }
    this._stats.connected = false;
  }

  // ─── Subscription ───────────────────────────────────────────

  /**
   * Subscribe to real-time updates for given asset IDs (token IDs).
   * Can be called before or after connection -- assets are tracked
   * and re-subscribed on reconnect.
   */
  subscribe(assetIds: string[]): void {
    const newIds = assetIds.filter((id) => !this.subscribedAssets.has(id));
    if (newIds.length === 0) return;

    for (const id of newIds) {
      this.subscribedAssets.add(id);
    }

    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.sendSubscribe(newIds);
    }

    logger.info({ count: newIds.length, total: this.subscribedAssets.size }, "Polymarket WS: subscribed assets");
  }

  /**
   * Unsubscribe from specific asset IDs.
   */
  unsubscribe(assetIds: string[]): void {
    for (const id of assetIds) {
      this.subscribedAssets.delete(id);
    }
    // No explicit unsubscribe message in Polymarket WS protocol --
    // we just stop tracking and ignore events for removed assets.
  }

  // ─── Internal ───────────────────────────────────────────────

  private sendSubscribe(assetIds: string[]): void {
    if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;

    // Polymarket expects batches of asset_ids per subscription message
    // Send in chunks to avoid overly large messages
    const chunkSize = 50;
    for (let i = 0; i < assetIds.length; i += chunkSize) {
      const chunk = assetIds.slice(i, i + chunkSize);
      const msg = JSON.stringify({
        assets_ids: chunk,
        type: "market",
        custom_feature_enabled: true,
      });
      this.ws.send(msg);
    }
  }

  private handleMessage(msg: Record<string, unknown>): void {
    // Polymarket WS sends an array of events
    const events = Array.isArray(msg) ? msg : [msg];

    for (const event of events) {
      const eventType = event.event_type as string | undefined;

      switch (eventType) {
        case "last_trade_price":
          this.emit("trade", event);
          break;

        case "book":
          this.emit("book", event);
          break;

        case "price_change":
          this.emit("price_change", event);
          break;

        case "best_bid_ask":
          this.emit("best_bid_ask", event);
          break;

        case "market_resolved":
          this.emit("market_resolved", event);
          break;

        default:
          // Pong, heartbeat, or unknown events
          logger.debug({ eventType }, "Polymarket WS unhandled event type");
          break;
      }
    }
  }

  private startPing(): void {
    this.stopPing();
    this.pingTimer = setInterval(() => {
      if (this.ws && this.ws.readyState === WebSocket.OPEN) {
        this.ws.send("PING");
      }
    }, PING_INTERVAL_MS);
  }

  private stopPing(): void {
    if (this.pingTimer) {
      clearInterval(this.pingTimer);
      this.pingTimer = null;
    }
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer) return;

    const delay = this.reconnectDelay;
    this.reconnectDelay = Math.min(this.reconnectDelay * 2, MAX_RECONNECT_DELAY_MS);
    this._stats.reconnects++;

    logger.info({ delayMs: delay }, "Polymarket WS reconnecting in...");

    this.reconnectTimer = setTimeout(async () => {
      this.reconnectTimer = null;
      try {
        await this.connect();
      } catch (err) {
        logger.warn({ err }, "Polymarket WS reconnect failed");
        if (!this.intentionallyClosed) {
          this.scheduleReconnect();
        }
      }
    }, delay);
  }
}
