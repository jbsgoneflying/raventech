"use client";

import { useState, useEffect } from "react";

const NAV_ITEMS = [
  { href: "/", label: "Home", desc: "Platform overview and engine directory" },
  { href: "/market-intelligence", label: "Market Intelligence", desc: "Pre-open roadmap and cross-asset stress" },
  { href: "/breach", label: "Engine 1", desc: "Earnings hold risk with Monte Carlo" },
  { href: "/spx", label: "Engine 2", desc: "SPX/SPY iron condor scanner" },
  { href: "/lead-lag", label: "Engine 5", desc: "Global lead-lag regime intelligence" },
  { href: "/red-dog", label: "Red Dog", desc: "Mean-reversion scanner (SP500 + NDX)" },
  { href: "/ichimoku", label: "Ichimoku", desc: "Trend-continuation scanner" },
  { href: "/calendar", label: "Earnings Calendar", desc: "Mega-cap earnings dates and compare workflow" },
  { href: "/compare", label: "Compare", desc: "Multi-ticker side-by-side" },
  { href: "/news-risk", label: "News Risk", desc: "Macro events and headline risk" },
  { href: "/flow-monitor", label: "Flow Monitor", desc: "Prediction market unusual activity detection" },
];

export function NavDrawer() {
  const [open, setOpen] = useState(false);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key === "Escape") setOpen(false);
    }
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const basePath = process.env.NEXT_PUBLIC_BASE_PATH ?? "";

  return (
    <>
      {/* Hamburger button */}
      <button
        className={`navHamburger${open ? " open" : ""}`}
        aria-label="Open navigation"
        onClick={() => setOpen((v) => !v)}
      >
        <span /><span /><span />
      </button>

      {/* Overlay */}
      <div
        className={`navOverlay${open ? " open" : ""}`}
        onClick={() => setOpen(false)}
      />

      {/* Drawer */}
      <nav className={`navDrawer${open ? " open" : ""}`} aria-label="Main navigation">
        <div className="navDrawerHead">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img className="navDrawerLogo" src={`${basePath}/RavenONLY.png`} alt="" />
          <div>
            <div className="navDrawerTitle">Raven-Tech.co</div>
            <div className="navDrawerSub">Quantitative Desk Intelligence</div>
          </div>
        </div>

        <div className="navDrawerLinks">
          {NAV_ITEMS.map((item) => {
            const isFlowMonitor = item.href === "/flow-monitor";
            const fullHref = isFlowMonitor
              ? `${basePath}/alerts`
              : `https://app.raven-tech.co${item.href}`;

            return (
              <a
                key={item.href}
                href={fullHref}
                className={`navDrawerLink${isFlowMonitor ? " navDrawerLink--active" : ""}`}
                onClick={() => setOpen(false)}
              >
                <span className="navDrawerLinkLabel">{item.label}</span>
                <span className="navDrawerLinkDesc">{item.desc}</span>
              </a>
            );
          })}
        </div>
      </nav>
    </>
  );
}
