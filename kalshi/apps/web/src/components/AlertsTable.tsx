"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import Link from "next/link";
import { useSSE } from "@/hooks/useSSE";
import { getAlerts, type AlertRow } from "@/lib/api";
import { timeAgo, formatTtc, centsToProb, alertTypeBadge } from "@/lib/format";
import { ScoreBar } from "./ScoreBar";
import { ExchangeBadge, ExchangeFilter } from "./ExchangeBadge";
import { InfoTip } from "./InfoTip";

interface Filters {
  min_score: number;
  alert_type: string;
  exchange: string;
}

export function AlertsTable() {
  const [alerts, setAlerts] = useState<AlertRow[]>([]);
  const [loading, setLoading] = useState(true);
  const [filters, setFilters] = useState<Filters>({ min_score: 0, alert_type: "", exchange: "" });
  const alertsRef = useRef(alerts);
  alertsRef.current = alerts;

  useEffect(() => {
    getAlerts({ limit: 100, min_score: filters.min_score || undefined, alert_type: filters.alert_type || undefined, exchange: filters.exchange || undefined })
      .then((data) => {
        setAlerts(data.alerts);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [filters]);

  const handleAlert = useCallback((data: Record<string, unknown>) => {
    const alert = data as unknown as AlertRow;
    setAlerts((prev) => {
      const next = [alert, ...prev.filter((a) => a.id !== alert.id)];
      return next.slice(0, 200);
    });
  }, []);

  const { connected } = useSSE({
    url: `/api/alerts/stream${filters.min_score ? `?min_score=${filters.min_score}` : ""}`,
    onAlert: handleAlert,
  });

  return (
    <div>
      {/* Filter bar */}
      <div className="surface flex items-center gap-4 mb-4 !p-3">
        <div className="flex items-center gap-2">
          <label className="text-[11px] text-raven-muted uppercase tracking-wider font-semibold">
            Min Score
            <InfoTip title="Minimum Anomaly Score">
              <p>Filters the alert feed to only show trades that scored at or above this threshold (0-100).</p>
              <ul>
                <li><b>0-30</b>: Routine activity.</li>
                <li><b>30-50</b>: Mildly unusual.</li>
                <li><b>50-70</b>: Notable — outsized or late flow.</li>
                <li><b>70+</b>: High conviction — rare, aggressive prints.</li>
              </ul>
              <p><b>Desk view</b>: Start at 0 to get a feel for flow, then raise to 40-50 to cut noise.</p>
            </InfoTip>
          </label>
          <input
            type="range"
            min={0}
            max={100}
            value={filters.min_score}
            onChange={(e) => setFilters((f) => ({ ...f, min_score: parseInt(e.target.value) }))}
            className="w-24 accent-accent-green"
          />
          <span className="text-xs font-mono text-raven-muted2 w-6">{filters.min_score}</span>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-[11px] text-raven-muted uppercase tracking-wider font-semibold">
            Type
            <InfoTip title="Alert Type Filter">
              <p>Narrow to a specific anomaly pattern:</p>
              <ul>
                <li><b>Late Print</b>: Large trade near expiry — last-minute conviction.</li>
                <li><b>Sweep</b>: Trade ate through multiple book levels — aggressive.</li>
                <li><b>Impact</b>: Trade moved the market price.</li>
                <li><b>Imbalance</b>: Sustained one-direction flow over 1 minute.</li>
              </ul>
            </InfoTip>
          </label>
          <select
            value={filters.alert_type}
            onChange={(e) => setFilters((f) => ({ ...f, alert_type: e.target.value }))}
            className="bg-white border border-raven-border text-[13px] rounded-[10px] px-2.5 py-1 text-raven-text"
          >
            <option value="">All</option>
            <option value="LARGE_LATE_PRINT">Late Print</option>
            <option value="LIQUIDITY_SWEEP">Sweep</option>
            <option value="FAST_PRICE_IMPACT">Impact</option>
            <option value="SUSTAINED_IMBALANCE">Imbalance</option>
          </select>
        </div>

        <ExchangeFilter
          value={filters.exchange}
          onChange={(v) => setFilters((f) => ({ ...f, exchange: v }))}
        />

        <div className="ml-auto flex items-center gap-2">
          <div className={`w-2 h-2 rounded-full ${connected ? "bg-accent-green" : "bg-accent-red"}`} />
          <span className="text-xs text-raven-muted font-medium">{connected ? "Live" : "Reconnecting..."}</span>
        </div>
      </div>

      {/* Table */}
      <div className="surface !p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px] text-raven-muted uppercase tracking-wider border-b border-raven-border">
                <th className="py-2.5 px-3 font-semibold">
                  Time
                  <InfoTip title="Alert Time">
                    <p>How long ago this alert was generated. Recent alerts represent live, actionable flow.</p>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-2 w-8 font-semibold">
                  Exch
                  <InfoTip title="Exchange">
                    <p><b>K</b> = Kalshi (U.S. regulated). <b>P</b> = Polymarket (crypto-settled).</p>
                    <p><b>Desk view</b>: Divergent flow across exchanges on the same event is a strong signal.</p>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-3 font-semibold">
                  Market
                  <InfoTip title="Market">
                    <p>The prediction market question being traded. Click to drill into charts, book, and tape.</p>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-3 font-semibold">
                  Score
                  <InfoTip title="Anomaly Score">
                    <p>Composite 0-100 score. Higher = more unusual. Combines size, timing, impact, depth, and flow direction.</p>
                    <ul>
                      <li><b>50-70</b>: Clearly anomalous.</li>
                      <li><b>70+</b>: Rare and aggressive — the flow you came to find.</li>
                    </ul>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-3 font-semibold">
                  Type
                  <InfoTip title="Alert Type">
                    <p><b>Late Print</b>: Near expiry. <b>Sweep</b>: Ate the book. <b>Impact</b>: Moved price. <b>Imbalance</b>: Sustained pressure.</p>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-3 font-semibold">
                  T-Close
                  <InfoTip title="Time to Close">
                    <p>Time remaining before market expires. Trades near close carry the strongest signal.</p>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-3 font-semibold">
                  Price
                  <InfoTip title="Yes Price">
                    <p>Market-implied probability for &ldquo;Yes.&rdquo;</p>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-3 font-semibold">
                  Size Z
                  <InfoTip title="Trade Size Z-Score">
                    <p>Standard deviations above average trade size. 3+ = extremely outsized.</p>
                  </InfoTip>
                </th>
                <th className="py-2.5 px-3 font-semibold">
                  Reason
                  <InfoTip title="Alert Reason">
                    <p>Human-readable explanation of why this trade was flagged.</p>
                  </InfoTip>
                </th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr>
                  <td colSpan={9} className="py-8 text-center text-raven-muted">Loading alerts...</td>
                </tr>
              ) : alerts.length === 0 ? (
                <tr>
                  <td colSpan={9} className="py-8 text-center text-raven-muted">No alerts yet. Monitoring markets...</td>
                </tr>
              ) : (
                alerts.map((alert) => (
                  <AlertRowComponent key={alert.id} alert={alert} />
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function AlertRowComponent({ alert }: { alert: AlertRow }) {
  const badge = alertTypeBadge(alert.alert_type);
  const explanation = alert.explanation as Record<string, number | string>;

  return (
    <tr className="alert-row border-b border-raven-line transition-colors animate-slide-in">
      <td className="py-2.5 px-3 font-mono text-xs text-raven-muted">
        {timeAgo(alert.created_at)}
      </td>
      <td className="py-2.5 px-2">
        <ExchangeBadge exchange={alert.exchange} />
      </td>
      <td className="py-2.5 px-3">
        <Link
          href={`/market/${alert.market_ticker}`}
          className="text-[var(--blue)] hover:underline font-medium"
        >
          {alert.market_title ?? alert.market_ticker}
        </Link>
      </td>
      <td className="py-2.5 px-3">
        <ScoreBar score={alert.anomaly_score} />
      </td>
      <td className="py-2.5 px-3">
        <span className={`px-2 py-0.5 text-xs font-semibold rounded-full ${badge.color}`}>
          {badge.label}
        </span>
      </td>
      <td className="py-2.5 px-3 font-mono text-xs text-raven-muted">
        {formatTtc(alert.close_time ?? null)}
      </td>
      <td className="py-2.5 px-3 font-mono text-xs text-raven-text">
        {centsToProb(alert.last_price_cents ?? null)}
      </td>
      <td className="py-2.5 px-3 font-mono text-xs text-raven-muted">
        {typeof explanation.trade_size_z === "number" ? explanation.trade_size_z.toFixed(1) : "—"}
      </td>
      <td className="py-2.5 px-3 text-xs text-raven-muted max-w-[300px] truncate" title={alert.reason}>
        {alert.reason}
      </td>
    </tr>
  );
}
