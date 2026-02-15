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
          <label className="text-xs text-gray-500 uppercase tracking-wider">
            Min Score
            <InfoTip title="Minimum Anomaly Score">
              <p>Filters the alert feed to only show trades that scored at or above this threshold (0–100).</p>
              <ul>
                <li><b>0–30</b>: Routine activity — most flow looks like this.</li>
                <li><b>30–50</b>: Mildly unusual — worth a glance if the market matters to you.</li>
                <li><b>50–70</b>: Notable — outsized or late flow that broke the normal pattern.</li>
                <li><b>70+</b>: High conviction — rare, large, or aggressive prints that warrant immediate attention.</li>
              </ul>
              <p><b>Desk view</b>: Start at 0 to get a feel for the flow, then raise to 40–50 to cut noise and focus on actionable signals.</p>
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
          <span className="text-xs font-mono text-gray-400 w-6">{filters.min_score}</span>
        </div>

        <div className="flex items-center gap-2">
          <label className="text-xs text-gray-500 uppercase tracking-wider">
            Type
            <InfoTip title="Alert Type Filter">
              <p>Narrow to a specific anomaly pattern:</p>
              <ul>
                <li><b>Late Print</b>: A large trade that hit close to market expiry — someone is making a last-minute conviction bet.</li>
                <li><b>Sweep</b>: A trade that ate through multiple price levels of the book — aggressive and urgent.</li>
                <li><b>Impact</b>: A trade that visibly moved the market price — the book was thin or the size was heavy.</li>
                <li><b>Imbalance</b>: Sustained one-directional flow over the last minute — persistent buying or selling pressure.</li>
              </ul>
              <p><b>Desk view</b>: Late Prints near expiry + high scores are the highest signal. Imbalances show emerging momentum.</p>
            </InfoTip>
          </label>
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
              <th className="pb-2 pr-3">
                Time
                <InfoTip title="Alert Time">
                  <p>How long ago this alert was generated. Alerts appear in real time as the engine detects anomalous trades.</p>
                  <p><b>Desk view</b>: Recent alerts (seconds/minutes old) represent live, actionable flow. Older alerts are historical context.</p>
                </InfoTip>
              </th>
              <th className="pb-2 pr-2 w-8">
                Exch
                <InfoTip title="Exchange">
                  <p>Which prediction market exchange the trade occurred on.</p>
                  <ul>
                    <li><b>K</b> = Kalshi — U.S. regulated, event contracts (politics, crypto, weather, sports).</li>
                    <li><b>P</b> = Polymarket — crypto-settled, larger liquidity on politics and current events.</li>
                  </ul>
                  <p><b>Desk view</b>: Cross-reference the same event on both exchanges. Divergent flow (heavy buying on one, quiet on the other) is a strong signal.</p>
                </InfoTip>
              </th>
              <th className="pb-2 pr-3">
                Market
                <InfoTip title="Market">
                  <p>The prediction market question being traded (e.g. &ldquo;Will BTC be above $100k on March 1?&rdquo;). Click to drill into the market detail page with charts, order book, and trade tape.</p>
                  <p><b>Desk view</b>: Look for names you have a thesis on. Unusual flow into a market you&apos;re watching is a confirmation signal or early warning.</p>
                </InfoTip>
              </th>
              <th className="pb-2 pr-3">
                Score
                <InfoTip title="Anomaly Score">
                  <p>Composite score from 0–100 measuring how unusual this trade was relative to the market&apos;s recent history. Combines trade size, timing, price impact, book depth, and flow direction.</p>
                  <ul>
                    <li><b>40–50</b>: Mildly unusual, broke one or two normal patterns.</li>
                    <li><b>50–70</b>: Clearly anomalous, multiple features elevated.</li>
                    <li><b>70+</b>: Rare and aggressive — this is the flow you came here to find.</li>
                  </ul>
                  <p><b>Desk view</b>: The score answers &ldquo;how much should I care?&rdquo; — higher = more urgent. Compare scores across markets to prioritize your attention.</p>
                </InfoTip>
              </th>
              <th className="pb-2 pr-3">
                Type
                <InfoTip title="Alert Type">
                  <p>The primary anomaly pattern detected:</p>
                  <ul>
                    <li><b>Late Print</b>: Large trade near market expiry — high conviction, time-sensitive.</li>
                    <li><b>Sweep</b>: Trade ate through multiple book levels — aggressive, price-insensitive buyer/seller.</li>
                    <li><b>Impact</b>: Trade visibly moved the market price — thin book or heavy size.</li>
                    <li><b>Imbalance</b>: Sustained one-direction flow over 1 minute — persistent pressure building.</li>
                  </ul>
                </InfoTip>
              </th>
              <th className="pb-2 pr-3">
                T-Close
                <InfoTip title="Time to Close">
                  <p>How much time remains before this market expires and settles. Displayed as days, hours, or minutes.</p>
                  <p><b>Desk view</b>: Trades near close are the most informative — the trader has less time to be wrong and is accepting more gamma risk. Late prints into expiry with high scores are the strongest signals.</p>
                </InfoTip>
              </th>
              <th className="pb-2 pr-3">
                Price
                <InfoTip title="Yes Price (Probability)">
                  <p>Current market-implied probability for the &ldquo;Yes&rdquo; outcome, shown as a percentage. A market at 72% means the crowd prices a 72% chance the event occurs.</p>
                  <p><b>Desk view</b>: Extreme probabilities (90%+ or sub-10%) mean the market is already priced as near-certain. Unusual flow at those levels suggests someone disagrees or is hedging. Mid-range prices (30–70%) are where flow signals are most tradeable.</p>
                </InfoTip>
              </th>
              <th className="pb-2 pr-3">
                Size Z
                <InfoTip title="Trade Size Z-Score">
                  <p>How many standard deviations above average this trade was in size, relative to the market&apos;s recent trade history.</p>
                  <ul>
                    <li><b>1–2</b>: Somewhat larger than normal.</li>
                    <li><b>2–3</b>: Notably outsized.</li>
                    <li><b>3+</b>: Extremely large relative to this market&apos;s norms.</li>
                  </ul>
                  <p><b>Desk view</b>: A high Z-score in a market with low volume is more meaningful than the same Z in a high-volume market. Cross-reference with the Score for conviction.</p>
                </InfoTip>
              </th>
              <th className="pb-2">
                Reason
                <InfoTip title="Alert Reason">
                  <p>Human-readable explanation of why this trade was flagged. Includes the dominant feature(s), direction (buy/sell, yes/no), and key metrics.</p>
                  <p><b>Desk view</b>: Read this first for quick context. It tells you the &ldquo;what&rdquo; and &ldquo;why&rdquo; at a glance before you drill into the detail page.</p>
                </InfoTip>
              </th>
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
        <Link
          href={`/market/${alert.market_ticker}`}
          className="text-blue-400 hover:text-blue-300 hover:underline"
        >
          {alert.market_title ?? alert.market_ticker}
        </Link>
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
