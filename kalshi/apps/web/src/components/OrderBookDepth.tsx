"use client";

import { InfoTip } from "./InfoTip";

interface Props {
  book: {
    mid: number | null;
    best_yes_bid: number | null;
    best_yes_ask: number | null;
    yes_bids: [number, number][];
    no_bids: [number, number][];
  } | null;
}

export function OrderBookDepth({ book }: Props) {
  if (!book) {
    return (
      <div className="surface">
        <h3 className="text-[13px] font-semibold text-raven-muted mb-3">
          Order Book
          <InfoTip title="Order Book Depth">
            <p>Shows bid/ask depth to gauge liquidity and potential price impact.</p>
          </InfoTip>
        </h3>
        <div className="py-8 text-center text-raven-muted2 text-sm">
          No orderbook data (requires API key)
        </div>
      </div>
    );
  }

  const maxQty = Math.max(
    ...book.yes_bids.map(([, q]) => q),
    ...book.no_bids.map(([, q]) => q),
    1
  );

  const yesAsks = book.no_bids
    .map(([p, q]): [number, number] => [100 - p, q])
    .sort((a, b) => a[0] - b[0])
    .slice(0, 10);

  const yesBids = book.yes_bids.slice(0, 10);

  return (
    <div className="surface">
      <h3 className="text-[13px] font-semibold text-raven-muted mb-3">
        Order Book
        {book.mid !== null && (
          <span className="ml-2 text-xs text-raven-muted2">
            Mid: {book.mid.toFixed(1)}%
          </span>
        )}
        <InfoTip title="Order Book Depth">
          <p><b>Green</b> = bids to buy Yes. <b>Red</b> = offers to sell Yes. Wider bars = deeper liquidity at that level.</p>
        </InfoTip>
      </h3>

      <div className="space-y-0.5">
        {[...yesAsks].reverse().map(([price, qty], i) => (
          <DepthRow key={`ask-${i}`} price={price} qty={qty} maxQty={maxQty} side="ask" />
        ))}

        <div className="flex items-center justify-center py-1">
          <div className="h-px bg-raven-border flex-1" />
          <span className="px-2 text-[10px] text-raven-muted2 uppercase font-semibold">
            spread {book.best_yes_ask !== null && book.best_yes_bid !== null
              ? `${(book.best_yes_ask - book.best_yes_bid).toFixed(0)}%`
              : "—"}
          </span>
          <div className="h-px bg-raven-border flex-1" />
        </div>

        {yesBids.map(([price, qty], i) => (
          <DepthRow key={`bid-${i}`} price={price} qty={qty} maxQty={maxQty} side="bid" />
        ))}
      </div>
    </div>
  );
}

function DepthRow({
  price,
  qty,
  maxQty,
  side,
}: {
  price: number;
  qty: number;
  maxQty: number;
  side: "bid" | "ask";
}) {
  const pct = (qty / maxQty) * 100;
  const color = side === "bid" ? "bg-emerald-500/15" : "bg-red-500/15";
  const textColor = side === "bid" ? "text-emerald-700" : "text-red-600";

  return (
    <div className="relative flex items-center justify-between px-2 py-0.5 text-xs font-mono rounded">
      <div
        className={`absolute inset-0 ${color} rounded`}
        style={{
          width: `${pct}%`,
          [side === "bid" ? "left" : "right"]: 0,
        }}
      />
      <span className={`relative font-semibold ${textColor}`}>{price}%</span>
      <span className="relative text-raven-muted">{qty.toFixed(0)}</span>
    </div>
  );
}
