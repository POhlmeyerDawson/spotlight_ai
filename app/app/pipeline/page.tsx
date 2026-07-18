"use client";

/**
 * Route `/pipeline` — the working dashboard.
 *
 * Thesis config (config, not code) → NL compound query → ranked list with
 * per-axis scores and momentum. Renders on fixtures if the backend is down.
 *
 * The compound query is the beat that has to work on the first click, so three things
 * are non-negotiable here:
 *
 *   1. The in-flight state is VISIBLE and BOUNDED — a progress bar, a stated reason for
 *      the disabled button, and a hard timeout in the client. There is no path where the
 *      button greys out and stays that way.
 *   2. The loading flag is cleared in a `finally`, and the query call itself cannot
 *      reject, so a failure surfaces as an error with a retry rather than as a dead control.
 *   3. Zero results are an ANSWER, not a failure. The readback of what the query was
 *      understood to mean is shown either way, and the empty state says so plainly.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { getCompanies, getThesis, runQuery, TIMEOUT, type Result } from "@/lib/api";
import type { CompanySummary, QueryResult, Thesis } from "@/lib/types";
import { readListState, writeListState, type ListState } from "@/lib/listState";
import CompanyList from "@/components/CompanyList";
import Shell from "@/components/Shell";
import ThesisPanel from "@/components/ThesisPanel";
import { Busy, EmptyState, ErrorNote, Loading, SourceChip } from "@/components/ui";

/**
 * Suggestions, in demo order. The first three return hits against the seeded data; the
 * fourth is kept deliberately and labelled, because a demo needs a live example of the
 * empty state and a zero-result query is the cheapest honest one.
 *
 * The zero-hit query used to be the DEFAULT text in the box, which meant the first click
 * of a working feature returned nothing and read as a broken button. The default is now
 * a query that hits.
 */
const EXAMPLES: { q: string; hits: "some" | "none" }[] = [
  { q: "cold start companies routed to proof protocol", hits: "some" },
  { q: "AI companies with integrity flags", hits: "some" },
  { q: "data tooling with a contradicted claim", hits: "some" },
  { q: "infra founders with rising trend and unverified revenue", hits: "none" },
];

const DEFAULT_QUERY = EXAMPLES[0].q;

export default function PipelinePage() {
  const [companies, setCompanies] = useState<Result<CompanySummary[]> | null>(null);
  const [thesis, setThesis] = useState<Result<Thesis> | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  const [q, setQ] = useState(DEFAULT_QUERY);
  const [queryResult, setQueryResult] = useState<Result<QueryResult> | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);

  const [restored, setRestored] = useState<ListState | null>(null);
  const orderRef = useRef<string[]>([]);

  /**
   * Load the page and restore the previous list state in one pass.
   *
   * The restore reads sessionStorage, which does not exist during SSR, so it cannot be
   * a lazy `useState` initialiser without causing a hydration mismatch. Doing it here,
   * after the first `await`, keeps it off the synchronous effect path as well.
   */
  useEffect(() => {
    let live = true;
    (async () => {
      const [c, t] = await Promise.all([getCompanies(), getThesis()]);
      if (!live) return;

      const s = readListState();
      setRestored(s);
      if (s.q) setQ(s.q);
      if (s.matched) {
        setQueryResult({
          data: {
            q: s.q,
            parsed: s.parsed ?? "restored from your last query",
            company_ids: s.matched,
            count: s.matched.length,
          },
          source: "fixture",
          note: "restored from this tab's previous query — run it again for a live answer",
        });
      }

      setCompanies(c);
      setThesis(t);
    })();
    return () => {
      live = false;
    };
  }, [reloadKey]);

  // Restore scroll only after the list is actually on screen and tall enough to scroll.
  useEffect(() => {
    const y = restored?.scrollY;
    if (!companies || !y) return;
    const id = requestAnimationFrame(() => window.scrollTo({ top: y }));
    return () => cancelAnimationFrame(id);
  }, [companies, restored?.scrollY]);

  const submitQuery = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      setQ(text);
      setQueryError(null);

      if (!trimmed) {
        setQueryResult(null);
        writeListState({ q: "", matched: null, parsed: null });
        return;
      }
      if (!companies) {
        setQueryError("The company list has not loaded yet — nothing to query against.");
        return;
      }

      setQuerying(true);
      try {
        const r = await runQuery(trimmed, companies.data);
        setQueryResult(r);
        if (r.failed) {
          setQueryError(
            `The query endpoint did not answer (${r.note ?? "unknown error"}). The result below was read locally from fixtures and may differ from the live answer.`,
          );
        }
        writeListState({
          q: trimmed,
          matched: r.data.company_ids,
          parsed: r.data.parsed,
        });
      } catch (e) {
        // runQuery is contractually non-rejecting; this exists so that a future
        // regression surfaces as a visible error instead of a stuck button.
        setQueryError(e instanceof Error ? e.message : String(e));
      } finally {
        // ALWAYS. The disabled state is released here and nowhere else.
        setQuerying(false);
      }
    },
    [companies],
  );

  const clearQuery = useCallback(() => {
    setQ("");
    setQueryResult(null);
    setQueryError(null);
    writeListState({ q: "", matched: null, parsed: null });
  }, []);

  if (!companies || !thesis) {
    return (
      <Shell title="pipeline">
        <Loading
          label="pipeline"
          stages={[
            "requesting the screened company list…",
            "reading the active thesis…",
            "ranking by the stated policy…",
          ]}
        />
      </Shell>
    );
  }

  const matched = queryResult ? new Set(queryResult.data.company_ids) : null;
  const hitCount = queryResult
    ? queryResult.data.company_ids.filter((id) =>
        companies.data.some((c) => c.id === id),
      ).length
    : 0;
  const zeroHits = queryResult !== null && queryResult.data.company_ids.length === 0;

  return (
    <Shell
      title="pipeline"
      lede={
        <>
          {companies.data.length} companies screened on three separate axes. No blended
          score exists anywhere in this system.
        </>
      }
      right={<SourceChip source={companies.source} note={companies.note} />}
      meta={
        <>
          SCREEN
          <br />
          {companies.data.length} RECORDS
        </>
      }
    >
      <div className="space-y-5">
        {companies.source === "fixture" && companies.note && (
          <ErrorNote
            message={`Backend unreachable — rendering local fixtures. (${companies.note})`}
            onRetry={() => setReloadKey((k) => k + 1)}
          />
        )}

        <ThesisPanel initial={thesis.data} />

        {/* --------------------------------------------- NL compound query */}
        <section className="border border-[color:var(--rule)] p-5">
          <label htmlFor="nlq" className="meta text-[color:var(--muted)]">
            Compound query
          </label>
          <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
            The model only translates the sentence into a filter. The filter itself runs
            in Python, and what it was understood to mean is printed back below.
          </p>
          <form
            className="mt-3 flex flex-wrap gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void submitQuery(q);
            }}
          >
            <input
              id="nlq"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder={DEFAULT_QUERY}
              disabled={querying}
              className="min-w-[280px] flex-1 border border-[color:var(--rule)] bg-transparent px-4 py-3 text-[16px] placeholder:text-[color:var(--muted)] disabled:opacity-60"
            />
            <button
              type="submit"
              disabled={querying || !q.trim()}
              // The disabled state always has a stated reason on hover and in the
              // sentence below the form. A greyed control that explains nothing is
              // indistinguishable from a hung one.
              title={
                querying
                  ? "Running — the button re-enables when the query returns or times out"
                  : !q.trim()
                    ? "Type a query first"
                    : "Run this query against the screened list"
              }
              className="meta border border-[color:var(--accent)] bg-[color:var(--accent)] px-5 py-3 text-[color:var(--paper)] disabled:opacity-60"
            >
              {querying ? "RUNNING…" : "RUN QUERY"}
            </button>
            {(queryResult || q) && (
              <button
                type="button"
                onClick={clearQuery}
                disabled={querying}
                className="meta border border-[color:var(--rule)] px-4 py-3 text-[color:var(--muted)] disabled:opacity-60"
              >
                CLEAR
              </button>
            )}
          </form>

          {querying && (
            <Busy
              className="mt-3"
              budgetMs={TIMEOUT.query}
              label={`Translating the sentence, then filtering ${companies.data.length} records in Python — gives up after ${TIMEOUT.query / 1000}s`}
            />
          )}

          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="meta text-[color:var(--muted)]">Try</span>
            {EXAMPLES.map((ex) => (
              <button
                key={ex.q}
                type="button"
                disabled={querying}
                onClick={() => void submitQuery(ex.q)}
                className="caption border border-[color:var(--rule)] px-3 py-1 text-[color:var(--muted)] hover:border-[color:var(--accent)] hover:text-[color:var(--accent)] disabled:opacity-50"
              >
                {ex.q}
                {ex.hits === "none" && (
                  <span className="meta ml-2 text-[color:var(--muted)]">
                    (0 hits, on purpose)
                  </span>
                )}
              </button>
            ))}
          </div>

          {queryError && (
            <div className="mt-3">
              <ErrorNote message={queryError} onRetry={() => void submitQuery(q)} />
            </div>
          )}

          {queryResult && (
            <div className="mt-3 border border-[color:var(--rule)] px-4 py-3">
              <div className="flex flex-wrap items-center justify-between gap-2">
                <div className="meta text-[color:var(--muted)]">
                  Understood as
                </div>
                <SourceChip source={queryResult.source} note={queryResult.note} />
              </div>
              <code className="mono mt-1 block text-[13px] text-[color:var(--accent)]">
                {queryResult.data.parsed}
              </code>
              <p className="caption mt-1.5 max-w-none text-[color:var(--muted)]">
                {queryResult.data.count} of {companies.data.length} match
                {hitCount !== queryResult.data.company_ids.length && (
                  <>
                    {" "}
                    ({hitCount} of them are in the list currently on screen)
                  </>
                )}
                . Non-matching rows are dimmed rather than removed — you can still see
                what was excluded and why.
              </p>
            </div>
          )}

          {zeroHits && (
            <div className="mt-3">
              <EmptyState
                title="Nothing matched. That is the answer, not an error."
                action={
                  <>
                    <button
                      type="button"
                      onClick={clearQuery}
                      className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
                    >
                      CLEAR THE QUERY
                    </button>
                    <button
                      type="button"
                      onClick={() => void submitQuery(DEFAULT_QUERY)}
                      className="meta border border-[color:var(--rule)] px-4 py-2 text-[color:var(--muted)]"
                    >
                      TRY ONE THAT HITS
                    </button>
                  </>
                }
              >
                The filter above ran against all {companies.data.length} records and
                every one of them failed at least one predicate. The conjunction is real:
                relaxing any single term will return rows.
              </EmptyState>
            </div>
          )}
        </section>

        <CompanyList
          companies={companies.data}
          highlight={matched}
          initialSort={restored?.sort ?? "founder"}
          initialSelected={restored?.selected ?? null}
          onSortChange={(s) => writeListState({ sort: s })}
          onOrderChange={(ids) => {
            orderRef.current = ids;
            writeListState({ order: ids });
          }}
        />
      </div>
    </Shell>
  );
}
