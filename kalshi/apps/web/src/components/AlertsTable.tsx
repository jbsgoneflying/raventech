"use client";

import { useCallback, useEffect, useState, useRef } from "react";
import { useSSE } from "@/hooks/useSSE";
import { getAlerts, type AlertRow } from "@/lib/api";
import { timeAgo, formatTtc, centsToProb, alertTypeBadge } from "@/lib/format";
import { ScoreBar } from "./ScoreBar";
import { ExchangeBadge, ExchangeFilter } from "./ExchangeBadge";

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

  // Load initial alerts
  useEffect(() => {
    getAlerts({ limit: 100, min_score: filters.min_score || undefined, alert_type: filters.alert_type || undefined, exchange: filters.exchange || undefined })
      .then((data) => {
        setAlerts(data.alerts);
        setLoading(false);
      })
      .catch(() => setLoading(false));
  }, [filters]);

  // SSE for real-time updates
  const handleAlert = useCallback((data: Record<string, unknown>) => {
    const alert = data as unknown as AlertRow;
    setAlerts((prev) => {
      // Prepend new alert, keep max 200
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
      <div className="flex items-center gap-4 mb-4 p-3 bg-surface-50 rounded-lg border border-surface-300">
        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500 uppercase tracking-wider">Min Score</label>
          <input
            type="range"
            min={0}
            max={100}
            value={filters.min_score}
            onChange={(e) => setFilters((f) => ({ ...f, min_score: parseInt(e.target.value) }))}
            className="w-24 accent-accent-green"
          />
          <span className="text-xs font-mono text-gray-400 w-6">{filters.min_score}</span>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500 uppercase tracking-wider">Type</label>
          <select
            value={filters.alert_type}
            onChange={(e) => setFilters((f) => ({ ...f, alert_type: e.target.value }))}
            className="bg-surface-200 border border-surface-300 text-sm rounded px-2 py-1 text-gray-300"
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
          <div className={`w-2 h-2 rounded-full ${connected ? "bg-green-500" : "bg-red-500"}`} />
          <span className="text-xs text-gray-500">{connected ? "Live" : "Reconnecting..."}</span>
        </div>
      </div>

      {/* Table */}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-surface-300">
              <th className="pb-2 pr-3">Time</th>
              <th className="pb-2 pr-2 w-8">Exch</th>
              <th className="pb-2 pr-3">Market</th>
              <th className="pb-2 pr-3">Score</th>
              <th className="pb-2 pr-3">Type</th>
              <th className="pb-2 pr-3">T-Close</th>
              <th className="pb-2 pr-3">Price</th>
              <th className="pb-2 pr-3">Size Z</th>
              <th className="pb-2">Reason</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr>
                <td colSpan={9} className="py-8 text-center text-gray-500">
                  Loading alerts...
                </td>
              </tr>
            ) : alerts.length === 0 ? (
              <tr>
                <td colSpan={9} className="py-8 text-center text-gray-500">
                  No alerts yet. Monitoring markets...
                </td>
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
  );
}

function AlertRowComponent({ alert }: { alert: AlertRow }) {
  const badge = alertTypeBadge(alert.alert_type);
  const explanation = alert.explanation as Record<string, number | string>;

  return (
    <tr className="alert-row border-b border-surface-200/50 hover:bg-surface-100/50 transition-colors animate-slide-in">
      <td className="py-2.5 pr-3 font-mono text-xs text-gray-400">
        {timeAgo(alert.created_at)}
      </td>
      <td className="py-2.5 pr-2">
        <ExchangeBadge exchange={alert.exchange} />
      </td>
      <td className="py-2.5 pr-3">
        <a
          href={`/market/${alert.market_ticker}`}
          className="text-blue-400 hover:text-blue-300 hover:underline"
        >
          {alert.market_title ?? alert.market_ticker}
        </a>
      </td>
      <td className="py-2.5 pr-3">
        <ScoreBar score={alert.anomaly_score} />
      </td>
      <td className="py-2.5 pr-3">
        <span className={`px-2 py-0.5 text-xs rounded-full ${badge.color}`}>
          {badge.label}
        </span>
      </td>
      <td className="py-2.5 pr-3 font-mono text-xs text-gray-400">
        {formatTtc(alert.close_time ?? null)}
      </td>
      <td className="py-2.5 pr-3 font-mono text-xs">
        {centsToProb(alert.last_price_cents ?? null)}
      </td>
      <td className="py-2.5 pr-3 font-mono text-xs text-gray-400">
        {typeof explanation.trade_size_z === "number" ? explanation.trade_size_z.toFixed(1) : "—"}
      </td>
      <td className="py-2.5 text-xs text-gray-400 max-w-[300px] truncate" title={alert.reason}>
        {alert.reason}
      </td>
    </tr>
  );
}
