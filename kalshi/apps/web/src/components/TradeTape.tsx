"use client";

import { timeAgo } from "@/lib/format";
import { InfoTip } from "./InfoTip";

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
    <div className="surface">
      <h3 className="text-[13px] font-semibold text-raven-muted mb-3">
        Recent Trades
        <span className="text-xs text-raven-muted2 ml-2">({trades.length})</span>
        <InfoTip title="Trade Tape">
          <p>Live stream of individual trades. <b>FLAGGED</b> = this trade triggered an anomaly alert. Watch for follow-through after flagged prints.</p>
        </InfoTip>
      </h3>

      <div className="max-h-[400px] overflow-y-auto space-y-0.5">
        {trades.length === 0 ? (
          <div className="py-8 text-center text-raven-muted2 text-sm">No trades</div>
        ) : (
          trades.map((trade) => {
            const isFlagged = flaggedTradeIds.has(trade.trade_id);
            const isYes = trade.taker_side === "yes";

            return (
              <div
                key={trade.trade_id}
                className={`flex items-center justify-between px-2 py-1 rounded text-xs font-mono ${
                  isFlagged
                    ? "trade-flagged"
                    : "hover:bg-raven-hover"
                }`}
              >
                <div className="flex items-center gap-3">
                  <span className="text-raven-muted2 w-16">{timeAgo(trade.created_time)}</span>
                  <span className={isYes ? "text-[var(--green)] font-semibold" : "text-[var(--red)] font-semibold"}>
                    {isYes ? "BUY" : "SELL"}
                  </span>
                  <span className="text-raven-text">{trade.count} @ {trade.yes_price_cents}%</span>
                </div>
                {isFlagged && (
                  <span className="text-[var(--red)] text-[10px] uppercase tracking-wider font-bold">flagged</span>
                )}
              </div>
            );
          })
        )}
      </div>
    </div>
  );
}
