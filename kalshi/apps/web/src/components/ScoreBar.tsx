"use client";

export function ScoreBar({ score }: { score: number }) {
  const color =
    score >= 80
      ? "bg-[var(--red)]"
      : score >= 60
        ? "bg-[var(--amber)]"
        : score >= 40
          ? "bg-[var(--amber)]"
          : "bg-[var(--green)]";

  const textColor =
    score >= 80
      ? "text-[var(--red)]"
      : score >= 60
        ? "text-[var(--amber)]"
        : score >= 40
          ? "text-[var(--amber)]"
          : "text-[var(--green)]";

  return (
    <div className="flex items-center gap-2">
      <span className={`font-mono font-bold text-sm ${textColor}`}>
        {score}
      </span>
      <div className="w-16 h-1.5 bg-raven-border rounded-full overflow-hidden">
        <div
          className={`h-full rounded-full transition-all duration-500 ${color}`}
          style={{ width: `${score}%` }}
        />
      </div>
    </div>
  );
}
