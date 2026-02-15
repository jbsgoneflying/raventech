"use client";

import { ScoreBar } from "./ScoreBar";
import { timeAgo, alertTypeBadge } from "@/lib/format";
import type { AlertRow } from "@/lib/api";

export function AlertHistory({ alerts }: { alerts: AlertRow[] }) {
  return (
    <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">
        Alert History
        <span className="text-xs text-gray-600 ml-2">({alerts.length})</span>
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
