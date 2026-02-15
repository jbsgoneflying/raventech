"use client";

import { useState, useRef, useEffect, type ReactNode } from "react";

interface InfoTipProps {
  title: string;
  children: ReactNode;
}

/**
 * Tooltip info button matching the Raven Tech "i" button pattern.
 * Click to open/close. Closes on outside click or Escape.
 */
export function InfoTip({ title, children }: InfoTipProps) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);

  useEffect(() => {
    if (!open) return;

    function handleClick(e: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) {
        setOpen(false);
      }
    }
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }

    document.addEventListener("mousedown", handleClick);
    document.addEventListener("keydown", handleKey);
    return () => {
      document.removeEventListener("mousedown", handleClick);
      document.removeEventListener("keydown", handleKey);
    };
  }, [open]);

  return (
    <span ref={wrapRef} className="relative inline-flex items-center ml-1">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        aria-label={`${title} help`}
        aria-expanded={open}
        className={`
          inline-flex items-center justify-center
          w-[15px] h-[15px] rounded-full
          text-[9px] font-bold leading-none
          border transition-colors cursor-pointer
          ${open
            ? "bg-blue-500/20 border-blue-500/50 text-blue-400"
            : "bg-surface-200/60 border-surface-300/80 text-gray-500 hover:bg-surface-200 hover:border-gray-500 hover:text-gray-400"
          }
        `}
      >
        i
      </button>

      {open && (
        <div
          role="tooltip"
          className="
            absolute z-[1000] top-[calc(100%+8px)] left-1/2 -translate-x-1/2
            w-[280px] p-3
            bg-surface-50 border border-surface-300
            rounded-lg shadow-xl shadow-black/40
            before:content-[''] before:absolute before:top-[-6px] before:left-1/2 before:-translate-x-1/2
            before:w-3 before:h-3 before:rotate-45
            before:bg-surface-50 before:border-l before:border-t before:border-surface-300
          "
        >
          <div className="text-[11px] font-bold text-white tracking-wide uppercase mb-1.5">
            {title}
          </div>
          <div className="text-[11px] font-medium text-gray-400 leading-[1.45] [&_p]:mt-1.5 [&_p:first-child]:mt-0 [&_ul]:mt-1.5 [&_ul]:pl-4 [&_ul]:list-disc [&_li]:mt-1 [&_b]:text-gray-300 [&_b]:font-semibold">
            {children}
          </div>
        </div>
      )}
    </span>
  );
}
