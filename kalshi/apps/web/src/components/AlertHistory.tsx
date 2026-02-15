"use client";

import { ScoreBar } from "./ScoreBar";
import { timeAgo, alertTypeBadge } from "@/lib/format";
import type { AlertRow } from "@/lib/api";
import { InfoTip } from "./InfoTip";

export function AlertHistory({ alerts }: { alerts: AlertRow[] }) {
  return (
    <div className="surface">
      <h3 className="text-[13px] font-semibold text-raven-muted mb-3">
        Alert History
        <span className="text-xs text-raven-muted2 ml-2">({alerts.length})</span>
        <InfoTip title="Alert History">
          <p>All anomaly alerts for this market. Multiple alerts in quick succession with rising scores = strong institutional flow.</p>
        </InfoTip>
      </h3>

      {alerts.length === 0 ? (
        <div className="py-4 text-center text-raven-muted2 text-sm">No alerts</div>
      ) : (
        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {alerts.map((alert) => {
            const badge = alertTypeBadge(alert.alert_type);
            return (
              <div
                key={alert.id}
                className="flex items-start gap-3 p-2 rounded-lg bg-raven-hover border border-raven-line"
              >
                <ScoreBar score={alert.anomaly_score} />
                <div className="flex-1 min-w-0">
                  <div className="flex items-center gap-2">
                    <span className={`px-1.5 py-0.5 text-[10px] font-semibold rounded-full ${badge.color}`}>
                      {badge.label}
                    </span>
                    <span className="text-[10px] text-raven-muted2">{timeAgo(alert.created_at)}</span>
                  </div>
                  <p className="text-xs text-raven-muted mt-0.5 truncate">{alert.reason}</p>
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
