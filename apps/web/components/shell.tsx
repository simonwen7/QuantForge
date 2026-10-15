"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { motion, useReducedMotion } from "motion/react";
import type { ReactNode } from "react";

import { ExperimentProvider, useExperiment } from "@/lib/experiment";

const LINKS = [
  { href: "/", label: "Market" },
  { href: "/lab", label: "Lab" },
  { href: "/performance", label: "Performance" },
  { href: "/replay", label: "Replay" },
  { href: "/compare", label: "Compare" },
  { href: "/system", label: "System" },
];

function current(pathname: string, href: string): boolean {
  return href === "/" ? pathname === "/" : pathname.startsWith(href);
}

function Frame({ children }: { children: ReactNode }) {
  const pathname = usePathname();
  const reduce = useReducedMotion();
  const { status } = useExperiment();
  const page = LINKS.find((item) => current(pathname, item.href))?.label ?? "Market";

  return (
    <div className="app">
      <aside className="rail">
        <div className="brand">
          <strong>QUANTFORGE</strong>
          <span>RESEARCH TERMINAL</span>
        </div>
        <nav className="nav" aria-label="Workspaces">
          {LINKS.map((item) => (
            <Link key={item.href} href={item.href} aria-current={current(pathname, item.href) ? "page" : undefined}>
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="rail-status">
          <div className="badge badge-synthetic">SYNTHETIC</div>
          <p className="note">API {status === "error" ? "unavailable" : status === "loading" ? "running" : "connected"}</p>
        </div>
      </aside>
      <div>
        <nav className="mobile-nav" aria-label="Workspaces">
          {LINKS.map((item) => (
            <Link key={item.href} href={item.href} aria-current={current(pathname, item.href) ? "page" : undefined}>
              {item.label}
            </Link>
          ))}
        </nav>
        <main className="workspace">
          <header className="topbar">
            <div>
              <p className="eyebrow">QuantForge</p>
              <h1>{page}</h1>
            </div>
            <div className="badge badge-synthetic">SYNTHETIC</div>
          </header>
          <motion.div
            key={pathname}
            initial={reduce ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduce ? 0 : 0.18 }}
          >
            {children}
          </motion.div>
        </main>
      </div>
    </div>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  return (
    <ExperimentProvider>
      <Frame>{children}</Frame>
    </ExperimentProvider>
  );
}
