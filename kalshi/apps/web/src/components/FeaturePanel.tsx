"use client";

import React from "react";
import { InfoTip } from "./InfoTip";

interface Props {
  alerts: Array<{
    explanation: Record<string, unknown>;
    anomaly_score: number;
    alert_type: string;
    reason: string;
    created_at: string;
  }>;
}

export function FeaturePanel({ alerts }: Props) {
  const latest = alerts[0];

  if (!latest) {
    return (
      <div className="surface">
        <h3 className="text-[13px] font-semibold text-raven-muted mb-3">Computed Features</h3>
        <div className="py-4 text-center text-raven-muted2 text-sm">
          No alerts generated for this market yet.
        </div>
      </div>
    );
  }

  const exp = latest.explanation;

  const features: Array<{ label: string; value: unknown; format: string; tipTitle: string; tipContent: React.ReactNode }> = [
    {
      label: "Trade Size Z",
      value: exp.trade_size_z,
      format: "f1",
      tipTitle: "Trade Size Z-Score",
      tipContent: <p>Standard deviations above mean trade size. 4+ = likely institutional.</p>,
    },
    {
      label: "Sweep Score",
      value: exp.sweep_score,
      format: "pct",
      tipTitle: "Sweep Score",
      tipContent: <p>Fraction of resting depth consumed. 100% = ate every resting order.</p>,
    },
    {
      label: "Impact 10s",
      value: exp.price_impact_10s,
      format: "pts",
      tipTitle: "Price Impact (10s)",
      tipContent: <p>Price movement in 10 seconds after the trade. Positive = confirming direction.</p>,
    },
    {
      label: "Late Factor",
      value: exp.late_factor,
      format: "f2",
      tipTitle: "Late Factor",
      tipContent: <p>How close to expiry the trade occurred. Higher = more conviction under time pressure.</p>,
    },
    {
      label: "Depth Ratio",
      value: exp.depth_ratio,
      format: "f2",
      tipTitle: "Depth Ratio",
      tipContent: <p>Trade size vs best-level depth. &gt;1.0 = trade walked the book.</p>,
    },
    {
      label: "Flow Imbalance",
      value: exp.flow_imbalance_1m,
      format: "pct",
      tipTitle: "Flow Imbalance (1 min)",
      tipContent: <p>Net directional bias over 60 seconds. 80%+ = strong one-way pressure.</p>,
    },
    {
      label: "Aggressiveness",
      value: exp.aggressiveness,
      format: "pct",
      tipTitle: "Aggressiveness",
      tipContent: <p>How far from mid the trade executed. 100% = crossed full spread for immediate fill.</p>,
    },
    {
      label: "Novelty",
      value: exp.novelty,
      format: "f2",
      tipTitle: "Novelty",
      tipContent: <p>How unusual vs recent activity. 1.0 = first big print in a quiet market.</p>,
    },
  ];

  return (
    <div className="surface">
      <h3 className="text-[13px] font-semibold text-raven-muted mb-3">
        Latest Features
        <span className="text-xs text-raven-muted2 ml-2">(from most recent alert)</span>
        <InfoTip title="Computed Features">
          <p>Raw signal features from the anomaly engine. Each captures a different dimension of why the trade was unusual.</p>
        </InfoTip>
      </h3>

      <div className="grid grid-cols-2 gap-2">
        {features.map(({ label, value, format, tipTitle, tipContent }) => (
          <div key={label} className="flex items-center justify-between px-2 py-1.5 rounded-lg bg-raven-hover border border-raven-line">
            <span className="text-xs text-raven-muted flex items-center font-medium">
              {label}
              <InfoTip title={tipTitle}>{tipContent}</InfoTip>
            </span>
            <span className="text-xs font-mono text-raven-text font-semibold">
              {formatValue(value as number | null | undefined, format)}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}

function formatValue(v: number | null | undefined, fmt: string): string {
  if (v === null || v === undefined) return "—";
  switch (fmt) {
    case "f1": return v.toFixed(1);
    case "f2": return v.toFixed(2);
    case "pct": return `${(v * 100).toFixed(0)}%`;
    case "pts": return `${v > 0 ? "+" : ""}${v.toFixed(1)} pts`;
    default: return String(v);
  }
}
