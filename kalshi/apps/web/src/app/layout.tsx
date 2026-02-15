import type { Metadata } from "next";
import Link from "next/link";
import "./globals.css";

export const metadata: Metadata = {
  title: "Kalshi Flow Monitor",
  description: "Real-time unusual activity detection for prediction markets",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className="dark">
      <head>
        <link
          href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700&family=JetBrains+Mono:wght@400;500&display=swap"
          rel="stylesheet"
        />
      </head>
      <body className="bg-surface min-h-screen">
        <nav className="border-b border-surface-300 bg-surface-50">
          <div className="max-w-[1600px] mx-auto px-4 py-3 flex items-center justify-between">
            <div className="flex items-center gap-6">
              <h1 className="text-lg font-semibold text-white tracking-tight">
                <span className="text-accent-green">K</span>alshi Flow Monitor
              </h1>
              <div className="flex gap-1">
                <NavLink href="/alerts">Alerts</NavLink>
                <NavLink href="/markets">Markets</NavLink>
              </div>
            </div>
            <div className="flex items-center gap-3">
              <StatusDot />
              <span className="text-xs text-gray-500 font-mono">v0.1.0</span>
            </div>
          </div>
        </nav>
        <main className="max-w-[1600px] mx-auto px-4 py-4">
          {children}
        </main>
      </body>
    </html>
  );
}

function NavLink({ href, children }: { href: string; children: React.ReactNode }) {
  return (
    <Link
      href={href}
      className="px-3 py-1.5 text-sm text-gray-400 hover:text-white hover:bg-surface-200 rounded transition-colors"
    >
      {children}
    </Link>
  );
}

function StatusDot() {
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-2 h-2 bg-accent-green rounded-full animate-pulse" />
      <span className="text-xs text-gray-500">Live</span>
    </div>
  );
}
