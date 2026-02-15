"use client";

export function ScoreBar({ score }: { score: number }) {
  const color =
    score >= 80
      ? "bg-red-500"
      : score >= 60
        ? "bg-orange-500"
        : score >= 40
          ? "bg-yellow-500"
          : "bg-green-500";

  const textColor =
    score >= 80
      ? "text-red-400"
      : score >= 60
        ? "text-orange-400"
        : score >= 40
          ? "text-yellow-400"
          : "text-green-400";

  return (
    <div className="flex items-center gap-2">
      <span className={`font-mono font-bold text-sm ${textColor}`}>
        {score}
      </span>
      <div className="w-16 h-1.5 bg-surface-300 rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  );
}
