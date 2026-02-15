"use client";

import { useEffect, useRef } from "react";

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

    // Dynamic import to avoid SSR issues
    import("lightweight-charts").then(({ createChart, LineStyle }) => {
      if (!containerRef.current) return;

      // Clear previous chart
      containerRef.current.innerHTML = "";

      const chart = createChart(containerRef.current, {
        width: containerRef.current.clientWidth,
        height: 250,
        layout: {
          background: { color: "#111118" },
          textColor: "#94a3b8",
          fontSize: 11,
        },
        grid: {
          vertLines: { color: "#1e1e28" },
          horzLines: { color: "#1e1e28" },
        },
        crosshair: {
          mode: 0,
        },
        rightPriceScale: {
          borderColor: "#2a2a36",
        },
        timeScale: {
          borderColor: "#2a2a36",
          timeVisible: true,
        },
      });

      const series = chart.addLineSeries({
        color: "#10b981",
        lineWidth: 2,
        priceFormat: {
          type: "custom",
          formatter: (price: number) => `${price.toFixed(0)}%`,
        },
      });

      // Convert trades to chart data (deduplicate by second)
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

      // Resize handler
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
    <div className="bg-surface-50 rounded-lg border border-surface-300 p-4">
      <h3 className="text-sm font-medium text-gray-400 mb-3">Price (Yes %)</h3>
      <div ref={containerRef} className="w-full" />
      {trades.length === 0 && (
        <div className="h-[250px] flex items-center justify-center text-gray-600 text-sm">
          No trade data available
        </div>
      )}
    </div>
  );
}
