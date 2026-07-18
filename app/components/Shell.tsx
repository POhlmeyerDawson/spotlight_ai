"use client";

/**
 * The working-surface frame.
 *
 * The plate IA (one idea per full-bleed screen) is deliberately NOT forced onto
 * the dashboard — a ranked list and a trace drill-down need scanability, not one
 * idea per screen. What carries over is the design language: the sheet-as-object
 * margin and shadow, mono metadata, hairline rules, the type scale, and the
 * five-colour discipline.
 *
 * Navigation is the frame's second job and it follows one rule: WHERE YOU ARE and
 * HOW YOU GET BACK must both be on screen at all times, including after scrolling.
 * The nav row is therefore sticky, the active route is marked three ways (accent
 * colour, a leading rule, and aria-current) rather than by colour alone, and any page
 * that is a level down states its parent in a breadcrumb that is also the way back.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";
import type { ReactNode } from "react";

const NAV = [
  { href: "/", label: "Plates", hint: "the poster sequence" },
  { href: "/pipeline", label: "Pipeline", hint: "ranked list + compound query" },
  { href: "/backtest", label: "Backtest", hint: "calibration + the fame check" },
];

export interface Crumb {
  label: string;
  href?: string;
}

export default function Shell({
  title,
  lede,
  right,
  meta,
  crumbs,
  toolbar,
  children,
}: {
  title: string;
  lede?: ReactNode;
  right?: ReactNode;
  meta?: ReactNode;
  /** Trail above the title. The last entry is the current page and is not a link. */
  crumbs?: Crumb[];
  /** Sticks under the nav row — section anchors, prev/next, list controls. */
  toolbar?: ReactNode;
  children: ReactNode;
}) {
  const pathname = usePathname();

  return (
    <div
      className="g-paper relative m-[var(--sheet-margin)] min-h-[calc(100svh-var(--sheet-margin)*2)] bg-[color:var(--ground)] p-[clamp(1.25rem,2.4vw,2.25rem)] text-[color:var(--figure)]"
      style={{ boxShadow: "0 1px 2px rgb(0 0 0 / 0.06), 0 12px 34px rgb(0 0 0 / 0.09)" }}
    >
      {/*
        Sticky nav. `-mx` + `px` pulls the background to the sheet's padding edge so the
        bar covers the content scrolling beneath it instead of letting type show through.
      */}
      <div className="sticky top-0 z-40 -mx-[clamp(1.25rem,2.4vw,2.25rem)] mb-5 border-b border-[color:var(--rule)] bg-[color:var(--ground)] px-[clamp(1.25rem,2.4vw,2.25rem)]">
        <nav
          aria-label="Sections of the dashboard"
          className="flex flex-wrap items-center gap-x-1 gap-y-1 py-2.5"
        >
          {NAV.map((n) => {
            const active =
              n.href === "/" ? pathname === "/" : pathname.startsWith(n.href);
            return (
              <Link
                key={n.href}
                href={n.href}
                aria-current={active ? "page" : undefined}
                title={n.hint}
                className="meta border-l-2 px-3 py-1.5"
                style={{
                  color: active ? "var(--accent)" : "var(--muted)",
                  // Position and rule-work carry the active state alongside colour,
                  // so it survives being read without colour perception (§2).
                  borderLeftColor: active ? "var(--accent)" : "transparent",
                  background: active ? "var(--ink-09)" : "transparent",
                }}
              >
                {n.label}
              </Link>
            );
          })}
        </nav>

        {toolbar && (
          <div className="border-t border-[color:var(--rule)] py-2.5">{toolbar}</div>
        )}
      </div>

      <header className="mb-6 grid gap-x-8 gap-y-4 border-b border-[color:var(--rule)] pb-4 md:grid-cols-[minmax(0,1fr)_auto]">
        <div>
          {crumbs && crumbs.length > 0 && (
            <nav aria-label="Breadcrumb" className="meta mb-2 flex flex-wrap items-center gap-2">
              {crumbs.map((c, i) => (
                <span key={`${c.label}-${i}`} className="flex items-center gap-2">
                  {i > 0 && (
                    <span aria-hidden className="text-[color:var(--muted)]">
                      /
                    </span>
                  )}
                  {c.href ? (
                    <Link
                      href={c.href}
                      className="text-[color:var(--accent)] underline underline-offset-4"
                    >
                      {c.label}
                    </Link>
                  ) : (
                    <span className="text-[color:var(--muted)]">{c.label}</span>
                  )}
                </span>
              ))}
            </nav>
          )}
          <h1 className="quiet">{title}</h1>
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
