/**
 * Formatting utilities for the dashboard.
 */

/** Convert cents to dollar display: 56 -> "$0.56" */
export function centsToDollars(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `$${(cents / 100).toFixed(2)}`;
}

/** Convert cents to probability display: 56 -> "56%" */
export function centsToProb(cents: number | null | undefined): string {
  if (cents === null || cents === undefined) return "—";
  return `${cents}%`;
}

/** Format anomaly score with color class */
export function scoreClass(score: number): string {
  if (score >= 80) return "score-critical";
  if (score >= 60) return "score-high";
  if (score >= 40) return "score-medium";
  return "score-low";
}

/** Format time-to-close in human-readable form */
export function formatTtc(closeTime: string | null): string {
  if (!closeTime) return "—";
  const ms = new Date(closeTime).getTime() - Date.now();
  if (ms <= 0) return "Closed";
  const s = ms / 1000;
  if (s < 60) return `${Math.round(s)}s`;
  if (s < 3600) return `${Math.round(s / 60)}m`;
  if (s < 86400) return `${(s / 3600).toFixed(1)}h`;
  return `${Math.round(s / 86400)}d`;
}

/** Format relative time: "2m ago", "1h ago" */
export function timeAgo(iso: string): string {
  const ms = Date.now() - new Date(iso).getTime();
  const s = ms / 1000;
  if (s < 10) return "just now";
  if (s < 60) return `${Math.round(s)}s ago`;
  if (s < 3600) return `${Math.round(s / 60)}m ago`;
  if (s < 86400) return `${Math.round(s / 3600)}h ago`;
  return `${Math.round(s / 86400)}d ago`;
}

/** Alert type to display badge */
export function alertTypeBadge(type: string): { label: string; color: string } {
  switch (type) {
    case "LARGE_LATE_PRINT":
      return { label: "Late Print", color: "bg-red-500/20 text-red-400" };
    case "LIQUIDITY_SWEEP":
      return { label: "Sweep", color: "bg-yellow-500/20 text-yellow-400" };
    case "FAST_PRICE_IMPACT":
      return { label: "Impact", color: "bg-blue-500/20 text-blue-400" };
    case "SUSTAINED_IMBALANCE":
      return { label: "Imbalance", color: "bg-purple-500/20 text-purple-400" };
    default:
      return { label: type, color: "bg-gray-500/20 text-gray-400" };
  }
}
