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
import { InfoTip } from "@/components/InfoTip";

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
        <span className="text-raven-muted">Loading market data...</span>
      </div>
    );
  }

  if (error || !data) {
    return (
      <div className="flex items-center justify-center h-64">
        <span className="text-[var(--red)]">{error ?? "Market not found"}</span>
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
          <Link href="/alerts" className="text-raven-muted2 hover:text-raven-text text-sm font-medium">&larr; Alerts</Link>
          <span className="text-raven-border">/</span>
          <span className="font-mono text-xs text-raven-muted2">{market.ticker}</span>
        </div>
        <div className="flex items-center gap-2">
          <h2 className="text-xl font-bold text-raven-text tracking-tight">{market.title}</h2>
          <ExchangeBadge exchange={market.exchange} />
        </div>
        <div className="flex items-center gap-4 mt-2 text-[13px] text-raven-muted">
          <div className="flex items-center">
            <span className="text-raven-muted2">Last: </span>
            <span className="text-raven-text font-mono font-semibold ml-1">{centsToProb(market.last_price_cents)}</span>
            <InfoTip title="Last Trade Price">
              <p>Most recent traded price for &ldquo;Yes,&rdquo; expressed as a probability.</p>
            </InfoTip>
          </div>
          <div className="flex items-center">
            <span className="text-raven-muted2">Bid/Ask: </span>
            <span className="font-mono ml-1">
              {centsToProb(market.yes_bid_cents)} / {centsToProb(market.yes_ask_cents)}
            </span>
            <InfoTip title="Bid / Ask Spread">
              <p>Best bid and ask for &ldquo;Yes.&rdquo; Tight = liquid. Wide = careful sizing.</p>
            </InfoTip>
          </div>
          <div className="flex items-center">
            <span className="text-raven-muted2">Vol: </span>
            <span className="font-mono ml-1">{market.volume?.toLocaleString() ?? "—"}</span>
            <InfoTip title="Volume">
              <p>Total contracts traded. Low volume = outsized flow is more impactful.</p>
            </InfoTip>
          </div>
          <div className="flex items-center">
            <span className="text-raven-muted2">OI: </span>
            <span className="font-mono ml-1">{market.open_interest?.toLocaleString() ?? "—"}</span>
            <InfoTip title="Open Interest">
              <p>Outstanding contracts. Rising OI + price move = new money confirming.</p>
            </InfoTip>
          </div>
          <div className="flex items-center">
            <span className="text-raven-muted2">Close: </span>
            <span className="font-mono ml-1">{formatTtc(market.close_time)}</span>
            <InfoTip title="Market Close">
              <p>Time until expiry and settlement. Near-close trades are highest signal.</p>
            </InfoTip>
          </div>
        </div>
      </div>

      {/* Main grid */}
      <div className="grid grid-cols-12 gap-4">
        <div className="col-span-8 space-y-4">
          <PriceChart trades={trades} />
          <TradeTape trades={trades} flaggedTradeIds={flaggedTradeIds} />
        </div>
        <div className="col-span-4 space-y-4">
          <OrderBookDepth book={book} />
          <FeaturePanel alerts={alerts} />
          <AlertHistory alerts={alerts} />
        </div>
      </div>
    </div>
  );
}
