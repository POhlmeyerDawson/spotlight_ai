"use client";

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

import { useEffect, useRef, useState, type ReactNode } from "react";
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

// ---------------------------------------------------------------------------
// Progress
//
// A progress bar is FURNITURE (DESIGN.md §4.3): mono, ruled, restrained. It gets the
// plate accent and never --signal — that colour is rationed to contradicted claims and
// caught injections, and spending it on a loading bar would cost it its meaning.
//
// Motion is expo.out with no overshoot (§8.2). The bar eases toward its target rather
// than springing to it, which is what makes waiting feel like weight instead of jitter.
// ---------------------------------------------------------------------------

/**
 * Drives a 0..1 value that eases toward completion over `budgetMs` without ever
 * reaching it, then snaps to 1 when the work actually finishes.
 *
 * This is the honest version of a determinate bar for work whose duration we cannot
 * know: the curve is asymptotic, so it always LOOKS like it is making progress and it
 * never lies by claiming to be done. `budgetMs` is the same number the request aborts
 * at, so a bar that has visibly flattened is a request that is genuinely about to time
 * out rather than one that stalled silently.
 */
export function useProgress(active: boolean, budgetMs: number): number {
  const [value, setValue] = useState(0);
  const startedAt = useRef(0);

  useEffect(() => {
    if (!active) {
      // Land on 1 so the bar completes rather than vanishing mid-travel, then reset.
      const done = setTimeout(() => setValue((v) => (v > 0 ? 1 : 0)), 0);
      const reset = setTimeout(() => setValue(0), 420);
      return () => {
        clearTimeout(done);
        clearTimeout(reset);
      };
    }

    startedAt.current = performance.now();
    const tick = () => {
      const elapsed = performance.now() - startedAt.current;
      // 1 - e^(-3t) reaches ~0.95 at the budget and never touches 1.
      setValue(Math.max(0.04, Math.min(0.97, 1 - Math.exp((-3 * elapsed) / budgetMs))));
    };
    // Deferred rather than called inline: setState in an effect body cascades renders.
    const first = setTimeout(tick, 0);
    const id = setInterval(tick, 90);
    return () => {
      clearTimeout(first);
      clearInterval(id);
    };
  }, [active, budgetMs]);

  return value;
}

/**
 * The bar itself. `value` is 0..1. Rendered as a hairline track with a filled rule —
 * the same rule-work the rest of the document uses, not a rounded consumer widget.
 */
export function ProgressBar({
  value,
  label,
  className = "",
}: {
  value: number;
  label?: string;
  className?: string;
}) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div className={className}>
      <div
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(pct)}
        aria-label={label ?? "Loading"}
        className="relative h-[3px] w-full overflow-hidden bg-[color:var(--ink-09)]"
      >
        <span
          className="absolute top-0 left-0 h-[3px] bg-[color:var(--accent)]"
          style={{
            width: `${pct}%`,
            transition: "width 900ms var(--expo-out)",
          }}
        />
      </div>
      {label && (
        <div className="meta mt-1.5 text-[color:var(--muted)]">{label}</div>
      )}
    </div>
  );
}

/**
 * Page-level loading. A bar plus the name of the thing being fetched, so a slow page
 * says WHAT is slow. `stages` narrates multi-step work — the current stage is the one
 * the progress value has reached.
 */
export function Loading({
  label,
  // Matches TIMEOUT.read in lib/api.ts. Kept as a literal rather than an import so a
  // presentational primitive does not depend on the transport layer; if that budget
  // changes, this is the one number to change with it.
  budgetMs = 8000,
  stages,
}: {
  label: string;
  budgetMs?: number;
  stages?: string[];
}) {
  const value = useProgress(true, budgetMs);
  const stage = stages?.length
    ? stages[Math.min(stages.length - 1, Math.floor(value * stages.length))]
    : null;

  return (
    <div
      role="status"
      aria-live="polite"
      className="border border-[color:var(--rule)] px-5 py-6"
    >
      <div className="meta text-[color:var(--muted)]">LOADING {label.toUpperCase()}…</div>
      <ProgressBar value={value} className="mt-3" />
      {stage && (
        <p className="caption mt-2 max-w-none text-[color:var(--muted)]">{stage}</p>
      )}
    </div>
  );
}

/**
 * Inline busy state for a control that is mid-flight. Pairs with a disabled button and
 * carries the REASON it is disabled — a greyed control with no explanation is the exact
 * thing that made this feature look broken.
 */
export function Busy({
  label,
  budgetMs,
  stages,
  className = "",
}: {
  label: string;
  budgetMs: number;
  /** Narrates multi-step work. The stage shown is the one the bar has reached. */
  stages?: string[];
  className?: string;
}) {
  const value = useProgress(true, budgetMs);
  const stage = stages?.length
    ? stages[Math.min(stages.length - 1, Math.floor(value * stages.length))]
    : null;
  return (
    <div role="status" aria-live="polite" className={className}>
      <ProgressBar value={value} />
      <div className="meta mt-1.5 text-[color:var(--muted)]">{label}</div>
      {stage && (
        <p className="caption mt-1 max-w-none text-[color:var(--muted)]">{stage}</p>
      )}
    </div>
  );
}

/** Errors are shown, never swallowed — but the page still renders on fixtures. */
export function ErrorNote({
  message,
  onRetry,
  retryLabel = "RETRY",
}: {
  message: string;
  /** When present, the error carries an escape hatch instead of being a dead end. */
  onRetry?: () => void;
  retryLabel?: string;
}) {
  return (
    <div
      role="alert"
      className="flex flex-wrap items-center justify-between gap-3 border border-dashed border-[color:var(--figure)] px-3 py-2"
    >
      <span className="caption max-w-none text-[color:var(--figure)]">{message}</span>
      {onRetry && (
        <button
          type="button"
          onClick={onRetry}
          className="meta shrink-0 border border-[color:var(--accent)] px-3 py-1 text-[color:var(--accent)]"
        >
          {retryLabel}
        </button>
      )}
    </div>
  );
}

/**
 * A deliberate empty state. Distinct from an error and distinct from loading: it states
 * what was asked, that the answer was legitimately nothing, and offers the way out.
 * Zero results are an ANSWER, and this is what makes them read as one.
 */
export function EmptyState({
  title,
  children,
  action,
}: {
  title: string;
  children?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="border border-dashed border-[color:var(--rule)] px-5 py-8 text-center">
      <p className="mono text-[15px] text-[color:var(--figure)]">{title}</p>
      {children && (
        <div className="caption mx-auto mt-2 max-w-[52ch] text-[color:var(--muted)]">
          {children}
        </div>
      )}
      {action && <div className="mt-4 flex justify-center gap-2">{action}</div>}
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

// ---------------------------------------------------------------------------
// Source references
//
// Every citation in this app is rendered from stored data, and a large share of that
// data CANNOT resolve — by design. Two families:
//
//   *.example.invalid   the seeded corpus. `.invalid` is reserved by RFC 2606
//                       precisely so it never resolves; the commit that introduced it
//                       ("Nothing constructed may cite a URL that resolves") is what
//                       stops a demo from appearing to cite real firms.
//   deck:// proof://    internal handles for a pitch deck page and a proof-protocol
//                       transcript turn. No browser has a handler for either.
//
// Rendering those as ordinary <a target="_blank"> was the bug: the interface promised
// navigation it could not deliver, so every evidence link led to a browser error that
// reads to a user as a 404. The URL is still shown in full — it is the receipt, and
// hiding it would cost more than the broken link did — but it is shown as a REFERENCE,
// with what it points at named, rather than as something to click.
// ---------------------------------------------------------------------------

/** Why a reference cannot be opened, or null when it can. */
function unresolvable(url: string): string | null {
  let u: URL;
  try {
    u = new URL(url, "https://placeholder.invalid");
  } catch {
    return "not a parseable URL";
  }

  if (u.protocol === "deck:") return "page of a stored pitch deck — not a web address";
  if (u.protocol === "proof:") return "turn in a stored proof-protocol transcript";
  if (u.protocol !== "http:" && u.protocol !== "https:") {
    return `${u.protocol.replace(":", "")} reference — not a web address`;
  }

  const host = u.hostname.toLowerCase();
  // RFC 2606 / RFC 6761 reserved names. Guaranteed never to resolve.
  if (
    host === "placeholder.invalid" ||
    host.endsWith(".invalid") ||
    host.endsWith(".test") ||
    host.endsWith(".example") ||
    host === "example.com" ||
    host === "example.net" ||
    host === "example.org"
  ) {
    return "seeded corpus — a reserved address that resolves nowhere, on purpose";
  }
  return null;
}

/**
 * A cited source. Opens in a new tab when the address can actually be reached, and
 * renders as a plainly-labelled dead reference when it cannot.
 *
 * `color` lets a caller carry the axis colour a trace is already using; it applies to
 * the live form only — a reference that goes nowhere must not look like a link.
 */
export function SourceRef({
  url,
  color,
  className = "",
}: {
  url: string;
  color?: string;
  className?: string;
}) {
  const why = unresolvable(url);

  if (!why) {
    return (
      <a
        href={url}
        target="_blank"
        rel="noreferrer noopener"
        className={`inline-block max-w-full truncate font-mono text-[13px] underline decoration-dotted underline-offset-4 ${className}`}
        style={{ color: color ?? "var(--accent)" }}
      >
        ↗ {url}
      </a>
    );
  }

  return (
    <span className={`block max-w-full ${className}`}>
      <span
        className="mono block truncate text-[13px] text-[color:var(--muted)]"
        title={url}
      >
        {url}
      </span>
      <span className="meta mt-0.5 block text-[color:var(--muted)]">
        <span aria-hidden>⊘</span> {why}
      </span>
    </span>
  );
}
