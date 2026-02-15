/**
 * Exchange badge and filter components for the light Raven Tech theme.
 */

import { InfoTip } from "./InfoTip";

interface ExchangeBadgeProps {
  exchange: string;
  className?: string;
}

const EXCHANGE_STYLES: Record<string, { label: string; abbr: string; color: string }> = {
  kalshi: {
    label: "Kalshi",
    abbr: "K",
    color: "bg-emerald-100 text-emerald-700 border border-emerald-200",
  },
  polymarket: {
    label: "Polymarket",
    abbr: "P",
    color: "bg-blue-100 text-blue-700 border border-blue-200",
  },
};

export function ExchangeBadge({ exchange, className = "" }: ExchangeBadgeProps) {
  const style = EXCHANGE_STYLES[exchange] ?? {
    label: exchange,
    abbr: exchange.charAt(0).toUpperCase(),
    color: "bg-gray-100 text-gray-600 border border-gray-200",
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

interface ExchangeFilterProps {
  value: string;
  onChange: (value: string) => void;
}

export function ExchangeFilter({ value, onChange }: ExchangeFilterProps) {
  return (
    <div className="flex items-center gap-2">
      <label className="text-[11px] text-raven-muted uppercase tracking-wider font-semibold">
        Exchange
        <InfoTip title="Exchange Filter">
          <p>Filter by prediction market exchange.</p>
          <ul>
            <li><b>Kalshi</b>: U.S. regulated event contracts.</li>
            <li><b>Polymarket</b>: Crypto-settled, larger political markets.</li>
          </ul>
        </InfoTip>
      </label>
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="bg-white border border-raven-border text-[13px] rounded-[10px] px-2.5 py-1 text-raven-text"
      >
        <option value="">All</option>
        <option value="kalshi">Kalshi</option>
        <option value="polymarket">Polymarket</option>
      </select>
    </div>
  );
}
