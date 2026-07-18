/**
 * Shared primitives, in the plate language.
 *
 * Palette discipline (DESIGN.md §2): five hues, no sixth. Where states need
 * telling apart, this file uses ICON + LABEL + RULE-WORK (solid / dashed /
 * hatched borders) rather than reaching for more colour.
 *
 * --signal appears on exactly two things in this application: a CONTRADICTED
 * claim and a caught injection. Both are the same semantic — something asserted
 * is not true. Nothing else gets it.
 */

import type { ReactNode } from "react";
import type { ClaimStatus, GateOutcome } from "@/lib/types";

/** A bordered document block. The app's equivalent of a plate's furniture. */
export function Panel({
  title,
  subtitle,
  right,
  children,
  className = "",
  id,
  emphasis = false,
}: {
  title?: ReactNode;
  subtitle?: ReactNode;
  right?: ReactNode;
  children: ReactNode;
  className?: string;
  id?: string;
  emphasis?: boolean;
}) {
  return (
    <section
      id={id}
      className={`border bg-[color:var(--ground)] ${
        emphasis ? "border-[color:var(--accent)]" : "border-[color:var(--rule)]"
      } ${className}`}
    >
      {(title || right) && (
        <header className="flex flex-wrap items-start justify-between gap-4 border-b border-[color:var(--rule)] px-5 py-3">
          <div>
            {title && <h2 className="meta text-[color:var(--figure)]">{title}</h2>}
            {subtitle && (
              <p className="caption mt-1 max-w-[64ch] text-[color:var(--muted)]">
                {subtitle}
              </p>
            )}
          </div>
          {right}
        </header>
      )}
      <div className="p-5">{children}</div>
    </section>
  );
}

/** Trend: glyph AND signed number. Colour never carries this alone. */
export function Trend({ value, className = "" }: { value: number; className?: string }) {
  const up = value > 0.05;
  const down = value < -0.05;
  return (
    <span
      className={`mono inline-flex items-center gap-1 text-[13px] ${className}`}
      style={{ color: up ? "var(--accent)" : "var(--muted)" }}
      title={`trend ${value > 0 ? "+" : ""}${value.toFixed(1)} per period`}
    >
      <span aria-hidden>{up ? "▲" : down ? "▼" : "▬"}</span>
      <span>
        {value > 0 ? "+" : ""}
        {value.toFixed(1)}
      </span>
    </span>
  );
}

const CLAIM_META: Record<
  ClaimStatus,
  { label: string; icon: string; color: string; border: string; hatch: boolean }
> = {
  verified: {
    label: "VERIFIED",
    icon: "✓",
    color: "var(--accent)",
    border: "solid",
    hatch: false,
  },
  // The rationed colour. An assertion contradicted by evidence.
  contradicted: {
    label: "CONTRADICTED",
    icon: "✕",
    color: "var(--signal)",
    border: "solid",
    hatch: false,
  },
  unverifiable: {
    label: "UNVERIFIABLE",
    icon: "?",
    color: "var(--figure)",
    border: "dashed",
    hatch: false,
  },
  not_attempted: {
    label: "NOT ATTEMPTED",
    icon: "—",
    color: "var(--muted)",
    border: "dashed",
    hatch: true,
  },
};

export function ClaimBadge({ status }: { status: ClaimStatus }) {
  const m = CLAIM_META[status];
  return (
    <span
      className={`meta inline-flex shrink-0 items-center gap-1.5 border px-2 py-1 ${
        m.hatch ? "hatch" : ""
      }`}
      style={{ color: m.color, borderColor: m.color, borderStyle: m.border }}
    >
      <span aria-hidden>{m.icon}</span>
      {m.label}
    </span>
  );
}

const GATE_META: Record<GateOutcome, { label: string; filled: boolean; hue: string }> = {
  proceed: { label: "PROCEED", filled: true, hue: "var(--accent)" },
  proof_protocol: { label: "PROOF PROTOCOL", filled: false, hue: "var(--accent)" },
  no_call: { label: "NO CALL", filled: true, hue: "var(--figure)" },
};

export function GateBadge({ gate }: { gate: GateOutcome }) {
  const m = GATE_META[gate];
  return (
    <span
      className="meta inline-flex shrink-0 items-center border px-2 py-1"
      style={
        m.filled
          ? { background: m.hue, borderColor: m.hue, color: "var(--paper)" }
          : { borderColor: m.hue, color: m.hue }
      }
    >
      {m.label}
    </span>
  );
}

export function Loading({ label }: { label: string }) {
  return (
    <div
      role="status"
      aria-live="polite"
      className="meta border border-[color:var(--rule)] px-5 py-8 text-[color:var(--muted)]"
    >
      LOADING {label.toUpperCase()}…
    </div>
  );
}

/** Errors are shown, never swallowed — but the page still renders on fixtures. */
export function ErrorNote({ message }: { message: string }) {
  return (
    <div
      role="alert"
      className="caption max-w-none border border-dashed border-[color:var(--figure)] px-3 py-2 text-[color:var(--figure)]"
    >
      {message}
    </div>
  );
}

/** States plainly whether the numbers on screen are live or fixture. Never faked. */
export function SourceChip({ source, note }: { source: "live" | "fixture"; note?: string }) {
  const live = source === "live";
  return (
    <span
      className="meta inline-flex items-center gap-2 border px-2.5 py-1"
      style={{
        color: live ? "var(--accent)" : "var(--muted)",
        borderColor: live ? "var(--accent)" : "var(--muted)",
        borderStyle: live ? "solid" : "dashed",
      }}
      title={note ?? (live ? "served by the backend" : "backend unreachable — local fixtures")}
    >
      {live ? "LIVE API" : "FIXTURE DATA"}
    </span>
  );
}

/** A figure with its label above it, in the ticket-stub idiom. */
export function Stat({
  label,
  value,
  sub,
  color,
}: {
  label: string;
  value: ReactNode;
  sub?: ReactNode;
  color?: string;
}) {
  return (
    <div className="border border-[color:var(--rule)] px-4 py-3">
      <div className="meta text-[color:var(--muted)]">{label}</div>
      <div
        className="mt-1.5 font-[family-name:var(--font-instrument-serif)] text-[38px] leading-none"
        style={color ? { color } : undefined}
      >
        {value}
      </div>
      {sub && <div className="caption mt-1.5 max-w-none text-[color:var(--muted)]">{sub}</div>}
    </div>
  );
}

/** The thing every trace must bottom out in. */
export function EvidenceSpan({ children }: { children: ReactNode }) {
  return (
    <blockquote className="evidence-span my-2 px-4 py-3">“{children}”</blockquote>
  );
}
