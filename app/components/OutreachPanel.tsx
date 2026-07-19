"use client";

/**
 * Outreach for ONE company: the eligibility gate, the grounded draft, the compose box,
 * and the handoff to a human.
 *
 * ---------------------------------------------------------------------------
 * WHY THERE IS NO SEND BUTTON.
 *
 * The whiteboard asked for "a text box that sends a message to LinkedIn". LinkedIn's
 * messaging API is gated behind its partner programme, and automating messages through
 * the site is a violation of its User Agreement. There is therefore no programmatic
 * send available to build, and a button labelled "send" that quietly did something else
 * would be worse than no button.
 *
 * What this panel does instead: it fills a compose box with a draft whose every factual
 * token is already tied to a quoted span, lets the VC edit it, and hands it off — to
 * the clipboard, and to LinkedIn's own interface — for a person to paste and send. The
 * label saying so is not fine print; it is the second line of the panel.
 *
 * This also happens to be the backend's position, arrived at independently:
 * `sourcing/outreach.py` has no email provider in it, `api/routers/outbound.py` has no
 * send endpoint, and `approve()` records that a human is willing to send and does
 * nothing else. "A queue that a scheduler can drain is a queue that will eventually be
 * drained by a scheduler."
 *
 * ---------------------------------------------------------------------------
 * PRIVACY.
 *
 * The founder's contact details are never surfaced to the VC and never stored by this
 * screen. `recipient_email` is null on every draft the backend creates — the reviewer
 * supplies an address outside the system — and `OutboundDraft` in lib/api.ts does not
 * declare the field, so no component here can render or collect it. Routing via
 * LinkedIn is the whole point: the VC contacts a person through a platform the person
 * controls, and never handles their personal address. A "recipient email" input on this
 * panel would be a convenience that dismantles the property it sits next to.
 *
 * ---------------------------------------------------------------------------
 * THE GATE IS THE BACKEND'S.
 *
 * Nothing here decides who may be contacted. `GET /outbound/eligible` re-runs the
 * decision gate, the claim validator, the integrity flags, the profile's red lines and
 * the memo's own cheque calculation, and a company passes only when all of them
 * independently came out in its favour. When it says no, this panel shows every check
 * and the reason each failing one gave, rather than hiding the button. The refusal is
 * the more interesting half: on the seeded corpus twelve of thirteen companies are
 * blocked, and two of those are blocked for two independent reasons.
 */

import { useCallback, useEffect, useState } from "react";
import {
  decideDraft,
  getEligible,
  postDraft,
  TIMEOUT,
  type EligibilityVerdict,
  type OutboundDraft,
} from "@/lib/api";
import { Busy, EmptyState, ErrorNote, Panel, SourceRef } from "@/components/ui";

const CHECK_LABEL: Record<string, string> = {
  not_suppressed: "Suppression list",
  gate_proceed: "Decision gate",
  no_contradicted_claims: "Claim validator",
  evidence_integrity: "Evidence integrity",
  red_lines: "Profile red lines",
  recommendation_has_amount: "Investment recommendation",
};

const label = (name: string) => CHECK_LABEL[name] ?? name.replace(/_/g, " ");

/** Passed / blocked told by glyph, label and rule-work — never by hue alone. */
function CheckRow({ check }: { check: EligibilityVerdict["checks"][number] }) {
  return (
    <li
      className="border-l-2 py-2 pl-3"
      style={{
        borderColor: check.passed ? "var(--rule)" : "var(--figure)",
        borderLeftStyle: check.passed ? "solid" : "dashed",
      }}
    >
      <div className="meta" style={{ color: check.passed ? "var(--muted)" : "var(--figure)" }}>
        <span aria-hidden>{check.passed ? "✓" : "✕"}</span>{" "}
        {check.passed ? "passed" : "blocked"} · {label(check.name)}
      </div>
      <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">{check.detail}</p>
    </li>
  );
}

/** Short, blunt reason strings for the header — "blocked: gate returned no_call". */
function blockedSummary(v: EligibilityVerdict): string[] {
  return v.checks
    .filter((c) => !c.passed)
    .map((c) => {
      if (c.name === "gate_proceed") {
        const m = c.detail.match(/returned (\w+)/);
        return `blocked: gate returned ${m ? m[1] : "not proceed"}`;
      }
      if (c.name === "no_contradicted_claims") {
        const m = c.detail.match(/^(\d+) deck claim/);
        return `blocked: ${m ? m[1] : "some"} contradicted claim${m && m[1] === "1" ? "" : "s"}`;
      }
      if (c.name === "evidence_integrity") {
        const m = c.detail.match(/^(\d+) event/);
        return `blocked: ${m ? m[1] : "some"} event(s) carry an impeaching integrity flag`;
      }
      if (c.name === "recommendation_has_amount") {
        return "blocked: the recommendation refused rather than sizing a cheque";
      }
      if (c.name === "red_lines") return "blocked: an unresolved stated red line";
      if (c.name === "not_suppressed") return "blocked: on the suppression list";
      return `blocked: ${label(c.name)}`;
    });
}

export default function OutreachPanel({
  companyId,
  companyName,
}: {
  companyId: string;
  companyName: string;
}) {
  /** Empty string = "now", which is what the ranked list on the pipeline uses.
   *  Eligibility is as-of dependent because the gate's conformal interval is, so the
   *  cutoff is a visible control rather than a hidden default. */
  const [asOf, setAsOf] = useState("");
  const [verdict, setVerdict] = useState<EligibilityVerdict | null>(null);
  const [checking, setChecking] = useState(true);
  const [checkError, setCheckError] = useState<string | null>(null);

  const [draft, setDraft] = useState<OutboundDraft | null>(null);
  const [drafting, setDrafting] = useState(false);
  const [draftError, setDraftError] = useState<{ message: string; unverifiable: boolean } | null>(
    null,
  );

  const [subject, setSubject] = useState("");
  const [bodyText, setBodyText] = useState("");
  const [by, setBy] = useState("");
  const [note, setNote] = useState("");
  const [deciding, setDeciding] = useState(false);
  const [decideError, setDecideError] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [showChecks, setShowChecks] = useState(false);

  /**
   * The request itself. Every setState here lands AFTER the await, which is what keeps
   * it legal to call straight from an effect — a synchronous setState in an effect body
   * cascades a render before the browser has painted anything.
   */
  const fetchVerdict = useCallback(async () => {
    const r = await getEligible({ companyId, asOf: asOf || null });
    if (!r.ok) {
      setVerdict(null);
      setCheckError(r.error);
    } else {
      const all = [...r.data.eligible, ...r.data.ineligible];
      setVerdict(all[0] ?? null);
      setCheckError(
        all.length ? null : "the eligibility route returned no verdict for this company",
      );
    }
    setChecking(false);
  }, [companyId, asOf]);

  // Re-runs on mount and whenever the cutoff moves. A `type="date"` input only fires
  // on a complete date or on clear, so this cannot fire mid-typing. The work is an
  // inline async IIFE — the same shape the rest of this app's effects use — so nothing
  // in the effect body runs synchronously and a stale response is discarded rather
  // than written over a newer one.
  useEffect(() => {
    let live = true;
    void (async () => {
      const r = await getEligible({ companyId, asOf: asOf || null });
      if (!live) return;
      if (!r.ok) {
        setVerdict(null);
        setCheckError(r.error);
      } else {
        const all = [...r.data.eligible, ...r.data.ineligible];
        setVerdict(all[0] ?? null);
        setCheckError(
          all.length ? null : "the eligibility route returned no verdict for this company",
        );
      }
      setChecking(false);
    })();
    return () => {
      live = false;
    };
  }, [companyId, asOf]);

  /** The explicit control. Shows the in-flight state, which the effect path does not
   *  need to because it starts in it. */
  const check = useCallback(() => {
    setChecking(true);
    setCheckError(null);
    void fetchVerdict();
  }, [fetchVerdict]);

  const generate = useCallback(async () => {
    setDrafting(true);
    setDraftError(null);
    try {
      const r = await postDraft(companyId, asOf || null);
      if (r.ok) {
        setDraft(r.data);
        setSubject(r.data.subject ?? "");
        setBodyText(r.data.body ?? "");
      } else {
        setDraft(null);
        setDraftError({ message: r.error, unverifiable: Boolean(r.unverifiable) });
      }
    } finally {
      setDrafting(false);
    }
  }, [companyId, asOf]);

  const composed = subject.trim()
    ? `${subject.trim()}\n\n${bodyText}`
    : bodyText;

  const copy = useCallback(
    async (text: string, what: string) => {
      try {
        await navigator.clipboard.writeText(text);
        setCopied(what);
        setTimeout(() => setCopied(null), 2500);
      } catch {
        setCopied("failed");
      }
    },
    [],
  );

  const openLinkedIn = useCallback(async () => {
    await copy(composed, "draft (LinkedIn opened in a new tab)");
    // LinkedIn exposes no URL that can prefill a message — there is no such parameter
    // and inventing one would produce a dead link. People search is the honest landing
    // point: it gets the reviewer to the right profile, and the paste is theirs.
    const q = [draft?.recipient_name, companyName].filter(Boolean).join(" ");
    window.open(
      `https://www.linkedin.com/search/results/people/?keywords=${encodeURIComponent(q)}`,
      "_blank",
      "noopener,noreferrer",
    );
  }, [composed, copy, draft?.recipient_name, companyName]);

  const decide = useCallback(
    async (decision: "approve" | "reject") => {
      if (!draft) return;
      if (!by.trim()) {
        setDecideError("A disposition needs a person on it — enter who is deciding.");
        return;
      }
      setDeciding(true);
      setDecideError(null);
      try {
        const r = await decideDraft(draft.draft_id, decision, by.trim(), note);
        if (r.ok) setDraft(r.data);
        else setDecideError(r.error);
      } finally {
        setDeciding(false);
      }
    },
    [draft, by, note],
  );

  const eligible = verdict?.eligible === true;
  const blocked = verdict ? blockedSummary(verdict) : [];

  return (
    <Panel
      id="outreach"
      title="Outbound — grounded draft, human send"
      subtitle="Only companies the system independently decided in favour of on every count are drafted for. Nothing in this application sends a message."
      className="scroll-mt-32"
    >
      {/* ---------------------------------------------------- the as-of control */}
      <div className="flex flex-wrap items-end gap-3 border-b border-[color:var(--rule)] pb-4">
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
          onClick={() => check()}
          disabled={checking}
          className="meta border border-[color:var(--rule)] px-3 py-1.5 text-[color:var(--muted)] disabled:opacity-50"
        >
          {checking ? "CHECKING…" : "RE-CHECK"}
        </button>
        <p className="caption max-w-[52ch] text-[color:var(--muted)]">
          Blank means today. The gate&apos;s conformal interval widens as evidence ages,
          so eligibility genuinely moves with the cutoff — it is a control rather than a
          hidden default.
        </p>
      </div>

      {checking && !verdict && (
        <Busy
          className="mt-4"
          budgetMs={TIMEOUT.llm}
          label="Re-running the gate, the validator, the integrity flags and the memo's cheque calculation for this company"
        />
      )}

      {checkError && (
        <div className="mt-4">
          <ErrorNote
            message={`Eligibility could not be established (${checkError}). Nothing is assumed in either direction — no draft can be generated without a verdict.`}
            onRetry={() => check()}
          />
        </div>
      )}

      {verdict && (
        <>
          {/* ----------------------------------------------- the verdict header */}
          <div
            className="mt-4 border px-4 py-3"
            style={{ borderColor: eligible ? "var(--accent)" : "var(--rule)" }}
          >
            <div
              className="meta"
              style={{ color: eligible ? "var(--accent)" : "var(--figure)" }}
            >
              {eligible
                ? "✓ Eligible — every check came out in this company's favour"
                : `✕ Not eligible — ${blocked.length} independent ${
                    blocked.length === 1 ? "reason" : "reasons"
                  }`}
            </div>
            {!eligible && (
              <ul className="mt-2 space-y-1">
                {blocked.map((b) => (
                  <li key={b} className="mono text-[13px] text-[color:var(--figure)]">
                    {b}
                  </li>
                ))}
              </ul>
            )}
            <button
              type="button"
              onClick={() => setShowChecks((s) => !s)}
              className="meta mt-2 text-[color:var(--accent)]"
            >
              {showChecks ? "HIDE" : "SHOW"} ALL {verdict.checks.length} CHECKS
            </button>
            {showChecks && (
              <ul className="mt-2 space-y-1">
                {verdict.checks.map((c) => (
                  <CheckRow key={c.name} check={c} />
                ))}
              </ul>
            )}
          </div>

          {!eligible && (
            <div className="mt-4">
              <EmptyState title="No draft will be generated for this company.">
                The button is absent because the gate said no, and the gate is not
                overridable from this screen — there is no threshold in the feature that
                anyone typed. The reasons above are each a decision the system already
                made on its own terms, re-run rather than remembered.
              </EmptyState>
            </div>
          )}
        </>
      )}

      {/* -------------------------------------------------------- generate */}
      {eligible && !draft && (
        <div className="mt-4">
          <button
            type="button"
            onClick={() => void generate()}
            disabled={drafting}
            className="meta border border-[color:var(--accent)] bg-[color:var(--accent)] px-5 py-3 text-[color:var(--paper)] disabled:opacity-60"
          >
            {drafting ? "DRAFTING…" : "DRAFT FROM THE EVIDENCE TRACE"}
          </button>
          {drafting && (
            <Busy
              className="mt-3"
              budgetMs={TIMEOUT.llm}
              label="Generating, then verifying every specific term against the quoted span of the event it cites"
              stages={[
                "collecting citable evidence as opaque refs — the model is never shown a URL…",
                "generating observations, one evidence item per line…",
                "verifying: no links, no invented citations, every specific token grounded…",
              ]}
            />
          )}
          <p className="caption mt-3 max-w-none text-[color:var(--muted)]">
            The model is shown quoted spans keyed by opaque ids and no URLs at all, so a
            fabricated link has no path into the output. Every line is then checked
            token-by-token against the span of the event it cites, and a draft that
            cannot be grounded is discarded rather than shown to you with a warning on
            it.
          </p>
        </div>
      )}

      {/* --------------------------- the anti-hallucination rejection, when it fires */}
      {draftError && (
        <div
          className="mt-4 border px-4 py-3"
          style={{ borderColor: draftError.unverifiable ? "var(--accent)" : "var(--rule)" }}
        >
          <div className="meta text-[color:var(--accent)]">
            {draftError.unverifiable
              ? "Draft rejected as unverifiable — recorded, discarded, never queued"
              : "The draft could not be generated"}
          </div>
          <p className="mt-1.5 max-w-[80ch] text-[15px] leading-relaxed text-[color:var(--figure)]">
            {draftError.message}
          </p>
          {draftError.unverifiable && (
            <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
              This is the system working. The generated text asserted something it could
              not tie to a stored span, so it was recorded with status{" "}
              <code className="mono">rejected_unverifiable</code> and thrown away. It is
              not in the review queue and no human will be offered it — an unverifiable
              claim about a stranger is not a draft with a warning on it, it is a draft
              that does not exist.
            </p>
          )}
          <button
            type="button"
            onClick={() => void generate()}
            disabled={drafting}
            className="meta mt-3 border border-[color:var(--rule)] px-3 py-1.5 text-[color:var(--muted)] disabled:opacity-50"
          >
            TRY AGAIN
          </button>
        </div>
      )}

      {/* ------------------------------------------------------- the compose box */}
      {draft && (
        <div className="mt-4 space-y-4">
          <div className="border border-[color:var(--figure)] px-4 py-3">
            <div className="meta text-[color:var(--figure)]">Manual send — and why</div>
            <p className="mt-1 max-w-[80ch] text-[15px] leading-relaxed text-[color:var(--figure)]">
              This application cannot and does not send this message.
            </p>
            <p className="caption mt-1.5 max-w-none text-[color:var(--muted)]">
              LinkedIn&apos;s messaging API is restricted to its partner programme, and
              automating messages through the site violates its User Agreement — so there
              is no programmatic send to build here and none is faked. Edit the draft
              below, then copy it and paste it into LinkedIn yourself. Approving records
              that you are willing to send; it does not send. The founder&apos;s email
              address is never requested, shown or stored by this system, which is the
              reason outreach is routed through a platform they control rather than
              through their inbox.
            </p>
          </div>

          <div>
            <label htmlFor="ob-subject" className="meta text-[color:var(--muted)]">
              Subject — lifted from the evidence, editable
            </label>
            <input
              id="ob-subject"
              value={subject}
              onChange={(e) => setSubject(e.target.value)}
              className="mono mt-1 w-full border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[14px]"
            />
          </div>

          <div>
            <label htmlFor="ob-body" className="meta text-[color:var(--muted)]">
              Message to {draft.recipient_name ?? "the founder"} — edit freely
            </label>
            <textarea
              id="ob-body"
              value={bodyText}
              onChange={(e) => setBodyText(e.target.value)}
              rows={16}
              className="mono mt-1 w-full border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[13px] leading-relaxed"
            />
            <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
              Your edits are yours and are not re-verified. Every claim in the text as
              generated resolves to a quoted span below; anything you add does not.
            </p>
          </div>

          {/* ------------------------------------------------------ the handoff */}
          <div className="flex flex-wrap items-center gap-2">
            <button
              type="button"
              onClick={() => void openLinkedIn()}
              className="meta border border-[color:var(--accent)] bg-[color:var(--accent)] px-4 py-2.5 text-[color:var(--paper)]"
            >
              COPY &amp; OPEN LINKEDIN
            </button>
            <button
              type="button"
              onClick={() => void copy(composed, "draft")}
              className="meta border border-[color:var(--accent)] px-4 py-2.5 text-[color:var(--accent)]"
            >
              COPY TO CLIPBOARD
            </button>
            {copied && (
              <span className="meta text-[color:var(--muted)]" role="status">
                {copied === "failed"
                  ? "the clipboard was refused by the browser — select the text and copy it"
                  : `copied ${copied}`}
              </span>
            )}
          </div>
          <p className="caption max-w-none text-[color:var(--muted)]">
            &ldquo;Copy &amp; open LinkedIn&rdquo; puts the text on your clipboard and
            opens LinkedIn&apos;s people search for{" "}
            {draft.recipient_name ?? "this founder"}. It cannot prefill their compose
            box: LinkedIn has no URL parameter for a message body, and a link pretending
            to carry one would simply be dead.
          </p>

          {/* ---------------------------------------------------- the receipts */}
          {draft.citations && draft.citations.length > 0 && (
            <details className="border border-[color:var(--rule)]">
              <summary className="meta cursor-pointer px-4 py-2.5 text-[color:var(--accent)]">
                {draft.citations.length} resolved {draft.citations.length === 1 ? "citation" : "citations"} — every link attached by code from a stored event
              </summary>
              <ul className="space-y-3 border-t border-[color:var(--rule)] p-4">
                {draft.citations.map((cit) => (
                  <li key={cit.n}>
                    <div className="meta text-[color:var(--muted)]">
                      [{cit.n}] {cit.kind.replace(/_/g, " ")} · {cit.source} ·{" "}
                      {cit.observed_at.slice(0, 10)}
                    </div>
                    <blockquote className="evidence-span my-1.5 px-4 py-3">
                      “{cit.evidence_span}”
                    </blockquote>
                    <SourceRef url={cit.source_url} />
                  </li>
                ))}
              </ul>
            </details>
          )}

          {/* ------------------------------------------------- the disposition */}
          <div className="border border-[color:var(--rule)] px-4 py-3">
            <div className="meta text-[color:var(--muted)]">
              Disposition — status: <span className="text-[color:var(--figure)]">{draft.status}</span>
              {draft.decided_by ? ` · by ${draft.decided_by}` : ""}
            </div>
            {draft.status === "queued" ? (
              <>
                <div className="mt-2 flex flex-wrap gap-2">
                  <input
                    value={by}
                    onChange={(e) => setBy(e.target.value)}
                    placeholder="who is deciding (required)"
                    aria-label="Who is deciding"
                    className="mono min-w-[220px] flex-1 border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[13px]"
                  />
                  <input
                    value={note}
                    onChange={(e) => setNote(e.target.value)}
                    placeholder="note (optional)"
                    aria-label="Note"
                    className="mono min-w-[220px] flex-1 border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[13px]"
                  />
                </div>
                <div className="mt-2 flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={() => void decide("approve")}
                    disabled={deciding}
                    className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)] disabled:opacity-50"
                  >
                    APPROVE FOR A HUMAN TO SEND
                  </button>
                  <button
                    type="button"
                    onClick={() => void decide("reject")}
                    disabled={deciding}
                    className="meta border border-[color:var(--rule)] px-4 py-2 text-[color:var(--muted)] disabled:opacity-50"
                  >
                    REJECT
                  </button>
                </div>
                <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
                  Approving marks the draft sendable by a person and writes who decided
                  it. It does not send, and there is no endpoint in the backend that
                  does. A disposition is recorded once and cannot be revised.
                </p>
              </>
            ) : (
              <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
                Recorded {draft.decided_at ? `at ${draft.decided_at.slice(0, 19).replace("T", " ")}` : ""}.
                A disposition is written once — this draft can no longer be changed.
                {draft.rejection_reason ? ` Note: ${draft.rejection_reason}` : ""}
              </p>
            )}
            {decideError && (
              <div className="mt-2">
                <ErrorNote message={decideError} />
              </div>
            )}
          </div>
        </div>
      )}
    </Panel>
  );
}
