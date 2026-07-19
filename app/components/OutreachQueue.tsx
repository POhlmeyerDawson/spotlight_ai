"use client";

/**
 * Outbound across the whole pipeline: the eligibility sweep, and the review queue.
 *
 * TWO THINGS THIS PANEL IS FOR.
 *
 * 1. THE FUNNEL, AUDITED FROM THE REFUSAL END. `GET /outbound/eligible` returns the
 *    ineligible list in full and that is the more useful half of the payload — a funnel
 *    that reports only its survivors cannot be checked for being too wide, and too wide
 *    is the only way cold outreach fails badly. So the blocked companies are listed
 *    with the reason each check gave, and companies blocked for more than one
 *    independent reason are called out as such.
 *
 * 2. THE REVIEW QUEUE, INCLUDING THE PART NO REVIEWER MAY ACT ON.
 *    `rejected_unverifiable` drafts never appear in the default listing — the router
 *    serves them only when asked for by name, so nobody can approve one by working down
 *    the page. This panel keeps that separation: the audit tab is reachable, labelled
 *    as an audit trail, and carries no approve control at all.
 *
 * The sweep re-runs the gate, the validator and the memo's cheque calculation for every
 * company, so it is deliberately on a button rather than on page load. A ranked list
 * that will not paint until thirteen gate evaluations finish is a worse page.
 */

import { useCallback, useEffect, useState } from "react";
import {
  decideDraft,
  getEligible,
  getOutboundQueue,
  TIMEOUT,
  type DraftStatus,
  type EligibilityVerdict,
  type EligibleResponse,
  type OutboundDraft,
} from "@/lib/api";
import { Busy, EmptyState, ErrorNote, Panel } from "@/components/ui";

const TABS: { status: DraftStatus; label: string; hint: string }[] = [
  {
    status: "queued",
    label: "Queued",
    hint: "Awaiting a human. Approving records a willingness to send; nothing here sends.",
  },
  {
    status: "approved",
    label: "Approved",
    hint: "A person accepted responsibility for sending these. The send happens outside this system.",
  },
  { status: "rejected", label: "Rejected", hint: "A human declined these." },
  {
    status: "rejected_unverifiable",
    label: "Rejected — unverifiable",
    hint: "Discarded by the verifier before any human saw them. This tab is an audit trail: there is no approve control on it, and these drafts are not in the queue above.",
  },
];

function VerdictRow({ v }: { v: EligibilityVerdict }) {
  const failed = v.checks.filter((c) => !c.passed);
  return (
    <li className="border-b border-[color:var(--rule)] py-2.5 last:border-b-0">
      <div className="flex flex-wrap items-baseline gap-2">
        <span className="mono text-[14px] text-[color:var(--figure)]">{v.name}</span>
        {failed.length > 1 && (
          <span className="meta border border-[color:var(--figure)] px-1.5 py-0.5 text-[color:var(--figure)]">
            {failed.length} independent reasons
          </span>
        )}
      </div>
      <ul className="mt-1 space-y-1">
        {failed.map((c) => (
          <li key={c.name} className="caption max-w-none text-[color:var(--muted)]">
            <span className="mono text-[color:var(--figure)]">
              blocked: {c.name.replace(/_/g, " ")}
            </span>{" "}
            — {c.detail}
          </li>
        ))}
      </ul>
    </li>
  );
}

export default function OutreachQueue() {
  const [asOf, setAsOf] = useState("");
  const [sweep, setSweep] = useState<EligibleResponse | null>(null);
  const [sweeping, setSweeping] = useState(false);
  const [sweepError, setSweepError] = useState<string | null>(null);

  const [tab, setTab] = useState<DraftStatus>("queued");
  const [items, setItems] = useState<OutboundDraft[] | null>(null);
  /** Which tab `items` was loaded for. Anything else means a load is in flight. */
  const [loadedFor, setLoadedFor] = useState<DraftStatus | null>(null);
  const [queueError, setQueueError] = useState<string | null>(null);
  const [by, setBy] = useState("");
  const [busyDraft, setBusyDraft] = useState<string | null>(null);
  const [decideError, setDecideError] = useState<string | null>(null);
  /** Bumped to re-read the queue — after a disposition, or on an explicit retry. */
  const [reloadKey, setReloadKey] = useState(0);

  // Inline async IIFE, matching the rest of this app's effects: nothing in the effect
  // body runs synchronously, and a response for a tab the reader has already left is
  // discarded instead of being written over the tab they are now looking at.
  useEffect(() => {
    let live = true;
    void (async () => {
      const r = await getOutboundQueue(tab);
      if (!live) return;
      if (r.ok) {
        setItems(r.data.items);
        setQueueError(null);
      } else {
        // NOT an empty array. "The queue is empty" and "the queue could not be read"
        // are different claims, and this panel's empty state asserts the first one in
        // so many words — rendering it after a failed read would have the page tell
        // the reader that a network error is the system's considered answer.
        setItems(null);
        setQueueError(`GET /outbound/queue?status=${tab}: ${r.error}`);
      }
      setLoadedFor(tab);
    })();
    return () => {
      live = false;
    };
  }, [tab, reloadKey]);

  const runSweep = useCallback(async () => {
    setSweeping(true);
    setSweepError(null);
    const r = await getEligible({ asOf: asOf || null });
    if (r.ok) setSweep(r.data);
    else {
      setSweep(null);
      setSweepError(r.error);
    }
    setSweeping(false);
  }, [asOf]);

  const decide = useCallback(
    async (draftId: string, decision: "approve" | "reject") => {
      if (!by.trim()) {
        setDecideError("A disposition needs a person on it — enter who is deciding.");
        return;
      }
      setDecideError(null);
      setBusyDraft(draftId);
      const r = await decideDraft(draftId, decision, by.trim());
      setBusyDraft(null);
      if (!r.ok) setDecideError(r.error);
      else setReloadKey((k) => k + 1);
    },
    [by],
  );

  const total = sweep ? sweep.eligible.length + sweep.ineligible.length : 0;
  const activeTab = TABS.find((t) => t.status === tab)!;

  return (
    <Panel
      id="outbound"
      title="Outbound — who may be contacted at all"
      subtitle="Eligibility is computed, never configured. There is no threshold in this feature that anyone typed, and no control on this page widens it."
      className="scroll-mt-32"
    >
      {/* ------------------------------------------------------- the sweep */}
      <div className="flex flex-wrap items-end gap-3">
        <label className="meta text-[color:var(--muted)]">
          Evaluated as of
          <input
            type="date"
            value={asOf}
            onChange={(e) => setAsOf(e.target.value)}
            className="mono ml-2 border border-[color:var(--rule)] bg-transparent px-2 py-1 text-[13px] normal-case text-[color:var(--figure)]"
          />
        </label>
        <button
          type="button"
          onClick={() => void runSweep()}
          disabled={sweeping}
          className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)] disabled:opacity-50"
        >
          {sweeping ? "SWEEPING…" : "RUN THE ELIGIBILITY SWEEP"}
        </button>
        <p className="caption max-w-[56ch] text-[color:var(--muted)]">
          On a button, not on page load: the sweep re-runs the decision gate, the claim
          validator, the integrity flags and the memo&apos;s own cheque calculation once
          per company. Blank cutoff means today.
        </p>
      </div>

      {sweeping && (
        <Busy
          className="mt-3"
          budgetMs={TIMEOUT.llm}
          label="Re-running every decision the system already made, once per company"
        />
      )}

      {sweepError && (
        <div className="mt-3">
          <ErrorNote message={`The eligibility sweep failed (${sweepError}).`} onRetry={() => void runSweep()} />
        </div>
      )}

      {sweep && (
        <div className="mt-4 space-y-3">
          <div className="stub">
            <div>
              <div className="meta text-[color:var(--muted)]">Eligible</div>
              <div className="font-[family-name:var(--font-instrument-serif)] text-[34px] leading-none">
                {sweep.eligible.length}
                <span className="text-[color:var(--muted)]"> / {total}</span>
              </div>
            </div>
            <div>
              <div className="meta text-[color:var(--muted)]">Blocked</div>
              <div className="font-[family-name:var(--font-instrument-serif)] text-[34px] leading-none">
                {sweep.ineligible.length}
              </div>
            </div>
            <div>
              <div className="meta text-[color:var(--muted)]">
                Blocked for 2+ independent reasons
              </div>
              <div className="font-[family-name:var(--font-instrument-serif)] text-[34px] leading-none">
                {sweep.ineligible.filter((v) => v.blocked_by.length > 1).length}
              </div>
            </div>
            <div>
              <div className="meta text-[color:var(--muted)]">VC profile screened</div>
              <div className="mono mt-2 text-[14px]">
                {sweep.profile_active ? "yes — red lines applied" : "no session"}
              </div>
            </div>
          </div>

          <p className="caption max-w-none text-[color:var(--muted)]">{sweep.rule}</p>

          {sweep.eligible.length > 0 && (
            <div className="border border-[color:var(--accent)] px-4 py-3">
              <div className="meta text-[color:var(--accent)]">
                Eligible — open the company to draft
              </div>
              <ul className="mt-1.5 space-y-0.5">
                {sweep.eligible.map((v) => (
                  <li key={v.company_id} className="mono text-[14px]">
                    {v.name}{" "}
                    <span className="text-[color:var(--muted)]">
                      ({v.checks.length} checks, all passed)
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}

          <details className="border border-[color:var(--rule)]" open>
            <summary className="meta cursor-pointer px-4 py-2.5 text-[color:var(--accent)]">
              {sweep.ineligible.length} blocked — every refusal, with the check that made it
            </summary>
            <ul className="border-t border-[color:var(--rule)] px-4">
              {sweep.ineligible.map((v) => (
                <VerdictRow key={v.company_id} v={v} />
              ))}
            </ul>
          </details>
        </div>
      )}

      {/* ------------------------------------------------------- the queue */}
      <div className="mt-6 border-t border-[color:var(--rule)] pt-4">
        <div className="flex flex-wrap items-center gap-1">
          {TABS.map((t) => (
            <button
              key={t.status}
              type="button"
              onClick={() => setTab(t.status)}
              aria-current={t.status === tab ? "true" : undefined}
              className="meta border-b-2 px-2.5 py-1.5"
              style={{
                color: t.status === tab ? "var(--accent)" : "var(--muted)",
                borderBottomColor: t.status === tab ? "var(--accent)" : "transparent",
              }}
            >
              {t.label}
            </button>
          ))}
        </div>
        <p className="caption mt-1.5 max-w-none text-[color:var(--muted)]">
          {activeTab.hint}
        </p>

        {queueError && (
          <div className="mt-3">
            <ErrorNote message={queueError} onRetry={() => setReloadKey((k) => k + 1)} />
          </div>
        )}

        {/* The error above already says what happened; a failed read renders nothing
            further, so the empty state never speaks for a queue nobody could read. */}
        {queueError ? null : items === null || loadedFor !== tab ? (
          <Busy className="mt-3" budgetMs={TIMEOUT.read} label="Reading the review queue" />
        ) : items.length === 0 ? (
          <div className="mt-3">
            <EmptyState title={`No drafts with status "${tab}".`}>
              An empty queue is an answer. On this corpus almost every company is blocked
              before a draft is ever generated, so an empty queue is the expected state
              rather than a failure to load.
            </EmptyState>
          </div>
        ) : (
          <>
            {tab === "queued" && (
              <div className="mt-3 flex flex-wrap gap-2">
                <input
                  value={by}
                  onChange={(e) => setBy(e.target.value)}
                  placeholder="who is deciding (required for approve/reject)"
                  aria-label="Who is deciding"
                  className="mono min-w-[260px] flex-1 border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[13px]"
                />
              </div>
            )}
            {decideError && (
              <div className="mt-2">
                <ErrorNote message={decideError} />
              </div>
            )}
            <ul className="mt-3 space-y-3">
              {items.map((d) => (
                <li key={d.draft_id} className="border border-[color:var(--rule)] p-4">
                  <div className="flex flex-wrap items-baseline justify-between gap-2">
                    <span className="font-[family-name:var(--font-instrument-serif)] text-[22px]">
                      {d.company_name ?? d.company_id}
                    </span>
                    <span className="meta text-[color:var(--muted)]">
                      {d.status} · created {d.created_at.slice(0, 10)}
                      {d.decided_by ? ` · by ${d.decided_by}` : ""}
                    </span>
                  </div>
                  {d.subject && (
                    <div className="mono mt-1 text-[13px] text-[color:var(--figure)]">
                      {d.subject}
                    </div>
                  )}
                  {d.status === "rejected_unverifiable" ? (
                    <>
                      <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
                        Why it was discarded:
                      </p>
                      <p className="mt-1 max-w-[80ch] text-[15px] leading-relaxed text-[color:var(--figure)]">
                        {d.rejection_reason ?? "no reason recorded"}
                      </p>
                      <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
                        Kept for the record only. This text was never offered to a
                        reviewer and cannot be approved from anywhere in this
                        application.
                      </p>
                    </>
                  ) : (
                    d.body && (
                      <pre className="mono mt-2 max-h-[220px] overflow-auto border border-[color:var(--rule)] bg-[color:var(--ink-09)] p-3 text-[12px] leading-relaxed whitespace-pre-wrap">
                        {d.body}
                      </pre>
                    )
                  )}
                  {d.status === "queued" && (
                    <div className="mt-3 flex flex-wrap gap-2">
                      <button
                        type="button"
                        onClick={() => void decide(d.draft_id, "approve")}
                        disabled={busyDraft === d.draft_id}
                        className="meta border border-[color:var(--accent)] px-3 py-1.5 text-[color:var(--accent)] disabled:opacity-50"
                      >
                        APPROVE FOR A HUMAN TO SEND
                      </button>
                      <button
                        type="button"
                        onClick={() => void decide(d.draft_id, "reject")}
                        disabled={busyDraft === d.draft_id}
                        className="meta border border-[color:var(--rule)] px-3 py-1.5 text-[color:var(--muted)] disabled:opacity-50"
                      >
                        REJECT
                      </button>
                    </div>
                  )}
                </li>
              ))}
            </ul>
          </>
        )}
      </div>
    </Panel>
  );
}
