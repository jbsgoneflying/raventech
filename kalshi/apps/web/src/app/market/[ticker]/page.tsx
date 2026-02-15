"use client";

import { useEffect, useState, useCallback } from "react";
import Link from "next/link";
import { useParams } from "next/navigation";
import { getMarketDetail, type MarketDetail, type AlertRow } from "@/lib/api";
import { useSSE } from "@/hooks/useSSE";
import { centsToProb, formatTtc, centsToDollars } from "@/lib/format";
import { PriceChart } from "@/components/PriceChart";
import { TradeTape } from "@/components/TradeTape";
import { OrderBookDepth } from "@/components/OrderBookDepth";
import { FeaturePanel } from "@/components/FeaturePanel";
import { AlertHistory } from "@/components/AlertHistory";
import { ExchangeBadge } from "@/components/ExchangeBadge";

export default function MarketPage() {
  const params = useParams();
  const ticker = params.ticker as string;
  const [data, setData] = useState<MarketDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!ticker) return;
    setLoading(true);
    getMarketDetail(ticker)
      .then((d) => {
        setData(d);
        setLoading(false);
      })
      .catch((err) => {
        setError(err.message);
        setLoading(false);
      });
  }, [ticker]);

  // SSE for real-time updates on this market
  const handleTrade = useCallback((tradeData: Record<string, unknown>) => {
    if (tradeData.market_ticker !== ticker) return;
    setData((prev) => {
      if (!prev) return prev;
      const trade = {
        trade_id: tradeData.trade_id as string,
        market_ticker: tradeData.market_ticker as string,
        yes_price_cents: tradeData.yes_price_cents as number,
        no_price_cents: (100 - (tradeData.yes_price_cents as number)),
        count: tradeData.count as number,
        taker_side: tradeData.taker_side as string,
        created_time: tradeData.created_time as string,
      };
      return {
        ...prev,
        trades: [trade, ...prev.trades].slice(0, 200),
      };
    });
  }, [ticker]);

  const handleAlert = useCallback((alertData: Record<string, unknown>) => {
    if (alertData.market_ticker !== ticker) return;
    setData((prev) => {
      if (!prev) return prev;
      return {
        ...prev,
        alerts: [alertData as unknown as AlertRow, ...prev.alerts].slice(0, 100),
      };
    });
  }, [ticker]);

  useSSE({
    url: `/api/alerts/stream?market_ticker=${ticker}`,
    onTrade: handleTrade,
    onAlert: handleAlert,
  });

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="text-gray-500">Loading market data...</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="text-red-400">{error ?? "Market not found"}</span>
      </div>
    );
  }

  const { market, trades, alerts, book } = data;
  const flaggedTradeIds = new Set(alerts.map((a) => a.trade_id).filter(Boolean) as string[]);

  return (
    <div>
      {/* Header */}
      <div className="mb-6">
        <div className="flex items-center gap-3 mb-1">
          <Link href="/alerts" className="text-gray-600 hover:text-gray-400 text-sm">&larr; Alerts</Link>
          <span className="text-gray-700">/</span>
          <span className="font-mono text-xs text-gray-500">{market.ticker}</span>
        </div>
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-semibold text-white">{market.title}</h2>
          <ExchangeBadge exchange={market.exchange} />
        </div>
        <div className="flex items-center gap-4 mt-2 text-sm text-gray-500">
          <div>
            <span className="text-gray-600">Last: </span>
            <span className="text-white font-mono">{centsToProb(market.last_price_cents)}</span>
          </div>
          <div>
            <span className="text-gray-600">Bid/Ask: </span>
            <span className="font-mono">
              {centsToProb(market.yes_bid_cents)} / {centsToProb(market.yes_ask_cents)}
            </span>
          </div>
          <div>
            <span className="text-gray-600">Vol: </span>
            <span className="font-mono">{market.volume?.toLocaleString() ?? "—"}</span>
          </div>
          <div>
            <span className="text-gray-600">OI: </span>
            <span className="font-mono">{market.open_interest?.toLocaleString() ?? "—"}</span>
          </div>
          <div>
            <span className="text-gray-600">Close: </span>
            <span className="font-mono">{formatTtc(market.close_time)}</span>
          </div>
        </div>
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-12 gap-4">
        {/* Left: Chart + Trade Tape */}
        <div className="col-span-8 space-y-4">
          <PriceChart trades={trades} />
          <TradeTape trades={trades} flaggedTradeIds={flaggedTradeIds} />
        </div>

        {/* Right: Order Book + Features + Alert History */}
        <div className="col-span-4 space-y-4">
          <OrderBookDepth book={book} />
          <FeaturePanel alerts={alerts} />
          <AlertHistory alerts={alerts} />
        </div>
      </div>
    </div>
  );
}
