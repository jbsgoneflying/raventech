import { AlertsTable } from "@/components/AlertsTable";

export default function AlertsPage() {
  return (
    <div>
      <div className="mb-4">
        <h2 className="text-xl font-semibold text-white">Live Alerts</h2>
        <p className="text-sm text-gray-500 mt-1">
          Unusual activity detected across all Kalshi markets, ranked by anomaly score.
        </p>
      </div>
      <AlertsTable />
    </div>
  );
}
