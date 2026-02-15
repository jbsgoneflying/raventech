"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { getMarkets, type MarketRow } from "@/lib/api";
import { centsToProb, formatTtc } from "@/lib/format";
import { ExchangeBadge, ExchangeFilter } from "@/components/ExchangeBadge";

export default function MarketsPage() {
  const [markets, setMarkets] = useState<MarketRow[]>([]);
  const [search, setSearch] = useState("");
  const [exchange, setExchange] = useState("");
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    const timeout = setTimeout(() => {
      getMarkets({ search: search || undefined, exchange: exchange || undefined, limit: 200 })
        .then((data) => {
          setMarkets(data.markets);
          setLoading(false);
        })
        .catch(() => setLoading(false));
    }, 300);

    return () => clearTimeout(timeout);
  }, [search, exchange]);

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-xl font-bold text-raven-text tracking-tight">Markets</h2>
        <p className="text-[13px] text-raven-muted font-medium mt-1">
          All active markets being monitored across exchanges.
        </p>
      </div>

      <div className="surface flex items-center gap-4 mb-4 !p-3">
        <input
          type="text"
          placeholder="Search markets..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 max-w-md bg-white border border-raven-border rounded-[10px] px-3 py-1.5 text-[13px] text-raven-text placeholder-raven-muted2 focus:outline-none focus:ring-2 focus:ring-[var(--focus-ring)]"
        />
        <ExchangeFilter value={exchange} onChange={setExchange} />
      </div>

      <div className="surface !p-0 overflow-hidden">
        <div className="overflow-x-auto">
          <table className="w-full text-[13px]">
            <thead>
              <tr className="text-left text-[11px] text-raven-muted uppercase tracking-wider border-b border-raven-border font-semibold">
                <th className="py-2.5 px-3 w-8">Exch</th>
                <th className="py-2.5 px-3">Market</th>
                <th className="py-2.5 px-3">Last Price</th>
                <th className="py-2.5 px-3">Bid/Ask</th>
                <th className="py-2.5 px-3">Volume</th>
                <th className="py-2.5 px-3">Open Interest</th>
                <th className="py-2.5 px-3">T-Close</th>
              </tr>
            </thead>
            <tbody>
              {loading ? (
                <tr><td colSpan={7} className="py-8 text-center text-raven-muted">Loading...</td></tr>
              ) : markets.length === 0 ? (
                <tr><td colSpan={7} className="py-8 text-center text-raven-muted">No markets found</td></tr>
              ) : (
                markets.map((m) => (
                  <tr key={m.ticker} className="border-b border-raven-line hover:bg-raven-hover transition-colors">
                    <td className="py-2 px-3">
                      <ExchangeBadge exchange={m.exchange} />
                    </td>
                    <td className="py-2 px-3">
                      <Link
                        href={`/market/${m.ticker}`}
                        className="text-[var(--blue)] hover:underline font-medium"
                      >
                        {m.title}
                      </Link>
                      <div className="text-[10px] text-raven-muted2 font-mono">{m.ticker}</div>
                    </td>
                    <td className="py-2 px-3 font-mono text-xs">{centsToProb(m.last_price_cents)}</td>
                    <td className="py-2 px-3 font-mono text-xs">
                      {centsToProb(m.yes_bid_cents)} / {centsToProb(m.yes_ask_cents)}
                    </td>
                    <td className="py-2 px-3 font-mono text-xs">{m.volume?.toLocaleString() ?? "—"}</td>
                    <td className="py-2 px-3 font-mono text-xs">{m.open_interest?.toLocaleString() ?? "—"}</td>
                    <td className="py-2 px-3 font-mono text-xs text-raven-muted">{formatTtc(m.close_time)}</td>
                  </tr>
                ))
              )}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}
