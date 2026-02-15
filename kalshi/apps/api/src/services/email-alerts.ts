/**
 * Email alert service: sends high-conviction alerts via Resend.
 * Includes per-hour rate limiting to avoid inbox flooding.
 */

import { Resend } from "resend";
import { logger } from "../logger.js";

// ─── Configuration ──────────────────────────────────────────────

interface EmailConfig {
  enabled: boolean;
  apiKey: string;
  from: string;
  to: string[];
  scoreThreshold: number;
  maxPerHour: number;
}

let _config: EmailConfig = {
  enabled: false,
  apiKey: "",
  from: "Flow Monitor <alerts@raven-tech.co>",
  to: [],
  scoreThreshold: 70,
  maxPerHour: 10,
};

let _resend: Resend | null = null;

/**
 * Initialize email alerting from environment variables.
 * Call once at startup.
 */
export function initEmailAlerts(): void {
  const apiKey = process.env.RESEND_API_KEY ?? "";
  const to = (process.env.EMAIL_ALERT_TO ?? "").split(",").map((s) => s.trim()).filter(Boolean);
  const enabled = process.env.EMAIL_ALERTS_ENABLED === "1" && apiKey.length > 0 && to.length > 0;

  _config = {
    enabled,
    apiKey,
    from: process.env.EMAIL_ALERT_FROM ?? "Flow Monitor <alerts@raven-tech.co>",
    to,
    scoreThreshold: parseInt(process.env.EMAIL_SCORE_THRESHOLD ?? "70", 10),
    maxPerHour: parseInt(process.env.EMAIL_MAX_PER_HOUR ?? "10", 10),
  };

  if (enabled) {
    _resend = new Resend(apiKey);
    logger.info(
      { to: _config.to, threshold: _config.scoreThreshold, maxPerHour: _config.maxPerHour },
      "Email alerts ENABLED"
    );
  } else {
    logger.info("Email alerts disabled (set EMAIL_ALERTS_ENABLED=1, RESEND_API_KEY, EMAIL_ALERT_TO to enable)");
  }
}

// ─── Rate limiting ──────────────────────────────────────────────

const _sentTimestamps: number[] = [];

function isRateLimited(): boolean {
  const now = Date.now();
  const oneHourAgo = now - 3_600_000;

  // Prune old entries
  while (_sentTimestamps.length > 0 && _sentTimestamps[0] < oneHourAgo) {
    _sentTimestamps.shift();
  }

  return _sentTimestamps.length >= _config.maxPerHour;
}

function recordSent(): void {
  _sentTimestamps.push(Date.now());
}

// ─── Alert payload ──────────────────────────────────────────────

export interface EmailAlertPayload {
  id: string | number;
  market_ticker: string;
  market_title: string;
  alert_type: string;
  anomaly_score: number;
  reason: string;
  exchange: string;
  close_time?: string;
  last_price_cents?: number;
  explanation: Record<string, unknown>;
}

// ─── Send logic ─────────────────────────────────────────────────

/**
 * Attempt to send an email alert if the score meets the threshold.
 * Non-blocking: errors are logged but never thrown.
 */
export async function maybeSendEmailAlert(alert: EmailAlertPayload): Promise<void> {
  if (!_config.enabled || !_resend) return;
  if (alert.anomaly_score < _config.scoreThreshold) return;

  if (isRateLimited()) {
    logger.warn(
      { alertId: alert.id, score: alert.anomaly_score },
      "Email rate limit reached, skipping email"
    );
    return;
  }

  try {
    const html = buildAlertEmail(alert);
    const subject = `[${alert.anomaly_score}] ${alertTypeLabel(alert.alert_type)} — ${alert.market_title}`;

    await _resend.emails.send({
      from: _config.from,
      to: _config.to,
      subject,
      html,
    });

    recordSent();

    logger.info(
      { alertId: alert.id, score: alert.anomaly_score, to: _config.to },
      "Email alert sent"
    );
  } catch (err) {
    logger.error({ err, alertId: alert.id }, "Failed to send email alert");
  }
}

// ─── Helpers ────────────────────────────────────────────────────

function alertTypeLabel(type: string): string {
  switch (type) {
    case "LARGE_LATE_PRINT": return "Late Print";
    case "LIQUIDITY_SWEEP": return "Sweep";
    case "FAST_PRICE_IMPACT": return "Impact";
    case "SUSTAINED_IMBALANCE": return "Imbalance";
    default: return type;
  }
}

function exchangeLabel(ex: string): string {
  return ex === "kalshi" ? "Kalshi" : ex === "polymarket" ? "Polymarket" : ex;
}

function formatPrice(cents?: number): string {
  if (cents === undefined || cents === null) return "—";
  return `${cents}%`;
}

function formatTimeToClose(closeTime?: string): string {
  if (!closeTime) return "—";
  const diff = new Date(closeTime).getTime() - Date.now();
  if (diff < 0) return "Expired";
  if (diff < 3_600_000) return `${Math.round(diff / 60_000)}m`;
  if (diff < 86_400_000) return `${Math.round(diff / 3_600_000)}h`;
  return `${Math.round(diff / 86_400_000)}d`;
}

function featureRow(label: string, value: unknown, format: "f1" | "f2" | "pct" | "pts"): string {
  if (value === null || value === undefined) return "";
  const v = Number(value);
  if (!Number.isFinite(v)) return "";
  let display: string;
  switch (format) {
    case "f1": display = v.toFixed(1); break;
    case "f2": display = v.toFixed(2); break;
    case "pct": display = `${(v * 100).toFixed(0)}%`; break;
    case "pts": display = `${v > 0 ? "+" : ""}${v.toFixed(1)} pts`; break;
  }
  return `
    <tr>
      <td style="padding:4px 12px 4px 0;color:#94a3b8;font-size:13px;">${label}</td>
      <td style="padding:4px 0;color:#e2e8f0;font-family:monospace;font-size:13px;text-align:right;">${display}</td>
    </tr>`;
}

// ─── HTML Template ──────────────────────────────────────────────

function buildAlertEmail(alert: EmailAlertPayload): string {
  const exp = alert.explanation || {};
  const scoreColor = alert.anomaly_score >= 80 ? "#ef4444" : alert.anomaly_score >= 70 ? "#f59e0b" : "#10b981";
  const typeLabel = alertTypeLabel(alert.alert_type);
  const typeBadgeColor =
    alert.alert_type === "LARGE_LATE_PRINT" ? "#c084fc" :
    alert.alert_type === "LIQUIDITY_SWEEP" ? "#f59e0b" :
    alert.alert_type === "FAST_PRICE_IMPACT" ? "#ef4444" :
    "#3b82f6";

  const features = [
    featureRow("Trade Size Z", exp.trade_size_z, "f1"),
    featureRow("Sweep Score", exp.sweep_score, "pct"),
    featureRow("Impact 10s", exp.price_impact_10s, "pts"),
    featureRow("Late Factor", exp.late_factor, "f2"),
    featureRow("Depth Ratio", exp.depth_ratio, "f2"),
    featureRow("Flow Imbalance", exp.flow_imbalance_1m, "pct"),
    featureRow("Aggressiveness", exp.aggressiveness, "pct"),
    featureRow("Novelty", exp.novelty, "f2"),
  ].filter(Boolean).join("");

  return `
<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#0f0f17;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#0f0f17;padding:24px 16px;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background:#16161e;border-radius:12px;border:1px solid #2a2a36;overflow:hidden;">

        <!-- Header -->
        <tr>
          <td style="padding:20px 24px;border-bottom:1px solid #2a2a36;">
            <table width="100%" cellpadding="0" cellspacing="0">
              <tr>
                <td style="color:#94a3b8;font-size:12px;letter-spacing:1px;text-transform:uppercase;">
                  Raven Tech · Flow Monitor
                </td>
                <td align="right">
                  <span style="display:inline-block;background:${scoreColor};color:#fff;font-weight:700;font-size:18px;padding:4px 14px;border-radius:8px;">
                    ${alert.anomaly_score}
                  </span>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Market Title -->
        <tr>
          <td style="padding:20px 24px 8px;">
            <div style="font-size:11px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:6px;">
              <span style="display:inline-block;background:${typeBadgeColor}22;color:${typeBadgeColor};padding:2px 8px;border-radius:10px;font-size:11px;font-weight:600;">
                ${typeLabel}
              </span>
              <span style="margin-left:8px;">${exchangeLabel(alert.exchange)}</span>
            </div>
            <div style="font-size:18px;font-weight:600;color:#f1f5f9;line-height:1.3;">
              ${alert.market_title}
            </div>
            <div style="font-family:monospace;font-size:12px;color:#64748b;margin-top:4px;">
              ${alert.market_ticker}
            </div>
          </td>
        </tr>

        <!-- Quick Stats -->
        <tr>
          <td style="padding:12px 24px;">
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#1e1e28;border-radius:8px;">
              <tr>
                <td style="padding:12px 16px;text-align:center;border-right:1px solid #2a2a36;">
                  <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Price</div>
                  <div style="font-size:16px;font-weight:600;color:#e2e8f0;font-family:monospace;margin-top:2px;">${formatPrice(alert.last_price_cents)}</div>
                </td>
                <td style="padding:12px 16px;text-align:center;border-right:1px solid #2a2a36;">
                  <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">T-Close</div>
                  <div style="font-size:16px;font-weight:600;color:#e2e8f0;font-family:monospace;margin-top:2px;">${formatTimeToClose(alert.close_time)}</div>
                </td>
                <td style="padding:12px 16px;text-align:center;">
                  <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;">Exchange</div>
                  <div style="font-size:16px;font-weight:600;color:#e2e8f0;margin-top:2px;">${exchangeLabel(alert.exchange)}</div>
                </td>
              </tr>
            </table>
          </td>
        </tr>

        <!-- Reason -->
        <tr>
          <td style="padding:4px 24px 16px;">
            <div style="background:#1e1e28;border-radius:8px;padding:12px 16px;border-left:3px solid ${scoreColor};">
              <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:4px;">Why it was flagged</div>
              <div style="font-size:13px;color:#cbd5e1;line-height:1.5;">${alert.reason}</div>
            </div>
          </td>
        </tr>

        <!-- Features Grid -->
        ${features ? `
        <tr>
          <td style="padding:4px 24px 20px;">
            <div style="font-size:10px;color:#64748b;text-transform:uppercase;letter-spacing:0.5px;margin-bottom:8px;">Anomaly Features</div>
            <table width="100%" cellpadding="0" cellspacing="0" style="background:#1e1e28;border-radius:8px;padding:8px 12px;">
              ${features}
            </table>
          </td>
        </tr>
        ` : ""}

        <!-- CTA -->
        <tr>
          <td style="padding:8px 24px 24px;" align="center">
            <a href="https://app.raven-tech.co/flow-monitor/market/${encodeURIComponent(alert.market_ticker)}"
               style="display:inline-block;background:#3b82f6;color:#fff;font-weight:600;font-size:14px;padding:10px 28px;border-radius:8px;text-decoration:none;">
              View Market Detail →
            </a>
          </td>
        </tr>

        <!-- Footer -->
        <tr>
          <td style="padding:16px 24px;border-top:1px solid #2a2a36;text-align:center;">
            <div style="font-size:11px;color:#475569;">
              Raven Tech Flow Monitor · Score threshold: ${_config.scoreThreshold}+ · Rate limit: ${_config.maxPerHour}/hr
            </div>
          </td>
        </tr>

      </table>
    </td></tr>
  </table>
</body>
</html>`;
}
