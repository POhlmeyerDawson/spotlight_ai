"use client";

/**
 * The receipts claim, made literal.
 *
 * score → contributing events → source span → original URL / slide id.
 *
 * A trace that stops at a source name is a broken trace, so every leaf here renders
 * the QUOTED SPAN itself. The last row of each event is the original link.
 */

import { useEffect, useState } from "react";
import type { AxisKey, CompanyDetail, EvidenceEvent } from "@/lib/types";
import { AXIS_LABEL } from "@/lib/types";
import { EvidenceSpan } from "./ui";

function FlagChip({ flag }: { flag: string }) {
  const critical = flag === "injection_stripped" || flag === "fabricated_history";
  const color = critical ? "var(--signal)" : "var(--figure)";
  return (
    <span
      className="inline-flex items-center gap-1 border px-1.5 py-0.5 text-[11px] font-medium tracking-wider uppercase"
      style={{ color, borderColor: color, background: `${color}14` }}
    >
      <span aria-hidden>{critical ? "⚠" : "◍"}</span>
      {flag.replace(/_/g, " ")}
    </span>
  );
}

function EventRow({ ev, color }: { ev: EvidenceEvent; color: string }) {
  const [open, setOpen] = useState(false);
  const positive = ev.contribution >= 0;

  return (
    <li className="border-b border-[color:var(--rule)] last:border-b-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-4 px-4 py-3 text-left transition hover:bg-[color:var(--ink-09)]"
      >
        <span
          className="mono mt-0.5 w-16 shrink-0 text-right text-[19px] font-medium"
          style={{ color: positive ? "var(--accent)" : "var(--figure)" }}
        >
          {positive ? "+" : ""}
          {ev.contribution.toFixed(1)}
        </span>
        <span className="min-w-0 flex-1">
          <span className="flex flex-wrap items-center gap-2">
            <span className="bg-[color:var(--ink-09)] px-1.5 py-0.5 font-mono text-[11px] tracking-wide text-[color:var(--muted)] uppercase">
              {ev.kind.replace(/_/g, " ")}
            </span>
            <span className="text-[12px] text-[color:var(--muted)]">
              {ev.source} · {new Date(ev.observed_at).toISOString().slice(0, 10)}
            </span>
            {ev.integrity_flags.map((f) => (
              <FlagChip key={f} flag={f} />
            ))}
          </span>
          <span className="mt-1 block text-[14px] leading-snug text-[color:var(--figure)]">{ev.summary}</span>
          <span className="mt-1 block font-mono text-[12px] text-[color:var(--muted)]">{ev.locator}</span>
        </span>
        <span className="mt-1 shrink-0 text-[13px]" style={{ color }}>
          {open ? "▾ span" : "▸ span"}
        </span>
      </button>

      {open && (
        <div className="bg-[color:var(--ink-09)] px-4 pt-1 pb-4 pl-[5.5rem]">
          <div className="meta text-[color:var(--muted)]">
            Quoted source span
          </div>
          {ev.evidence_span ? (
            <EvidenceSpan>{ev.evidence_span}</EvidenceSpan>
          ) : (
            <p className="my-2 border border-dashed border-[color:var(--figure)] px-3 py-2 text-[13px] text-[color:var(--muted)]">
              No span captured for this event. It is excluded from scoring — we do not
              score evidence we cannot quote.
            </p>
          )}
          <div className="mono mt-2 flex flex-wrap items-center gap-x-5 gap-y-1 text-[12px] text-[color:var(--muted)]">
            <span>
              extraction confidence{" "}
              <strong className="text-[color:var(--figure)]">{(ev.confidence * 100).toFixed(0)}%</strong>
            </span>
            <span>
              observed_at{" "}
              <strong className="text-[color:var(--figure)]">
                {new Date(ev.observed_at).toISOString().replace("T", " ").slice(0, 16)}Z
              </strong>
            </span>
            <span className="font-mono">event_id {ev.event_id}</span>
          </div>
          {ev.source_url && (
            <a
              href={ev.source_url}
              target="_blank"
              rel="noreferrer noopener"
              className="mt-2 inline-block max-w-full truncate font-mono text-[13px] underline decoration-dotted underline-offset-4"
              style={{ color }}
            >
              ↗ {ev.source_url}
            </a>
          )}
        </div>
      )}
    </li>
  );
}

export default function TraceDrawer({
  company,
  axisKey,
  onClose,
}: {
  company: CompanyDetail;
  axisKey: AxisKey | null;
  onClose: () => void;
}) {
  useEffect(() => {
    if (!axisKey) return;
    const onKey = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [axisKey, onClose]);

  if (!axisKey) return null;

  const axis = company.axes[axisKey];
  const color = "var(--accent)";
  const events = axis.evidence_event_ids
    .map((id) => company.events.find((e) => e.event_id === id))
    .filter((e): e is EvidenceEvent => Boolean(e))
    .sort((a, b) => Math.abs(b.contribution) - Math.abs(a.contribution));

  return (
    <div className="fixed inset-0 z-50 flex justify-end">
      <div
        className="absolute inset-0"
        style={{ background: "color-mix(in srgb, var(--ink) 62%, transparent)" }}
        onClick={onClose}
        aria-hidden
      />
      <aside
        role="dialog"
        aria-modal="true"
        aria-label={`${AXIS_LABEL[axisKey]} score trace`}
        className="relative flex h-full w-full max-w-[720px] flex-col overflow-y-auto border-l border-[color:var(--figure)] bg-[color:var(--ground)] shadow-2xl"
      >
        <header
          className="sticky top-0 z-10 border-b border-[color:var(--rule)] bg-[color:var(--ground)] px-5 py-4"
          style={{ borderTopColor: color, borderTopWidth: 4 }}
        >
          <div className="flex items-start justify-between gap-4">
            <div>
              <div className="meta text-[color:var(--muted)]">
                Trace · {company.name}
              </div>
              <h2 className="mt-1 text-[26px] leading-tight font-medium" style={{ color }}>
                {AXIS_LABEL[axisKey]}{" "}
                <span className="mono text-[color:var(--figure)]">
                  {axis.score.toFixed(0)}
                  <span className="text-[18px] text-[color:var(--muted)]"> ±{axis.band.toFixed(1)}</span>
                </span>
              </h2>
              <p className="mt-1 text-[13px] text-[color:var(--muted)]">
                {events.length} contributing {events.length === 1 ? "event" : "events"} · open
                any row for the quoted span and the original source
              </p>
            </div>
            <button
              type="button"
              onClick={onClose}
              className="border border-[color:var(--rule)] px-3 py-1.5 text-[14px] text-[color:var(--muted)] transition hover:bg-[color:var(--ink-09)]"
            >
              Close ✕
            </button>
          </div>
        </header>

        <ol className="flex-1">
          {events.length ? (
            events.map((ev) => <EventRow key={ev.event_id} ev={ev} color={color} />)
          ) : (
            <li className="px-5 py-8 text-[14px] text-[color:var(--muted)]">
              No contributing events recorded for this axis.
            </li>
          )}
        </ol>

        <footer className="border-t border-[color:var(--rule)] bg-[color:var(--ink-09)] px-5 py-3 text-[12px] text-[color:var(--muted)]">
          Every row bottoms out in text the source actually contains. Nothing on this panel
          is generated prose.
        </footer>
      </aside>
    </div>
  );
}
