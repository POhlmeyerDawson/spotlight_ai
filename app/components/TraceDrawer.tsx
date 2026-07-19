"use client";

/**
 * The receipts claim, made literal.
 *
 * score → contributing events → source span → original URL / slide id.
 *
 * A trace that stops at a source name is a broken trace, so every leaf here renders
 * the QUOTED SPAN itself. The last row of each event is the original link.
 */

import { useEffect, useRef, useState } from "react";
import { getTrace } from "@/lib/api";
import type { AxisKey, CompanyDetail, EventTrace, EvidenceEvent } from "@/lib/types";
import { AXIS_LABEL } from "@/lib/types";
import { EvidenceSpan, SourceRef } from "./ui";

/**
 * Evidence is grouped by WHAT IT EVIDENCES, not by which source it came from.
 *
 * "Six GitHub events, two HN events" tells you where we looked. "Shipping cadence,
 * public reception" tells you what was established — which is the thing a reader is
 * actually assembling as they scan, and the thing that lets them notice a group with
 * only one thin item in it.
 */
const EVIDENCE_GROUP: { id: string; label: string; blurb: string; kinds: string[] }[] = [
  {
    id: "building",
    label: "That they are building",
    blurb: "Repository activity, bursts, and tagged releases.",
    kinds: ["repo_activity", "commit_burst", "release"],
  },
  {
    id: "reception",
    label: "How the work was received",
    blurb: "Public discussion and published research.",
    kinds: ["paper", "hn_post", "hn_comment"],
  },
  {
    id: "asserted",
    label: "What the founder asserts",
    blurb: "Self-reported claims from decks and profiles — not yet corroborated here.",
    kinds: ["deck_claim", "profile_fact"],
  },
  {
    id: "assessed",
    label: "What the system concluded",
    blurb:
      "Rollups and validator output. These are computed by this system, not receipts — each one traces to the events underneath it.",
    kinds: ["green_flag", "validation_result", "contradiction", "entity_merge"],
  },
  {
    id: "proof",
    label: "Proof Protocol",
    blurb: "Evidence this system created because none existed to find.",
    kinds: ["proof_challenge_issued", "proof_artifact", "proof_behavior"],
  },
  {
    id: "integrity",
    label: "Integrity findings",
    blurb: "What the sanitizer caught in the sources.",
    kinds: ["integrity"],
  },
];

function groupOf(kind: string): string {
  return EVIDENCE_GROUP.find((g) => g.kinds.includes(kind))?.id ?? "assessed";
}

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

/**
 * The receipts under a generated span.
 *
 * A rollup event ("1/24 applicable green flags fired") has a real span, but this system
 * wrote it. Presenting that as the receipt would be exactly the flattening this drawer
 * exists to prevent, so the generated summary is labelled as generated and the actual
 * source spans are rendered beneath it, each with its own URL. The drill-down bottoms
 * out HERE, in a commit span, not in the rollup.
 */
function UnderlyingReceipts({
  trace,
  color,
}: {
  trace: EventTrace;
  color: string;
}) {
  if (!trace.underlying_evidence.length) return null;
  return (
    <div className="mt-3 border-l-2 pl-3" style={{ borderColor: color }}>
      <div className="meta text-[color:var(--muted)]">
        Underlying evidence — {trace.underlying_evidence.length}{" "}
        {trace.underlying_evidence.length === 1 ? "receipt" : "receipts"} beneath that
        summary
      </div>
      <ul className="mt-1.5 space-y-2.5">
        {trace.underlying_evidence.map((u) => (
          <li key={u.event_id}>
            <div className="mono flex flex-wrap items-center gap-x-3 text-[12px] text-[color:var(--muted)]">
              <span className="bg-[color:var(--ink-09)] px-1.5 py-0.5 uppercase">
                {u.kind.replace(/_/g, " ")}
              </span>
              <span>{u.source}</span>
              {u.observed_at && (
                <span>{new Date(u.observed_at).toISOString().slice(0, 10)}</span>
              )}
            </div>
            {u.quoted_span ? (
              <EvidenceSpan>{u.quoted_span}</EvidenceSpan>
            ) : (
              <p className="my-1.5 text-[13px] text-[color:var(--muted)]">
                This receipt carries no quoted span.
              </p>
            )}
            {u.source_url && <SourceRef url={u.source_url} color={color} />}
          </li>
        ))}
      </ul>
    </div>
  );
}

function EventRow({
  ev,
  color,
  companyId,
}: {
  ev: EvidenceEvent;
  color: string;
  companyId: string;
}) {
  const [open, setOpen] = useState(false);
  const [trace, setTrace] = useState<EventTrace | null>(null);
  const [traceState, setTraceState] = useState<"idle" | "loading" | "done" | "failed">(
    "idle",
  );
  // Guards against a second fetch on re-render. A ref rather than reading `traceState`
  // in the effect body, so the effect does not have to setState synchronously to record
  // that it started — which is what cascades renders.
  const requested = useRef(false);

  // The trace is fetched only when the row is opened. Pre-fetching every row would fire
  // one request per event on a company with sixty of them, for panels nobody opens.
  useEffect(() => {
    if (!open || requested.current) return;
    requested.current = true;
    let live = true;
    (async () => {
      setTraceState("loading");
      const t = await getTrace(companyId, ev.event_id);
      if (!live) return;
      setTrace(t);
      setTraceState(t ? "done" : "failed");
    })();
    return () => {
      live = false;
    };
  }, [open, companyId, ev.event_id]);

  // A null contribution means the source listed this event as evidence without
  // attributing score units to it. Printing "+0.0" would assert it moved the score by
  // nothing, which is a claim the data does not make. It gets an em dash.
  const contribution = ev.contribution;
  const positive = contribution !== null && contribution >= 0;

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
          style={{
            color:
              contribution === null
                ? "var(--muted)"
                : positive
                  ? "var(--accent)"
                  : "var(--figure)",
          }}
          title={
            contribution === null
              ? "This source lists the event as evidence but does not attribute a per-event contribution to it."
              : undefined
          }
        >
          {contribution === null
            ? "—"
            : `${positive ? "+" : ""}${contribution.toFixed(1)}`}
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
          {(() => {
            // Prefer the server's trace: it is the only source that knows whether the
            // span it carries is a receipt or a summary this system generated.
            const span = trace?.quoted_span ?? ev.evidence_span;
            const generated = trace?.span_is_generated === true;

            if (traceState === "loading" && !ev.evidence_span) {
              return (
                <p className="my-2 text-[13px] text-[color:var(--muted)]">
                  Resolving the trace…
                </p>
              );
            }

            if (!span) {
              return (
                <>
                  <div className="meta text-[color:var(--muted)]">Quoted source span</div>
                  <p className="my-2 border border-dashed border-[color:var(--figure)] px-3 py-2 text-[13px] text-[color:var(--muted)]">
                    No span captured for this event. It is excluded from scoring — we do
                    not score evidence we cannot quote.
                  </p>
                </>
              );
            }

            return (
              <>
                <div className="meta text-[color:var(--muted)]">
                  {generated
                    ? "Rollup summary — generated by this system, not a receipt"
                    : "Quoted source span"}
                </div>
                <EvidenceSpan>{span}</EvidenceSpan>
                {generated && (
                  <p className="text-[13px] leading-snug text-[color:var(--muted)]">
                    This sentence was written by the scorer to describe a rollup. It is
                    not text any source contains, so the receipts it rests on are below.
                  </p>
                )}
                {trace && <UnderlyingReceipts trace={trace} color={color} />}
                {generated && trace?.underlying_evidence.length === 0 && (
                  <p className="mt-2 border border-dashed border-[color:var(--figure)] px-3 py-2 text-[13px] text-[color:var(--muted)]">
                    The server reported no underlying evidence for this rollup, so the
                    trace stops here rather than at a source span.
                  </p>
                )}
                {traceState === "failed" && (
                  <p className="mt-2 text-[13px] text-[color:var(--muted)]">
                    The trace endpoint could not be reached, so this shows the span held
                    locally. Any deeper evidence is unavailable rather than absent.
                  </p>
                )}
              </>
            );
          })()}
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
            <SourceRef url={ev.source_url} color={color} className="mt-2" />
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
      if (e.key !== "Escape") return;
      // Escape also returns the company page to the pipeline. Stopping propagation
      // here means the first Escape closes the drawer and the second one navigates,
      // instead of one keypress doing both.
      e.stopPropagation();
      onClose();
    };
    // Capture phase, so this runs before the page-level Escape handler on window.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [axisKey, onClose]);

  if (!axisKey) return null;

  // An axis the backend never sent has no trace to open. Returning null keeps the
  // drawer shut rather than rendering an empty trace, which would read as "we looked
  // and found no evidence" for an axis we never computed.
  const axis = company.axes[axisKey];
  if (!axis) return null;
  const color = "var(--accent)";
  const events = axis.evidence_event_ids
    .map((id) => company.events.find((e) => e.event_id === id))
    .filter((e): e is EvidenceEvent => Boolean(e))
    // Largest absolute contribution first; unattributed events sort to the end rather
    // than being ranked as though they contributed nothing.
    .sort((a, b) => Math.abs(b.contribution ?? -1) - Math.abs(a.contribution ?? -1));

  const missingSpans = axis.evidence_event_ids.length - events.length;

  // Grouped by what each event evidences, in the fixed order declared above. Empty
  // groups are dropped: a heading with nothing under it is noise, not an absence claim
  // (the absence claims that matter are per-axis and per-claim, and both are elsewhere).
  const grouped = EVIDENCE_GROUP.map((group) => ({
    group,
    items: events.filter((e) => groupOf(e.kind) === group.id),
  })).filter((g) => g.items.length > 0);

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
                  {axis.score === null ? (
                    <span className="text-[color:var(--muted)]">not scored</span>
                  ) : (
                    <>
                      {axis.score.toFixed(0)}
                      <span className="text-[18px] text-[color:var(--muted)]">
                        {" "}
                        {axis.band === null
                          ? "· no interval"
                          : `±${axis.band.toFixed(1)}`}
                      </span>
                    </>
                  )}
                </span>
              </h2>
              <p className="mt-1 text-[13px] text-[color:var(--muted)]">
                {events.length} contributing {events.length === 1 ? "event" : "events"} · open
                any row for the quoted span and the original source
                {missingSpans > 0 && (
                  <>
                    {" "}
                    · {missingSpans} referenced{" "}
                    {missingSpans === 1 ? "event is" : "events are"} not in the local
                    event log and cannot be shown
                  </>
                )}
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

        <div className="flex-1">
          {events.length ? (
            grouped.map(({ group, items }) => (
              <section key={group.id}>
                <header className="sticky top-[104px] z-[5] border-y border-[color:var(--rule)] bg-[color:var(--ink-09)] px-5 py-2">
                  <h3 className="meta text-[color:var(--figure)]">
                    {group.label} · {items.length}
                  </h3>
                  <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
                    {group.blurb}
                  </p>
                </header>
                <ol>
                  {items.map((ev) => (
                    <EventRow
                      key={ev.event_id}
                      ev={ev}
                      color={color}
                      companyId={company.id}
                    />
                  ))}
                </ol>
              </section>
            ))
          ) : (
            /*
              Previously this asserted "nothing was observed — the score above is absent
              for that reason". Both halves can be false: an axis can be scored and still
              list no events the local payload resolved, and the backend now supplies its
              own reason. So the backend's reason is preferred and the fallback claims
              only what is actually known — that this page could not resolve the events.
            */
            <p className="px-5 py-8 text-[14px] leading-[1.55] text-[color:var(--muted)]">
              {axis.reason ??
                (axis.evidence_event_ids.length
                  ? `This axis cites ${axis.evidence_event_ids.length} event(s), but none of them are in the event log this page received, so none can be shown. They are unresolved here rather than absent from the system.`
                  : "No contributing events are attached to this axis, and the backend did not state a reason.")}
            </p>
          )}
        </div>

        <footer className="border-t border-[color:var(--rule)] bg-[color:var(--ink-09)] px-5 py-3 text-[12px] leading-[1.5] text-[color:var(--muted)]">
          Every row bottoms out in text a source actually contains. Where a row&apos;s own
          span was generated by this system, it is labelled as generated and the receipts
          beneath it are shown.
        </footer>
      </aside>
    </div>
  );
}
