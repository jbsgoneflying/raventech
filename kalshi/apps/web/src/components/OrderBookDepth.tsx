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
      <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
        <h3 className="text-sm font-medium text-gray-400 mb-3">
          Order Book
          <InfoTip title="Order Book Depth">
            <p>Stacked visualization of resting limit orders. Shows bid/ask depth to help you gauge market liquidity and potential price impact.</p>
            <p><b>Desk view</b>: No data means the exchange doesn&apos;t expose orderbook depth for this market, or the API key is not configured. You can still use the trade tape and alerts to track flow.</p>
          </InfoTip>
        </h3>
        <div className="py-8 text-center text-gray-600 text-sm">
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

  // Derive yes asks from no bids: yes_ask at price (100-no_bid_price) with no_bid_qty
  const yesAsks = book.no_bids
    .map(([p, q]): [number, number] => [100 - p, q])
    .sort((a, b) => a[0] - b[0]) // ascending price
    .slice(0, 10);

  const yesBids = book.yes_bids.slice(0, 10);

  return (
    <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">
        Order Book
        {book.mid !== null && (
          <span className="ml-2 text-xs text-gray-600">
            Mid: {book.mid.toFixed(1)}%
          </span>
        )}
        <InfoTip title="Order Book Depth">
          <p>Stacked visualization of resting limit orders. <b>Green bars</b> (bottom) = bids to buy Yes. <b>Red bars</b> (top) = offers to sell Yes (derived from No bids).</p>
          <ul>
            <li><b>Thick bars</b>: Deep liquidity at that price level — harder to move through.</li>
            <li><b>Thin bars</b>: Shallow depth — a single large trade could blow through this level.</li>
            <li><b>Spread</b>: The gap between best bid and best ask. Wider spread = less liquid, more opportunity cost.</li>
          </ul>
          <p><b>Desk view</b>: Before trading, check the book. If depth is thin on the side you&apos;re crossing, size down or use limits. A &ldquo;sweep&rdquo; alert means someone already ate through these levels aggressively.</p>
        </InfoTip>
      </h3>

      <div className="space-y-0.5">
        {/* Asks (top, reversed so lowest ask at bottom near mid) */}
        {[...yesAsks].reverse().map(([price, qty], i) => (
          <DepthRow
            key={`ask-${i}`}
            price={price}
            qty={qty}
            maxQty={maxQty}
            side="ask"
          />
        ))}

        {/* Spread indicator */}
        <div className="flex items-center justify-center py-1">
          <div className="h-px bg-surface-300 flex-1" />
          <span className="px-2 text-[10px] text-gray-600 uppercase">
            spread {book.best_yes_ask !== null && book.best_yes_bid !== null
              ? `${(book.best_yes_ask - book.best_yes_bid).toFixed(0)}%`
              : "—"}
          </span>
          <div className="h-px bg-surface-300 flex-1" />
        </div>

        {/* Bids */}
        {yesBids.map(([price, qty], i) => (
          <DepthRow
            key={`bid-${i}`}
            price={price}
            qty={qty}
            maxQty={maxQty}
            side="bid"
          />
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
  const color = side === "bid" ? "bg-green-500/20" : "bg-red-500/20";
  const textColor = side === "bid" ? "text-green-400" : "text-red-400";

  return (
    <div className="relative flex items-center justify-between px-2 py-0.5 text-xs font-mono rounded">
      <div
        className={`absolute inset-0 ${color} rounded`}
        style={{
          width: `${pct}%`,
          [side === "bid" ? "left" : "right"]: 0,
        }}
      />
      <span className={`relative ${textColor}`}>{price}%</span>
      <span className="relative text-gray-400">{qty.toFixed(0)}</span>
    </div>
  );
}
