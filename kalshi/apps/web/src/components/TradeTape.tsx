"use client";

import { timeAgo } from "@/lib/format";

interface Trade {
  trade_id: string;
  yes_price_cents: number;
  no_price_cents: number;
  count: number;
  taker_side: string;
  created_time: string;
}

interface Props {
  trades: Trade[];
  flaggedTradeIds?: Set<string>;
}

export function TradeTape({ trades, flaggedTradeIds = new Set() }: Props) {
  return (
    <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">
        Recent Trades
        <span className="text-xs text-gray-600 ml-2">({trades.length})</span>
      </h3>

      <div className="max-h-[400px] overflow-y-auto space-y-0.5">
        {trades.length === 0 ? (
          <div className="py-8 text-center text-gray-600 text-sm">No trades</div>
        ) : (
          trades.map((trade) => {
            const isFlagged = flaggedTradeIds.has(trade.trade_id);
            const isYes = trade.taker_side === "yes";

            return (
              <div
                key={trade.trade_id}
                className={`flex items-center justify-between px-2 py-1 rounded text-xs font-mono ${
                  isFlagged
                    ? "trade-flagged bg-red-500/5"
                    : "hover:bg-surface-200/50"
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="text-gray-600 w-16">{timeAgo(trade.created_time)}</span>
                  <span className={isYes ? "text-green-400" : "text-red-400"}>
                    {isYes ? "BUY" : "SELL"}
                  </span>
                  <span className="text-gray-300">{trade.count} @ {trade.yes_price_cents}%</span>
                </div>
                {isFlagged && (
                  <span className="text-red-400 text-[10px] uppercase tracking-wider">flagged</span>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
