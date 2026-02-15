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
    }, 300); // Debounce search

    return () => clearTimeout(timeout);
  }, [search, exchange]);

  return (
    <div>
      <div className="mb-4">
        <h2 className="text-xl font-semibold text-white">Markets</h2>
        <p className="text-sm text-gray-500 mt-1">
          All active markets being monitored across exchanges.
        </p>
      </div>

      <div className="flex items-center gap-4 mb-4">
        <input
          type="text"
          placeholder="Search markets..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          className="flex-1 max-w-md bg-surface-100 border border-surface-300 rounded-lg px-3 py-2 text-sm text-gray-300 placeholder-gray-600 focus:outline-none focus:border-accent-green/50"
        />
        <ExchangeFilter value={exchange} onChange={setExchange} />
      </div>

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-left text-xs text-gray-500 uppercase tracking-wider border-b border-surface-300">
              <th className="pb-2 pr-2 w-8">Exch</th>
              <th className="pb-2 pr-3">Market</th>
              <th className="pb-2 pr-3">Last Price</th>
              <th className="pb-2 pr-3">Bid/Ask</th>
              <th className="pb-2 pr-3">Volume</th>
              <th className="pb-2 pr-3">Open Interest</th>
              <th className="pb-2">T-Close</th>
            </tr>
          </thead>
          <tbody>
            {loading ? (
              <tr><td colSpan={7} className="py-8 text-center text-gray-500">Loading...</td></tr>
            ) : markets.length === 0 ? (
              <tr><td colSpan={7} className="py-8 text-center text-gray-500">No markets found</td></tr>
            ) : (
              markets.map((m) => (
                <tr key={m.ticker} className="border-b border-surface-200/50 hover:bg-surface-100/50">
                  <td className="py-2 pr-2">
                    <ExchangeBadge exchange={m.exchange} />
                  </td>
                  <td className="py-2 pr-3">
                    <Link
                      href={`/market/${m.ticker}`}
                      className="text-blue-400 hover:text-blue-300 hover:underline"
                    >
                      {m.title}
                    </Link>
                    <div className="text-[10px] text-gray-600 font-mono">{m.ticker}</div>
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs">{centsToProb(m.last_price_cents)}</td>
                  <td className="py-2 pr-3 font-mono text-xs">
                    {centsToProb(m.yes_bid_cents)} / {centsToProb(m.yes_ask_cents)}
                  </td>
                  <td className="py-2 pr-3 font-mono text-xs">{m.volume?.toLocaleString() ?? "—"}</td>
                  <td className="py-2 pr-3 font-mono text-xs">{m.open_interest?.toLocaleString() ?? "—"}</td>
                  <td className="py-2 font-mono text-xs text-gray-400">{formatTtc(m.close_time)}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
