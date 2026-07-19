"use client";

/**
 * Integrity flags — caught injections, low-confidence OCR, transliterated-name merges.
 *
 * The caught injection is a live demo beat, so it is findable (top of the page when
 * critical), legible (the payload is quoted verbatim), and honest about what happened
 * to it (stripped at ingestion, never reached a model, and scored as a negative).
 */

import type { IntegrityFlag } from "@/lib/types";
import { EvidenceSpan } from "./ui";

const SEV: Record<IntegrityFlag["severity"], { color: string; icon: string; label: string }> = {
  critical: { color: "var(--signal)", icon: "⚠", label: "CRITICAL" },
  serious: { color: "var(--figure)", icon: "▲", label: "SERIOUS" },
  warning: { color: "var(--figure)", icon: "◍", label: "WARNING" },
};

export default function IntegrityPanel({ flags }: { flags: IntegrityFlag[] }) {
  if (!flags.length) {
    return (
      <p className="border border-[color:var(--rule)] bg-[color:var(--ground)] px-5 py-4 text-[14px] text-[color:var(--muted)]">
        No integrity flags raised. The sanitizer ran over every source and found nothing —
        this is a clean result, not an unchecked one.
      </p>
    );
  }

  return (
    <ul className="space-y-3">
      {flags.map((f, i) => {
        const s = SEV[f.severity];
        return (
          <li
            key={i}
            className="overflow-hidden border bg-[color:var(--ground)]"
            style={{ borderColor: s.color }}
          >
            <div
              className="flex flex-wrap items-center gap-3 px-4 py-2.5"
              style={{ background: `color-mix(in oklab, ${s.color} 12%, transparent)` }}
            >
              <span
                className="flex items-center gap-1.5 text-[13px] font-medium"
                style={{ color: s.color }}
              >
                <span aria-hidden>{s.icon}</span>
                {s.label}
              </span>
              <span className="font-mono text-[14px] font-medium tracking-wide text-[color:var(--figure)] uppercase">
                {f.flag.replace(/_/g, " ")}
              </span>
              <span className="font-mono text-[13px] text-[color:var(--muted)]">{f.where}</span>
            </div>

            <div className="px-4 py-3">
              {/*
                What this did to the DECISION leads the card. It is the only line here
                that completes "this matters because…" — the forensics below are the
                evidence for it, not the finding itself.
              */}
              {f.effect_on_decision && (
                <p className="mb-2 text-[16px] leading-[1.5] font-medium text-[color:var(--figure)]">
                  Effect on the decision: {f.effect_on_decision}
                </p>
              )}

              <p className="text-[15px] leading-[1.55] text-[color:var(--muted)]">{f.detail}</p>

              {f.effect_on_score && (
                <p className="mt-1.5 text-[14px] leading-[1.55] text-[color:var(--muted)]">
                  Effect on the score: {f.effect_on_score}
                </p>
              )}

              {f.quoted_span && (
                <>
                  <div className="meta mt-3 text-[color:var(--muted)]">
                    Caught payload, quoted verbatim
                  </div>
                  <EvidenceSpan>{f.quoted_span}</EvidenceSpan>
                </>
              )}

              <div className="mt-2 border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2">
                <span className="meta text-[color:var(--muted)]">
                  Action taken
                </span>
                <p className="mt-1 text-[14px] leading-[1.55] text-[color:var(--figure)]">{f.action_taken}</p>
              </div>

              {/*
                The control comparison is a claim about the DETECTOR, not about this
                company — "we did not false-positive a fast builder" is only credible
                with the control beside it, so it is rendered rather than dropped.
              */}
              {f.control_note && (
                <div className="mt-2 border border-dashed border-[color:var(--rule)] px-3 py-2">
                  <span className="meta text-[color:var(--muted)]">
                    Checked against a control
                  </span>
                  <p className="mt-1 text-[14px] leading-[1.55] text-[color:var(--muted)]">
                    {f.control_note}
                  </p>
                </div>
              )}
            </div>
          </li>
        );
      })}
    </ul>
  );
}
