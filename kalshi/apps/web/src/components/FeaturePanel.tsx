"use client";

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
  // Show the latest alert's feature values
  const latest = alerts[0];

  if (!latest) {
    return (
      <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-3">Computed Features</h3>
        <div className="py-4 text-center text-gray-600 text-sm">
          No alerts generated for this market yet.
        </div>
      </div>
    );
  }

  const exp = latest.explanation;

  const features = [
    { label: "Trade Size Z", value: exp.trade_size_z, format: "f1" },
    { label: "Sweep Score", value: exp.sweep_score, format: "pct" },
    { label: "Impact 10s", value: exp.price_impact_10s, format: "pts" },
    { label: "Late Factor", value: exp.late_factor, format: "f2" },
    { label: "Depth Ratio", value: exp.depth_ratio, format: "f2" },
    { label: "Flow Imbalance", value: exp.flow_imbalance_1m, format: "pct" },
    { label: "Aggressiveness", value: exp.aggressiveness, format: "pct" },
    { label: "Novelty", value: exp.novelty, format: "f2" },
  ];

  return (
    <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">
        Latest Features
        <span className="text-xs text-gray-600 ml-2">(from most recent alert)</span>
      </h3>

      <div className="grid grid-cols-2 gap-2">
        {features.map(({ label, value, format }) => (
          <div key={label} className="flex items-center justify-between px-2 py-1.5 rounded bg-surface-200/50">
            <span className="text-xs text-gray-500">{label}</span>
            <span className="text-xs font-mono text-gray-300">
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
