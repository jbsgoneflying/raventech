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

  const features: Array<{ label: string; value: unknown; format: string; tipTitle: string; tipContent: React.ReactNode }> = [
    {
      label: "Trade Size Z",
      value: exp.trade_size_z,
      format: "f1",
      tipTitle: "Trade Size Z-Score",
      tipContent: (
        <>
          <p>Standard deviations above the mean trade size for this market over the recent window. A Z of 3.0 means this trade was 3 standard deviations larger than typical.</p>
          <ul>
            <li><b>1–2</b>: Moderately above normal.</li>
            <li><b>2–4</b>: Notably outsized — flag-worthy.</li>
            <li><b>4+</b>: Extremely large — likely institutional.</li>
          </ul>
        </>
      ),
    },
    {
      label: "Sweep Score",
      value: exp.sweep_score,
      format: "pct",
      tipTitle: "Sweep Score",
      tipContent: (
        <>
          <p>Fraction of the order book&apos;s resting depth that was consumed by this trade. 100% means the trade ate through every resting order on one side.</p>
          <p><b>Desk view</b>: High sweep + high size Z = someone who doesn&apos;t care about slippage. This is urgent, price-insensitive flow — the strongest conviction signal.</p>
        </>
      ),
    },
    {
      label: "Impact 10s",
      value: exp.price_impact_10s,
      format: "pts",
      tipTitle: "Price Impact (10 seconds)",
      tipContent: (
        <>
          <p>How much the market price moved in the 10 seconds after this trade, measured in percentage points.</p>
          <ul>
            <li><b>Positive</b>: Price moved in the trade&apos;s direction (confirming).</li>
            <li><b>Negative</b>: Price reverted (the market disagreed, or the move was faded).</li>
          </ul>
          <p><b>Desk view</b>: Positive impact with sustained follow-through is a strong signal. Immediate reversion means the trade may have been absorbed by the book.</p>
        </>
      ),
    },
    {
      label: "Late Factor",
      value: exp.late_factor,
      format: "f2",
      tipTitle: "Late Factor",
      tipContent: (
        <>
          <p>Multiplier based on how close to market expiry this trade occurred. Higher = closer to close. A factor of 2.0+ means the trade was within the final fraction of the market&apos;s life.</p>
          <p><b>Desk view</b>: Late factor is arguably the single most important feature. A large trade with a high late factor means someone is betting with maximum time-decay risk — they believe strongly in the outcome and are willing to accept the worst-case gamma.</p>
        </>
      ),
    },
    {
      label: "Depth Ratio",
      value: exp.depth_ratio,
      format: "f2",
      tipTitle: "Depth Ratio",
      tipContent: (
        <>
          <p>Ratio of the trade&apos;s size to the available depth at the best price level. A ratio &gt;1.0 means the trade was larger than what the best level could fill — it &ldquo;walked the book.&rdquo;</p>
          <p><b>Desk view</b>: High depth ratio explains <em>why</em> a trade caused impact. It tells you the trade&apos;s size relative to the market&apos;s capacity to absorb it.</p>
        </>
      ),
    },
    {
      label: "Flow Imbalance",
      value: exp.flow_imbalance_1m,
      format: "pct",
      tipTitle: "Flow Imbalance (1 min)",
      tipContent: (
        <>
          <p>Net directional bias over the last 60 seconds, expressed as a percentage from -100% (all sells) to +100% (all buys). Measured by volume on each side.</p>
          <ul>
            <li><b>80–100%</b>: Nearly all volume on one side — strong directional conviction.</li>
            <li><b>50–80%</b>: Clear lean, but some two-way flow.</li>
            <li><b>&lt;50%</b>: Mixed — no clear directional signal.</li>
          </ul>
          <p><b>Desk view</b>: Sustained imbalance (&gt;80%) across multiple alerts = momentum building. This is where you look for entry timing.</p>
        </>
      ),
    },
    {
      label: "Aggressiveness",
      value: exp.aggressiveness,
      format: "pct",
      tipTitle: "Aggressiveness",
      tipContent: (
        <>
          <p>How far the trade&apos;s execution price was from the mid-market price. 100% means the trader crossed the full spread — they paid the maximum cost to get filled immediately.</p>
          <p><b>Desk view</b>: High aggressiveness means the trader prioritized speed over price. Combined with large size, this signals urgency — they know something or believe the price is about to move.</p>
        </>
      ),
    },
    {
      label: "Novelty",
      value: exp.novelty,
      format: "f2",
      tipTitle: "Novelty",
      tipContent: (
        <>
          <p>How unusual this trade is compared to recent activity in the same market. A score of 1.0 means the trade stands out significantly from the market&apos;s recent pattern. 0.0 means it looks like normal flow.</p>
          <p><b>Desk view</b>: Novelty catches &ldquo;first mover&rdquo; trades — the first big print in a quiet market. High novelty + high score = someone is initiating a new position or thesis, not just adding to existing flow.</p>
        </>
      ),
    },
  ];

  return (
    <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">
        Latest Features
        <span className="text-xs text-gray-600 ml-2">(from most recent alert)</span>
        <InfoTip title="Computed Features">
          <p>These are the raw signal features computed by the anomaly engine for the most recent alert. Each one captures a different dimension of why the trade was unusual.</p>
          <p><b>Desk view</b>: Use these to understand <em>why</em> a trade was flagged. A high score from Size Z + Sweep tells a different story than one from Late Factor + Imbalance. Click any feature&apos;s info button for details.</p>
        </InfoTip>
      </h3>

      <div className="grid grid-cols-2 gap-2">
        {features.map(({ label, value, format, tipTitle, tipContent }) => (
          <div key={label} className="flex items-center justify-between px-2 py-1.5 rounded bg-surface-200/50">
            <span className="text-xs text-gray-500 flex items-center">
              {label}
              <InfoTip title={tipTitle}>{tipContent}</InfoTip>
            </span>
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
