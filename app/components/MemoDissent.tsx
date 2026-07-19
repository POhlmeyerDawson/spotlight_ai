"use client";

/**
 * Memo | Dissent, side by side.
 *
 * The recommendation is locked SERVER-SIDE until the dissent is opened
 * (`api/main.py` nulls it unless `dissent_viewed=true`). We do not work around the
 * lock, we do not pre-fetch the unlocked memo, and we render the locked state
 * honestly rather than hiding the section until it arrives.
 */

import { TIMEOUT } from "@/lib/api";
import { AXIS_KEYS, AXIS_LABEL, type AxisKey } from "@/lib/types";
import type { MemoDissentState } from "@/lib/useMemoDissent";
import { Busy, EmptyState, ErrorNote, Loading } from "./ui";

/**
 * Renders inline citations as monospace chips so every claim visibly carries its id.
 *
 * Two citation dialects arrive here. Fixtures use short human ids (`[e-hx-01]`) which
 * are readable inline and are chipped as-is. The live council emits raw UUID lists
 * (`[evidence: df924ecc-f045-…,20e0d58d-…,3fc86b06-…]`) which ran to ~200 characters and
 * swallowed the sentence they were attached to — three lines of hex in the middle of the
 * one paragraph that states the bear case.
 *
 * A UUID is not information a reader uses, but dropping it would cost the traceability
 * claim. So a UUID block collapses to a count, with the full ids kept in the title for
 * anyone who needs them: the citation is still there, it just stops shouting.
 */
const UUID_RE =
  /[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}/gi;

function Cited({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]+\])/g);
  return (
    <>
      {parts.map((p, i) => {
        if (!/^\[.+\]$/.test(p)) return <span key={i}>{p}</span>;

        const ids = p.match(UUID_RE);
        if (ids) {
          return (
            <code
              key={i}
              title={ids.join("\n")}
              className="mx-0.5 cursor-help bg-[color:var(--ink-09)] px-1.5 py-0.5 font-mono text-[12px] text-[var(--accent)]"
            >
              ▸ {ids.length} {ids.length === 1 ? "event" : "events"}
            </code>
          );
        }
        return (
          <code
            key={i}
            className="mx-0.5 bg-[color:var(--ink-09)] px-1.5 py-0.5 font-mono text-[12px] text-[var(--accent)]"
          >
            {p}
          </code>
        );
      })}
    </>
  );
}

/**
 * Bull/bear spread per axis.
 *
 * Split out because the failure it fixes was a rendering one. The wire carries spreads
 * on 0..1; this component printed `v.toFixed(0)` against a bar sized `v * 2`%, so three
 * genuine disagreements of 0.50, 0.30 and 0.40 rendered as "0", "0", "0" behind three
 * hairline bars — which reads as "the bull and the bear agree perfectly", the exact
 * opposite of the truth. The adapter now normalises to score units, and an axis the
 * payload does not carry is OMITTED rather than defaulted, so "not computed" and "no
 * spread" no longer share a rendering.
 */
function AxisSpreads({ spreads }: { spreads: Partial<Record<AxisKey, number>> }) {
  const missing = AXIS_KEYS.filter((k) => spreads[k] === undefined);

  if (missing.length === AXIS_KEYS.length) {
    return (
      <div>
        <h4 className="meta text-[color:var(--muted)]">Bull/bear spread per axis</h4>
        <p className="mt-1.5 border border-dashed border-[color:var(--figure)] px-3 py-2 text-[13px] leading-[1.55] text-[color:var(--muted)]">
          No spread was computed for this company. That is not the same as the memo and
          the dissent agreeing — it means the comparison was not run, so nothing is drawn
          here rather than three empty bars implying consensus.
        </p>
      </div>
    );
  }

  return (
    <div>
      <h4 className="meta text-[color:var(--muted)]">Bull/bear spread per axis</h4>
      <p className="mt-1 mb-2 text-[13px] leading-[1.5] text-[color:var(--muted)]">
        How far apart the memo and the dissent land on each axis, in score units. A wide
        spread is uncertainty, and it is reported per axis — never pooled.
      </p>
      <ul className="space-y-2">
        {AXIS_KEYS.map((k) => {
          const v = spreads[k];
          return (
            <li key={k} className="flex items-center gap-3">
              <span className="w-36 shrink-0 text-[13px] text-[color:var(--muted)]">
                {AXIS_LABEL[k]}
              </span>
              {v === undefined ? (
                <>
                  <span className="hatch h-2.5 flex-1 bg-[color:var(--ink-09)]" />
                  <span className="mono w-20 shrink-0 text-right text-[12px] text-[color:var(--muted)]">
                    not run
                  </span>
                </>
              ) : (
                <>
                  <span className="h-2.5 flex-1 bg-[color:var(--ink-09)]">
                    <span
                      className="block h-2.5"
                      style={{
                        // Score units against the full 0–100 track, floored at a visible
                        // sliver so a genuinely tiny spread still reads as measured.
                        width: `${Math.max(1.5, Math.min(100, v))}%`,
                        background: "var(--figure)",
                        opacity: 0.75,
                      }}
                    />
                  </span>
                  <span className="mono w-20 shrink-0 text-right text-[14px] font-medium text-[color:var(--figure)]">
                    {v.toFixed(1)}
                  </span>
                </>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}

/**
 * The memo | dissent split view.
 *
 * State is owned by `useMemoDissent` and passed in, because the recommendation panel at
 * the top of the page renders the same server lock. One fetch, one lock, two views.
 */
export default function MemoDissent({ state }: { state: MemoDissentState }) {
  const {
    memo,
    memoMissing,
    dissent,
    dissentOpen,
    loading: loadingDissent,
    error,
    openDissent,
  } = state;

  if (memoMissing) {
    return (
      <EmptyState title="No memo has been written for this company.">
        The memo generator has not run against this record. Nothing is shown in its place
        — a memo assembled from another company&apos;s evidence would be worse than none.
      </EmptyState>
    );
  }
  if (!memo) {
    return (
      <Loading
        label="memo"
        stages={[
          "requesting the memo…",
          "resolving inline event citations…",
          "checking whether the recommendation is unlocked…",
        ]}
      />
    );
  }

  const m = memo.data;

  return (
    <div className="space-y-3">
      {error && <ErrorNote message={`Dissent load failed: ${error}`} />}

      <div className="grid gap-3 lg:grid-cols-2">
        {/* ---------------------------------------------------------------- memo */}
        <section className="border border-[color:var(--rule)] bg-[color:var(--ground)]">
          <header className="border-b border-[color:var(--rule)] px-5 py-3">
            <h3 className="meta text-[color:var(--figure)]">
              Investment memo
            </h3>
            <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
              Every claim cites its event id. Gaps are flagged, never filled.
            </p>
          </header>

          <div className="space-y-5 px-5 py-4">
            {m.sections.map((s) => (
              <div key={s.heading}>
                <h4 className="meta text-[color:var(--muted)]">
                  {s.heading}
                </h4>
                <p className="mt-1.5 text-[15px] leading-[1.55] text-[color:var(--muted)]">
                  <Cited text={s.body} />
                </p>
              </div>
            ))}

            {m.gaps.length > 0 && (
              <div
                className="border px-4 py-3"
                style={{
                  borderColor: "var(--figure)",
                  background: "color-mix(in oklab, var(--figure) 8%, transparent)",
                }}
              >
                <h4
                  className="meta"
                  style={{ color: "var(--figure)" }}
                >
                  ⚠ What this memo does not know
                </h4>
                <ul className="mt-2 space-y-1.5">
                  {m.gaps.map((g, i) => (
                    <li key={i} className="text-[14px] leading-snug text-[color:var(--muted)]">
                      <span className="mr-2 text-[color:var(--muted)]">—</span>
                      {g}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/*
              The recommendation is NOT repeated here. It leads the page in the decision
              panel, where it is the first thing read — restating it at the bottom of the
              memo column would give the same sentence two homes and neither authority.
            */}
          </div>
        </section>

        {/* ------------------------------------------------------------- dissent */}
        <section
          className="border bg-[color:var(--ground)]"
          style={{ borderColor: dissentOpen ? "var(--accent)" : "var(--rule)" }}
        >
          <header className="border-b border-[color:var(--rule)] px-5 py-3">
            <h3 className="meta text-[color:var(--figure)]">
              Dissent · the case against
            </h3>
            <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
              Generated adversarially against the memo, from the same evidence set.
            </p>
          </header>

          {!dissentOpen ? (
            <div className="flex min-h-[280px] flex-col items-center justify-center px-6 py-10 text-center">
              <p className="max-w-sm text-[15px] leading-[1.55] text-[color:var(--muted)]">
                The dissent is not open yet. Opening it is what unlocks the recommendation
                on the left — the order is enforced by the server, not by this page.
              </p>
              <button
                type="button"
                onClick={openDissent}
                disabled={loadingDissent}
                className="mt-5 border px-5 py-2.5 text-[15px] font-medium tracking-wide text-[color:var(--figure)] transition disabled:opacity-60"
                style={{
                  borderColor: "var(--accent)",
                  background: "color-mix(in oklab, var(--accent) 14%, transparent)",
                }}
              >
                {loadingDissent ? "Opening…" : "Open the dissent"}
              </button>
              {loadingDissent && (
                <Busy
                  className="mt-3 w-full max-w-sm"
                  budgetMs={TIMEOUT.read * 2}
                  label="Reading the dissent, then re-requesting the memo unlocked"
                />
              )}
            </div>
          ) : dissent ? (
            <div className="space-y-5 px-5 py-4">
              {/*
                The load-bearing claim leads the column. It is the single thing that
                kills the thesis if it is false, which makes it the highest-value
                sentence on this half of the page — it was previously third, below prose
                a reader has to get through first.
              */}
              <div
                className="border-2 px-4 py-3"
                style={{
                  borderColor: "var(--accent)",
                  background: "color-mix(in oklab, var(--accent) 9%, transparent)",
                }}
              >
                <h4 className="meta" style={{ color: "var(--accent)" }}>
                  ◂ Load-bearing claim — if this is false, the thesis fails
                </h4>
                <p className="mt-1.5 text-[16px] leading-[1.5] font-medium text-[color:var(--figure)]">
                  <Cited text={dissent.data.load_bearing_claim} />
                </p>
                <p className="mt-1.5 text-[13px] text-[color:var(--muted)]">
                  Named, not hedged. This is the one thing to go and check.
                </p>
              </div>

              <div>
                <h4 className="meta text-[color:var(--muted)]">Bear case</h4>
                <p className="mt-1.5 text-[15px] leading-[1.55] text-[color:var(--muted)]">
                  <Cited text={dissent.data.bear_case} />
                </p>
              </div>

              {dissent.data.weakest_evidence.length > 0 && (
                <div>
                  <h4 className="meta text-[color:var(--muted)]">Weakest evidence</h4>
                  <ul className="mt-2 space-y-2">
                    {dissent.data.weakest_evidence.map((w, i) => (
                      <li
                        key={i}
                        className="border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2 text-[14px] leading-snug text-[color:var(--muted)]"
                      >
                        <Cited text={w} />
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              <AxisSpreads spreads={dissent.data.axis_spreads} />
            </div>
          ) : (
            <div className="p-5">
              <Loading label="dissent" />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
