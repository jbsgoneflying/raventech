/**
 * Exchange badge component: displays a colored pill indicating
 * which prediction market exchange a row comes from.
 */

interface ExchangeBadgeProps {
  exchange: string;
  className?: string;
}

const EXCHANGE_STYLES: Record<string, { label: string; abbr: string; color: string }> = {
  kalshi: {
    label: "Kalshi",
    abbr: "K",
    color: "bg-emerald-900/60 text-emerald-400 border border-emerald-700/50",
  },
  polymarket: {
    label: "Polymarket",
    abbr: "P",
    color: "bg-blue-900/60 text-blue-400 border border-blue-700/50",
  },
};

export function ExchangeBadge({ exchange, className = "" }: ExchangeBadgeProps) {
  const style = EXCHANGE_STYLES[exchange] ?? {
    label: exchange,
    abbr: exchange.charAt(0).toUpperCase(),
    color: "bg-gray-800 text-gray-400 border border-gray-700",
  };

  return (
    <span
      className={`inline-flex items-center justify-center px-1.5 py-0.5 text-[10px] font-semibold rounded ${style.color} ${className}`}
      title={style.label}
    >
      {style.abbr}
    </span>
  );
}

/**
 * Dropdown for filtering by exchange.
 */
interface ExchangeFilterProps {
  value: string;
  onChange: (value: string) => void;
}

export function ExchangeFilter({ value, onChange }: ExchangeFilterProps) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-xs text-gray-500 uppercase tracking-wider">Exchange</label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-surface-200 border border-surface-300 text-sm rounded px-2 py-1 text-gray-300"
      >
        <option value="">All</option>
        <option value="kalshi">Kalshi</option>
        <option value="polymarket">Polymarket</option>
      </select>
    </div>
  );
}
