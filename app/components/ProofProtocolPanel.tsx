"use client";

/**
 * The Proof Protocol — the centerpiece beat, so it gets real estate.
 *
 * For a cold-start company there is no public evidence to score, so the system
 * MANUFACTURES evidence: it issues a challenge with two things deliberately planted
 * in it, and grades the behavior rather than the artifact.
 *
 * The panel's job is to show WHAT WAS PLANTED before showing how they responded.
 * A grading result without the plant visible is just an opinion.
 */

import { useState } from "react";
import { gradeProof, issueProof, TIMEOUT } from "@/lib/api";
import type { ProofBehavior, ProofProtocol } from "@/lib/types";
import { Busy, ErrorNote, EvidenceSpan } from "./ui";

const RESULT: Record<
  ProofBehavior["result"],
  { color: string; icon: string; label: string }
> = {
  pass: { color: "var(--accent)", icon: "✓", label: "PASS" },
  partial: { color: "var(--figure)", icon: "◐", label: "PARTIAL" },
  fail: { color: "var(--figure)", icon: "✕", label: "FAIL" },
};

function Planted({
  kind,
  text,
  color,
}: {
  kind: string;
  text: string;
  color: string;
}) {
  const body = text.replace(/^PLANTED:\s*/, "");
  return (
    <div className="border-2 border-dashed bg-[color:var(--ink-09)] p-4" style={{ borderColor: color }}>
      <div className="flex items-center gap-2">
        <span
          className="px-2 py-0.5 text-[11px] font-medium text-black uppercase"
          style={{ background: color }}
        >
          Planted
        </span>
        <span className="meta text-[color:var(--muted)]">
          {kind}
        </span>
      </div>
      <p className="mt-2.5 text-[15px] leading-[1.55] text-[color:var(--figure)]">{body}</p>
    </div>
  );
}

/**
 * Generating a challenge and grading a submission are real LLM round trips that take
 * seconds, which makes them the two controls on the whole dashboard most likely to be
 * mistaken for a hang. They get the most explicit progress on the page: a bar bounded by
 * the same timeout the request uses, and a narration of what is actually happening
 * rather than a generic spinner.
 *
 * The stages are honest descriptions of the server's work, not a fake stepper — the bar
 * eases asymptotically toward the timeout and the caption names the phase it is in.
 */
const ISSUE_STAGES = [
  "reading the deck's central claim…",
  "generating a challenge around it…",
  "planting one ambiguous requirement and one bad constraint…",
  "still working — this is a live model call, not a cached fixture…",
];

const GRADE_STAGES = [
  "reading the submitted artifact and trace…",
  "checking behaviour against each grading axis…",
  "deciding whether the planted constraint was pushed back on…",
  "still working — grading is a live model call…",
];

export default function ProofProtocolPanel({
  companyId,
  initial,
}: {
  companyId: string;
  initial: ProofProtocol;
}) {
  const [pp, setPp] = useState<ProofProtocol>(initial);
  const [busy, setBusy] = useState<null | "issue" | "grade">(null);
  const [error, setError] = useState<string | null>(null);
  const passed = pp.behaviors.filter((b) => b.result === "pass").length;

  const runIssue = async () => {
    setBusy("issue");
    setError(null);
    try {
      const r = await issueProof(companyId);
      if (r.ok) setPp(r.data);
      else setError(`Could not issue a new challenge: ${r.error}`);
    } finally {
      // Always. A several-second LLM call that fails must still hand the button back.
      setBusy(null);
    }
  };

  const runGrade = async () => {
    if (!pp.artifact_url) return;
    setBusy("grade");
    setError(null);
    try {
      const r = await gradeProof(companyId, pp.challenge_id, {
        artifact_url: pp.artifact_url,
        trace: "",
      });
      if (r.ok) setPp(r.data);
      else setError(`Could not grade the submission: ${r.error}`);
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="overflow-hidden border-2 bg-[color:var(--ground)]" style={{ borderColor:"var(--figure)" }}>
      <header
        className="flex flex-wrap items-center justify-between gap-4 px-5 py-4"
        style={{ background: "color-mix(in oklab, var(--figure) 13%, transparent)" }}
      >
        <div>
          <h2 className="text-[20px] font-medium tracking-wide text-[color:var(--figure)] uppercase">
            Proof Protocol · cold start
          </h2>
          <p className="mt-1 max-w-2xl text-[14px] leading-[1.55] text-[color:var(--muted)]">
            This company had no public footprint to score. Rather than penalise the absence,
            the system created evidence: a challenge with two deliberate traps, graded on
            behavior rather than on the artifact.
          </p>
        </div>
        <div className="text-right">
          <div className="meta text-[color:var(--muted)]">
            {pp.behaviors.length ? "Behaviors passed" : "Grading"}
          </div>
          {pp.behaviors.length ? (
            <div className="mono text-[44px] leading-none font-medium text-[color:var(--figure)]">
              {passed}
              <span className="text-[24px] text-[color:var(--muted)]">
                /{pp.behaviors.length}
              </span>
            </div>
          ) : (
            // Issued-but-not-graded is a real state, not a half-loaded one. Showing
            // "0/0" would read as a total failure; "not yet run" is what is true.
            <div className="mono text-[20px] leading-tight text-[color:var(--muted)]">
              not yet run
            </div>
          )}
        </div>
      </header>

      <div className="space-y-5 px-5 py-5">
        {/* ------------------------------------------ the two slow controls */}
        <div className="flex flex-wrap items-center gap-2 border-b border-[color:var(--rule)] pb-4">
          <button
            type="button"
            onClick={() => void runIssue()}
            disabled={busy !== null}
            title={
              busy
                ? "A model call is already running — one at a time"
                : "Generate a fresh challenge from the deck's central claim (live model call, several seconds)"
            }
            className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)] disabled:opacity-50"
          >
            {busy === "issue" ? "GENERATING…" : "↻ REGENERATE CHALLENGE"}
          </button>
          <button
            type="button"
            onClick={() => void runGrade()}
            disabled={busy !== null || !pp.artifact_url}
            title={
              !pp.artifact_url
                ? "No artifact has been submitted yet, so there is nothing to grade"
                : busy
                  ? "A model call is already running — one at a time"
                  : "Grade the submitted artifact on behaviour (live model call, several seconds)"
            }
            className="meta border border-[color:var(--rule)] px-4 py-2 text-[color:var(--muted)] disabled:opacity-50"
          >
            {busy === "grade" ? "GRADING…" : "GRADE SUBMISSION"}
          </button>
          {!pp.artifact_url && busy === null && (
            <span className="meta text-[color:var(--muted)]">
              Grading is disabled: no artifact submitted
            </span>
          )}
        </div>

        {busy === "issue" && (
          <Busy
            budgetMs={TIMEOUT.llm}
            stages={ISSUE_STAGES}
            label={`Generating a challenge — gives up after ${TIMEOUT.llm / 1000}s`}
          />
        )}
        {busy === "grade" && (
          <Busy
            budgetMs={TIMEOUT.llm}
            stages={GRADE_STAGES}
            label={`Grading the submission — gives up after ${TIMEOUT.llm / 1000}s`}
          />
        )}
        {error && <ErrorNote message={error} />}

        <div>
          <h3 className="meta text-[color:var(--muted)]">
            The claim under test
          </h3>
          <p className="mt-1.5 text-[15px] leading-[1.55] text-[color:var(--muted)]">{pp.central_claim}</p>
        </div>

        <div>
          <h3 className="meta text-[color:var(--muted)]">
            Challenge issued
          </h3>
          <blockquote className="evidence-span mt-1.5 px-4 py-3 text-[14px] leading-[1.55] text-[color:var(--figure)]">
            {pp.prompt}
          </blockquote>
          <p className="mono mt-1 font-mono text-[12px] text-[color:var(--muted)]">
            {pp.challenge_id} · issued {pp.issued_at.replace("T", " ").slice(0, 16)}Z
            {pp.responded_at &&
              ` · responded ${pp.responded_at.replace("T", " ").slice(0, 16)}Z`}
          </p>
        </div>

        {/* ------------------------------------------------ what was planted in it */}
        <div>
          <h3 className="meta text-[color:var(--muted)]">
            What was planted in that prompt
          </h3>
          <p className="mt-1 mb-3 text-[14px] text-[color:var(--muted)]">
            Both traps are invisible in the prompt above unless you know they are there.
            That is the point — the grade is what they did about them.
          </p>
          <div className="grid gap-3 md:grid-cols-2">
            <Planted
              kind="Ambiguous requirement"
              text={pp.ambiguous_requirement}
              color="var(--figure)"
            />
            <Planted
              kind="Bad constraint"
              text={pp.planted_bad_constraint}
              color="var(--figure)"
            />
          </div>
        </div>

        {/* ------------------------------------------------------ behavioral grade */}
        <div>
          <h3 className="meta text-[color:var(--muted)]">
            Behavioral grading
          </h3>
          {pp.behaviors.length === 0 && (
            <div className="mt-2 border border-dashed border-[color:var(--rule)] px-4 py-4">
              <p className="text-[14px] leading-[1.55] text-[color:var(--muted)]">
                The challenge has been issued and no response has been graded yet. What
                the grader will look for is fixed in advance and listed here, so the
                criteria cannot be chosen after seeing the answer:
              </p>
              {pp.grading_axes?.length ? (
                <ul className="mt-3 space-y-1.5">
                  {pp.grading_axes.map((a) => (
                    <li
                      key={a}
                      className="mono border border-[color:var(--rule)] px-3 py-2 text-[13px] text-[color:var(--figure)]"
                    >
                      {a.replace(/_/g, " ")}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
                  No grading axes were reported with this challenge.
                </p>
              )}
            </div>
          )}
          <ul className="mt-2 space-y-2">
            {pp.behaviors.map((b) => {
              const r = RESULT[b.result];
              return (
                <li
                  key={b.name}
                  className="border bg-[color:var(--ink-09)] p-4"
                  style={{ borderColor: `color-mix(in oklab, ${r.color} 45%, var(--rule))` }}
                >
                  <div className="flex flex-wrap items-center justify-between gap-3">
                    <h4 className="text-[16px] font-medium text-[color:var(--figure)]">{b.name}</h4>
                    <span
                      className="flex shrink-0 items-center gap-1.5 border px-2.5 py-1 text-[12px] font-medium"
                      style={{
                        color: r.color,
                        borderColor: r.color,
                        background: `color-mix(in oklab, ${r.color} 12%, transparent)`,
                      }}
                    >
                      <span aria-hidden>{r.icon}</span>
                      {r.label}
                    </span>
                  </div>
                  <EvidenceSpan>{b.evidence_span}</EvidenceSpan>
                  <p className="text-[14px] leading-[1.55] text-[color:var(--muted)]">{b.note}</p>
                </li>
              );
            })}
          </ul>
        </div>

        {pp.artifact_url && (
          <a
            href={pp.artifact_url}
            target="_blank"
            rel="noreferrer noopener"
            className="inline-block font-mono text-[13px] text-[var(--accent)] underline decoration-dotted underline-offset-4"
          >
            ↗ {pp.artifact_url}
          </a>
        )}

        <div
          className="border-l-4 px-4 py-3"
          style={{
            borderColor: pp.verdict === "signal" ? "var(--accent)" : "var(--muted)",
            background:
              pp.verdict === "signal"
                ? "color-mix(in oklab, var(--accent) 9%, transparent)"
                : "var(--ground)",
          }}
        >
          <div className="meta text-[color:var(--muted)]">
            Verdict · {pp.verdict.replace("_", " ")}
          </div>
          <p className="mt-1.5 text-[15px] leading-[1.55] text-[color:var(--figure)]">
            {pp.verdict_rationale ||
              "No rationale recorded yet — the verdict is written when the grading runs."}
          </p>
        </div>
      </div>
    </div>
  );
}
