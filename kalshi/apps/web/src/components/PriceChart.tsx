"use client";

import { useEffect, useRef } from "react";
import { InfoTip } from "./InfoTip";

interface PricePoint {
  time: number;
  value: number;
}

export function PriceChart({ trades }: { trades: Array<{ created_time: string; yes_price_cents: number }> }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<unknown>(null);

  useEffect(() => {
    if (!containerRef.current || trades.length === 0) return;

    let cleanup = () => {};

    import("lightweight-charts").then(({ createChart, LineStyle }) => {
      if (!containerRef.current) return;

      containerRef.current.innerHTML = "";

      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 250,
        layout: {
          background: { color: "transparent" },
          textColor: "rgba(11, 11, 15, 0.48)",
          fontSize: 11,
          fontFamily: "-apple-system, BlinkMacSystemFont, sans-serif",
        },
        grid: {
          vertLines: { color: "rgba(15, 23, 42, 0.06)" },
          horzLines: { color: "rgba(15, 23, 42, 0.06)" },
        },
        crosshair: { mode: 0 },
        rightPriceScale: { borderColor: "rgba(15, 23, 42, 0.10)" },
        timeScale: { borderColor: "rgba(15, 23, 42, 0.10)", timeVisible: true },
      });

      const series = chart.addLineSeries({
        color: "rgba(52, 199, 89, 0.95)",
        lineWidth: 2,
        priceFormat: {
          type: "custom",
          formatter: (price: number) => `${price.toFixed(0)}%`,
        },
      });

      const pointMap = new Map<number, number>();
      for (const t of trades) {
        const ts = Math.floor(new Date(t.created_time).getTime() / 1000);
        pointMap.set(ts, t.yes_price_cents);
      }

      const data = Array.from(pointMap.entries())
        .sort((a, b) => a[0] - b[0])
        .map(([time, value]) => ({ time: time as any, value }));

      if (data.length > 0) {
        series.setData(data);
        chart.timeScale().fitContent();
      }

      chartRef.current = chart;

      const onResize = () => {
        if (containerRef.current) {
          chart.applyOptions({ width: containerRef.current.clientWidth });
        }
      };
      window.addEventListener("resize", onResize);

      cleanup = () => {
        window.removeEventListener("resize", onResize);
        chart.remove();
      };
    });

    return () => cleanup();
  }, [trades]);

  return (
    <div className="surface">
      <h3 className="text-[13px] font-semibold text-raven-muted mb-3">
        Price (Yes %)
        <InfoTip title="Price Chart">
          <p>Real-time &ldquo;Yes&rdquo; price over time. Sharp moves correspond to aggressive flow. Look for the spike matching a flagged trade.</p>
        </InfoTip>
      </h3>
      <div ref={containerRef} className="w-full" />
      {trades.length === 0 && (
        <div className="h-[250px] flex items-center justify-center text-raven-muted2 text-sm">
          No trade data available
        </div>
      )}
    </div>
  );
}
