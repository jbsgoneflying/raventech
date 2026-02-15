"use client";

import { ScoreBar } from "./ScoreBar";
import { timeAgo, alertTypeBadge } from "@/lib/format";
import type { AlertRow } from "@/lib/api";
import { InfoTip } from "./InfoTip";

export function AlertHistory({ alerts }: { alerts: AlertRow[] }) {
  return (
    <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">
        Alert History
        <span className="text-xs text-gray-600 ml-2">({alerts.length})</span>
        <InfoTip title="Alert History">
          <p>All anomaly alerts generated for this specific market, ordered newest first. Each card shows the score, type, timing, and reason.</p>
          <ul>
            <li><b>Multiple alerts in quick succession</b>: Sustained institutional flow — very high signal.</li>
            <li><b>Increasing scores</b>: The flow is getting more aggressive over time.</li>
            <li><b>Mixed types</b> (Sweep + Imbalance): Confirms the move is real, not just one noisy trade.</li>
          </ul>
          <p><b>Desk view</b>: This is your conviction log. If a market has 3+ alerts in the last hour, especially with rising scores, it&apos;s time to act or build a position thesis around it.</p>
        </InfoTip>
      </h3>

      {alerts.length === 0 ? (
        <div className="py-4 text-center text-gray-600 text-sm">No alerts</div>
      ) : (
        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {alerts.map((alert) => {
            const badge = alertTypeBadge(alert.alert_type);
            return (
              <div
                key={alert.id}
                className="flex items-start gap-3 p-2 rounded bg-surface-200/30"
              >
                <ScoreBar score={alert.anomaly_score} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`px-1.5 py-0.5 text-[10px] rounded-full ${badge.color}`}>
                      {badge.label}
                    </span>
                    <span className="text-[10px] text-gray-600">{timeAgo(alert.created_at)}</span>
                  </div>
                  <p className="text-xs text-gray-400 mt-0.5 truncate">{alert.reason}</p>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
