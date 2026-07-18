"use client";

/**
 * The sheet frame (DESIGN.md §4.1) plus the recurring furniture (§4.3).
 *
 * Every plate is the same four-row grid — that consistency is what makes a
 * series. What varies per plate is ground, visual, marker, density and presence
 * (§5), never the anatomy.
 */

import type { ReactNode } from "react";
import Reveal from "./Reveal";

export type Ground = "paper" | "cobalt" | "ink";

/** §4.2 — fixed ground/figure/accent triples. Do not improvise new pairs. */
const GROUND_CLASS: Record<Ground, string> = {
  paper: "g-paper",
  cobalt: "g-cobalt",
  ink: "g-ink",
};

export function Poster({
  ground = "paper",
  captions,
  meta,
  visual,
  children,
  stub,
  id,
}: {
  ground?: Ground;
  /** Scientific caption blocks, top-left. Clipped technical register (§11). */
  captions?: ReactNode;
  /** Mono metadata, pinned to the LAST grid column explicitly (§4.3). */
  meta?: ReactNode;
  /** Full-bleed visual. Omit entirely for the silent plate (§5). */
  visual?: ReactNode;
  /** Display type, ranged left, bottom of composition. */
  children: ReactNode;
  /** Ticket-stub table. Opening and closing plates only — all of them is monotonous. */
  stub?: ReactNode;
  id?: string;
}) {
  return (
    <section id={id} className={`sheet ${GROUND_CLASS[ground]}`}>
      {/* row 1 — captions + metadata */}
      <div className="grid grid-cols-1 gap-x-8 gap-y-4 md:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto]">
        <div className="caption text-[color:var(--muted)] md:col-span-2">
          <Reveal quiet>{captions}</Reveal>
        </div>
        {/* Pinned to column 3 explicitly, or a plate with one caption mis-places it. */}
        <div className="meta text-[color:var(--muted)] md:col-start-3 md:text-right">
          <Reveal quiet>{meta}</Reveal>
        </div>
      </div>

      {/* row 2 — the visual, bleeding past the sheet padding. Type never does. */}
      <div className="bleed min-h-[34svh]">{visual}</div>

      {/* rows 3/4 — display type, then the optional stub */}
      <div className="words">{children}</div>
      <div>{stub}</div>
    </section>
  );
}

/** A ticket-stub cell: label above value, mono, divided by 1px rules (§4.3). */
export function Stub({ label, value }: { label: string; value: ReactNode }) {
  return (
    <div>
      <div className="meta text-[color:var(--muted)]">{label}</div>
      <div className="mono mt-1 text-[13px] leading-snug">{value}</div>
    </div>
  );
}

/**
 * Registration bar — ruled measurement furniture, not an image. Used on the
 * reading plate, where the point is that the plate is made of document parts.
 */
export function Registration({ ticks = 40 }: { ticks?: number }) {
  return (
    <div className="flex h-10 items-end border-b border-[color:var(--rule)]" aria-hidden>
      {Array.from({ length: ticks }).map((_, i) => (
        <span
          key={i}
          className="flex-1 border-l border-[color:var(--rule)]"
          style={{ height: i % 5 === 0 ? "100%" : i % 5 === 2 ? "52%" : "26%" }}
        />
      ))}
    </div>
  );
}
