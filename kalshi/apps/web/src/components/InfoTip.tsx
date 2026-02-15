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
          w-[18px] h-[18px] rounded-full
          text-[10px] font-bold leading-none
          border transition-colors cursor-pointer
          ${open
            ? "bg-[var(--blue)] border-[var(--blue)] text-white shadow-sm"
            : "bg-white border-raven-borderStrong text-raven-muted hover:bg-raven-hover hover:border-raven-borderStrong"
          }
        `}
      >
        i
      </button>

      {open && (
        <div
          role="tooltip"
          className="
            absolute z-[1000] top-[calc(100%+10px)] left-1/2 -translate-x-1/2
            w-[280px] p-3
            bg-white border border-raven-border
            rounded-[14px] shadow-card
            before:content-[''] before:absolute before:top-[-6px] before:left-1/2 before:-translate-x-1/2
            before:w-3 before:h-3 before:rotate-45
            before:bg-white before:border-l before:border-t before:border-raven-border
          "
        >
          <div className="text-[12px] font-extrabold text-raven-text tracking-wide uppercase mb-1.5" style={{ letterSpacing: "0.1px" }}>
            {title}
          </div>
          <div className="text-[12px] font-medium text-raven-muted leading-[1.35] [&_p]:mt-2 [&_p:first-child]:mt-0 [&_ul]:mt-2 [&_ul]:pl-4 [&_ul]:list-disc [&_li]:mt-1.5 [&_b]:text-raven-text [&_b]:font-semibold">
            {children}
          </div>
        </div>
      )}
    </span>
  );
}
