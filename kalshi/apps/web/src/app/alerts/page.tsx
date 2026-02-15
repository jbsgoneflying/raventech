import { AlertsTable } from "@/components/AlertsTable";

export default function AlertsPage() {
  return (
    <div>
      <div className="mb-4">
        <h2 className="text-xl font-bold text-raven-text tracking-tight">Live Alerts</h2>
        <p className="text-[13px] text-raven-muted font-medium mt-1">
          Unusual activity detected across all Kalshi &amp; Polymarket markets, ranked by anomaly score.
        </p>
      </div>
      <AlertsTable />
    </div>
  );
}
