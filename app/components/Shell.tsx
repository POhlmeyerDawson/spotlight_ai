"use client";

/**
 * The working-surface frame.
 *
 * The plate IA (one idea per full-bleed screen) is deliberately NOT forced onto
 * the dashboard — a ranked list and a trace drill-down need scanability, not one
 * idea per screen. What carries over is the design language: the sheet-as-object
 * margin and shadow, mono metadata, hairline rules, the type scale, and the
 * five-colour discipline.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Plates" },
  { href: "/pipeline", label: "Pipeline" },
  { href: "/backtest", label: "Backtest" },
];

export default function Shell({
  title,
  lede,
  right,
  meta,
  children,
}: {
  title: string;
  lede?: ReactNode;
  right?: ReactNode;
  meta?: ReactNode;
  children: ReactNode;
}) {
  const pathname = usePathname();

  return (
    <div
      className="g-paper relative m-[var(--sheet-margin)] min-h-[calc(100svh-var(--sheet-margin)*2)] bg-[color:var(--ground)] p-[clamp(1.25rem,2.4vw,2.25rem)] text-[color:var(--figure)]"
      style={{ boxShadow: "0 1px 2px rgb(0 0 0 / 0.06), 0 12px 34px rgb(0 0 0 / 0.09)" }}
    >
      <header className="mb-6 grid gap-x-8 gap-y-4 border-b border-[color:var(--rule)] pb-4 md:grid-cols-[minmax(0,1fr)_auto]">
        <div>
          <nav className="flex flex-wrap items-center gap-x-5 gap-y-1">
            {NAV.map((n) => {
              const active = n.href === pathname;
              return (
                <Link
                  key={n.href}
                  href={n.href}
                  aria-current={active ? "page" : undefined}
                  className="meta"
                  style={{
                    color: active ? "var(--accent)" : "var(--muted)",
                    textDecoration: active ? "underline" : "none",
                    textUnderlineOffset: "4px",
                  }}
                >
                  {n.label}
                </Link>
              );
            })}
          </nav>
          <h1 className="quiet mt-3">{title}</h1>
          {lede && <p className="lede mt-2 text-[color:var(--muted)]">{lede}</p>}
        </div>
        <div className="flex flex-col items-start gap-2 md:items-end">
          {right}
          {meta && <div className="meta text-[color:var(--muted)] md:text-right">{meta}</div>}
        </div>
      </header>

      {children}

      <footer className="caption mt-10 max-w-none border-t border-[color:var(--rule)] pt-4 text-[color:var(--muted)]">
        Scores are per-axis and are never averaged. Every number traces to a quoted source
        span.
      </footer>
    </div>
  );
}
