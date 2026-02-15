import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";
import { NavDrawer } from "@/components/NavDrawer";

export const metadata: Metadata = {
  title: "Raven-Tech.co · Flow Monitor",
  description: "Prediction market unusual activity detection — Kalshi & Polymarket",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en">
      <head>
        <meta name="theme-color" content="#f5f5f7" />
        <link rel="icon" type="image/png" href="/RavenONLY.png" />
        <link rel="apple-touch-icon" href="/RavenONLY.png" />
      </head>
      <body>
        <main className="max-w-[1240px] mx-auto px-5 pt-[22px] pb-16">
          {/* ── Raven Tech App Header ── */}
          <header className="flex items-center justify-between gap-4 mb-3.5 relative z-[16000]">
            <div className="inline-flex items-center gap-3 min-w-0">
              {/* eslint-disable-next-line @next/next/no-img-element */}
              <img
                className="w-[52px] h-[52px] object-contain rounded-[14px] bg-white border border-raven-border shadow-card"
                src={`${process.env.NEXT_PUBLIC_BASE_PATH ?? ""}/RavenONLY.png`}
                alt="Raven-Tech.co"
              />
              <div className="min-w-0 flex flex-col">
                <div className="text-2xl font-extrabold tracking-tight text-raven-text" style={{ letterSpacing: "-0.3px" }}>
                  Raven-Tech.co
                </div>
                <div className="mt-1.5 text-[13px] font-semibold text-raven-muted">
                  Flow Monitor · Prediction market unusual activity detection (Kalshi + Polymarket)
                </div>
              </div>
            </div>
            <NavDrawer />
          </header>

          {/* ── Sub-navigation (Alerts / Markets) ── */}
          <SubNav />

          {/* ── Page content ── */}
          {children}
        </main>
      </body>
    </html>
  );
}

function SubNav() {
  return (
    <nav className="surface mb-4 flex items-center gap-2 !p-2">
      <SubNavLink href="/alerts" label="Alerts" />
      <SubNavLink href="/markets" label="Markets" />
      <div className="ml-auto flex items-center gap-1.5 pr-2">
        <div className="w-2 h-2 bg-accent-green rounded-full animate-pulse" />
        <span className="text-xs text-raven-muted font-medium">Live</span>
        <span className="text-xs text-raven-muted2 font-mono ml-2">v0.1.0</span>
      </div>
    </nav>
  );
}

function SubNavLink({ href, label }: { href: string; label: string }) {
  return (
    <Link
      href={href}
      className="px-3.5 py-1.5 text-[13px] font-semibold text-raven-muted hover:text-raven-text hover:bg-raven-hover rounded-[10px] transition-colors"
    >
      {label}
    </Link>
  );
}
