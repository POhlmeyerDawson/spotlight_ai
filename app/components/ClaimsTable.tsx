"use client";

/**
 * Per-claim trust. Invariant #2: there is no company-level trust number anywhere,
 * so this renders a row per claim and never a roll-up.
 *
 * NOT_ATTEMPTED is rendered as loudly as VERIFIED — hatched, labelled, and carrying
 * the reason we did not look. Hiding the gaps would be the dishonest rendering, and
 * a memo that looks complete because it omitted what it skipped fails on trust.
 */

import { useState } from "react";
import type { ClaimStatus, ClaimVerdict } from "@/lib/types";
import { ClaimBadge, EvidenceSpan, SourceRef } from "./ui";

const ORDER: ClaimStatus[] = ["contradicted", "verified", "unverifiable", "not_attempted"];

function Row({ claim }: { claim: ClaimVerdict }) {
  const [open, setOpen] = useState(false);
  const na = claim.status === "not_attempted";
  const fraudShaped =
    claim.status === "contradicted" &&
    claim.claim_asserted_at &&
    claim.counter_evidence_at &&
    new Date(claim.counter_evidence_at) > new Date(claim.claim_asserted_at);

  return (
    <li className={`border-b border-[color:var(--rule)] last:border-b-0 ${na ?"hatch" :""}`}>
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-expanded={open}
        className="flex w-full items-start gap-4 px-4 py-3 text-left transition hover:bg-[color:var(--ink-09)]"
      >
        <span className="min-w-0 flex-1">
          <span className="block text-[15px] leading-snug font-medium text-[color:var(--figure)]">
            {claim.claim_text}
          </span>
          <span className="mt-1 flex flex-wrap items-center gap-x-4 gap-y-1 font-mono text-[12px] text-[color:var(--muted)]">
            <span>said at: {claim.claim_source_span}</span>
            {claim.self_published && <span>self-published</span>}
            {claim.claim_asserted_at && (
              <span>asserted {claim.claim_asserted_at.slice(0, 10)}</span>
            )}
            {claim.counter_evidence_at && (
              <span style={{ color: "var(--signal)" }}>
                counter-evidence {claim.counter_evidence_at.slice(0, 10)}
              </span>
            )}
          </span>
        </span>

        <span className="flex shrink-0 flex-col items-end gap-1.5">
          <ClaimBadge status={claim.status} />
          <span className="mono text-[13px] text-[color:var(--muted)]">
            trust <strong className="text-[color:var(--figure)]">{claim.trust.toFixed(2)}</strong>
          </span>
        </span>
      </button>

      {open && (
        <div className="bg-[color:var(--ink-09)] px-4 pt-0 pb-4">
          {fraudShaped && (
            <p
              className="mb-2 border px-3 py-2 text-[13px] leading-snug"
              style={{
                color: "var(--signal)",
                borderColor: "var(--signal)",
                background: "color-mix(in oklab, var(--signal) 10%, transparent)",
              }}
            >
              ⚠ The counter-evidence is <strong>later</strong> than the claim
              ({claim.claim_asserted_at?.slice(0, 10)} → {claim.counter_evidence_at?.slice(0, 10)}).
              This is the fraud-shaped ordering, not a stale deck.
            </p>
          )}

          {na ? (
            <div className="border border-dashed border-[color:var(--figure)] bg-[color:var(--ink-09)] px-4 py-3">
              <div className="meta text-[color:var(--muted)]">
                Why we did not look
              </div>
              <p className="mt-1.5 text-[14px] leading-[1.55] text-[color:var(--muted)]">
                {claim.not_attempted_reason ??
                  "No verification was attempted. We are not implying anything about this claim either way."}
              </p>
              <p className="mt-2 text-[13px] text-[color:var(--muted)]">
                Trust stays at the 0.50 prior: this is an <strong>absence of checking</strong>,
                not a finding.
              </p>
            </div>
          ) : claim.corroborating_span ? (
            <>
              <div className="meta text-[color:var(--muted)]">
                {claim.status === "contradicted"
                  ? "Contradicting span"
                  : "Corroborating span"}
              </div>
              <EvidenceSpan>{claim.corroborating_span}</EvidenceSpan>
            </>
          ) : (
            <p className="border border-dashed border-[color:var(--figure)] px-3 py-2 text-[14px] text-[color:var(--muted)]">
              We looked and found nothing to check this against. No independent source
              exists — that is what UNVERIFIABLE means here, and it is not the same as
              false.
            </p>
          )}

          {claim.corroborating_url && (
            <SourceRef url={claim.corroborating_url} className="mt-1" />
          )}
        </div>
      )}
    </li>
  );
}

export default function ClaimsTable({ claims }: { claims: ClaimVerdict[] }) {
  const sorted = [...claims].sort(
    (a, b) => ORDER.indexOf(a.status) - ORDER.indexOf(b.status),
  );
  const counts = ORDER.map((s) => ({ s, n: claims.filter((c) => c.status === s).length }));

  return (
    <div>
      <div className="mb-3 flex flex-wrap gap-2">
        {counts.map(({ s, n }) => (
          <span key={s} className="flex items-center gap-1.5">
            <ClaimBadge status={s} />
            <span className="mono text-[15px] font-medium text-[color:var(--figure)]">{n}</span>
          </span>
        ))}
      </div>

      <ol className="overflow-hidden border border-[color:var(--rule)] bg-[color:var(--ground)]">
        {sorted.map((c) => (
          <Row key={c.claim_id} claim={c} />
        ))}
      </ol>

      <p className="mt-2 text-[13px] leading-[1.55] text-[color:var(--muted)]">
        Each claim carries its own status and its own trust value. There is deliberately no
        company-level trust score to average these into.
      </p>
    </div>
  );
}
