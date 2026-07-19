"use client";

/**
 * Route `/pipeline` — the working dashboard.
 *
 * ONE query control → ranked list with per-axis scores and momentum. Renders on
 * fixtures if the backend is down.
 *
 * The page used to stack two controls: a structured thesis panel and a
 * natural-language box. Collapsing them to one input forced a semantic choice, because
 * they were never the same operation:
 *
 *   THESIS  (`core/thesis.in_scope`) EXCLUDES companies from the pipeline. A sector
 *           dropped there stops being sourced at all.
 *   QUERY   (`GET /query`) only FILTERS the already-screened list. It dims rows.
 *
 * The single box on this page is a FILTER, and it says so. A VC typing "seed-stage
 * fintech in Europe, cheque under $2M" means "narrow what I am looking at", not
 * "permanently rewrite my fund's mandate". The standing thesis is real state and still
 * exists — it moved to `/thesis` — and a query can be promoted into it only through the
 * explicit action below the readback, never as a side effect of typing.
 *
 * Three further things are non-negotiable here:
 *
 *   1. The in-flight state is VISIBLE and BOUNDED — a progress bar, a stated reason for
 *      the disabled button, and a hard timeout in the client. There is no path where the
 *      button greys out and stays that way.
 *   2. The loading flag is cleared in a `finally`, and the query call itself cannot
 *      reject, so a failure surfaces as an error with a retry rather than as a dead control.
 *   3. Zero results are an ANSWER, not a failure. The readback of what the query was
 *      understood to mean is shown either way, and the empty state says so plainly.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";
import {
  getCompanies,
  getThesis,
  putThesis,
  runQuery,
  thesisDiffFromFilter,
  TIMEOUT,
  type RankedCompany,
  type Result,
} from "@/lib/api";
import type { CompanySummary, QueryResult, Thesis } from "@/lib/types";
import { getPersonalRank, type PersonalRanking } from "@/lib/vc";
import { readListState, writeListState, type ListState } from "@/lib/listState";
import CompanyList from "@/components/CompanyList";
import OutreachQueue from "@/components/OutreachQueue";
import RankedCards from "@/components/RankedCards";
import Shell from "@/components/Shell";
import { Busy, EmptyState, ErrorNote, Loading, SourceChip } from "@/components/ui";

/**
 * Suggestions, in demo order. The first four return hits against the seeded data; the
 * last is kept deliberately and labelled, because a demo needs a live example of the
 * empty state — and the honest one to show is an industry this system has never
 * sourced, which is exactly the query that used to return the ENTIRE list under the
 * heading "no filters recognised".
 *
 * The zero-hit query used to be the DEFAULT text in the box, which meant the first click
 * of a working feature returned nothing and read as a broken button. The default is now
 * a query that hits.
 */
const EXAMPLES: { q: string; hits: "some" | "none" }[] = [
  { q: "cold start companies routed to proof protocol", hits: "some" },
  { q: "AI companies with integrity flags", hits: "some" },
  { q: "data tooling with a contradicted claim", hits: "some" },
  { q: "top 3 dev tools with founder score above 0.7", hits: "some" },
  { q: "seed-stage climate hardware in Europe, cheque under $2M", hits: "none" },
];

const DEFAULT_QUERY = EXAMPLES[0].q;

/** Cards carry the mark, the standout summary and the personal-vs-core rank pair.
 *  The table stays reachable because a dense numeric read of thirteen rows is a
 *  genuinely different job from scanning them. */
type View = "cards" | "table";

export default function PipelinePage() {
  const router = useRouter();
  const [companies, setCompanies] = useState<Result<CompanySummary[]> | null>(null);
  const [thesis, setThesis] = useState<Result<Thesis> | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [view, setView] = useState<View>("cards");
  /**
   * The personal layer. Loaded separately and allowed to fail: `api/routers/personal.py`
   * requires a session and nothing on the objective page depends on it, so an anonymous
   * visitor or a login outage costs the personal rank and leaves the core rank intact
   * (DIFFERENTIATOR §1). Null here means "core rank only", which the cards state.
   */
  const [personal, setPersonal] = useState<PersonalRanking | null>(null);

  const [q, setQ] = useState(DEFAULT_QUERY);
  const [queryResult, setQueryResult] = useState<Result<QueryResult> | null>(null);
  const [queryError, setQueryError] = useState<string | null>(null);
  const [querying, setQuerying] = useState(false);

  const [restored, setRestored] = useState<ListState | null>(null);
  const orderRef = useRef<string[]>([]);

  /** Promotion of a query into the standing thesis. Always an explicit, confirmed
   *  action — never a side effect of typing, because it changes what the fund sees. */
  const [promoting, setPromoting] = useState(false);
  const [promoted, setPromoted] = useState<string | null>(null);

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

      // After the page is renderable, never before. A 401 here is the ordinary
      // anonymous state, not an error, and it must not delay the objective ranking.
      const p = await getPersonalRank();
      if (live) setPersonal(p.ok ? p.data : null);
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
            `The query endpoint did not answer (${r.note ?? "unknown error"}). The rows below were filtered in this browser, against the company list already on screen — nothing was invented, but any clause about claim-level evidence could not be applied at all. The notes under the readback say which. Retry for a live answer.`,
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
    setPromoted(null);
    writeListState({ q: "", matched: null, parsed: null });
  }, []);

  /**
   * Promote the current query's filter into the standing thesis.
   *
   * The confirm step is the point. This is the one action on the page that changes
   * what the fund SEES rather than what this screen shows, and it names the exact
   * fields it will overwrite before it writes them.
   */
  const promote = useCallback(async () => {
    if (!thesis || !queryResult) return;
    const { next, changes } = thesisDiffFromFilter(thesis.data, queryResult.data.filter);
    if (!changes.length) return;
    if (
      !window.confirm(
        `Rewrite the standing thesis?\n\n${changes.join("\n")}\n\n` +
          "Unlike this query, the thesis EXCLUDES companies from the pipeline entirely — " +
          "anything outside it stops being sourced, scored and shown, and clearing the " +
          "query will not bring it back.",
      )
    ) {
      return;
    }
    setPromoting(true);
    try {
      const r = await putThesis(next);
      setThesis(r);
      setPromoted(
        r.source === "live"
          ? `Standing thesis updated: ${changes.join("; ")}. The pipeline re-screens on the next load.`
          : `Could not reach the backend (${r.note ?? "unknown error"}) — the thesis was NOT changed on the server.`,
      );
    } finally {
      setPromoting(false);
    }
  }, [thesis, queryResult]);

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

        {/* ------------------------------------ the one query control on this page */}
        <section className="border border-[color:var(--rule)] p-5">
          <label htmlFor="nlq" className="meta text-[color:var(--muted)]">
            Filter this view
          </label>
          <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
            Industry, stage, geography, cheque size and evidence, in one sentence. The
            model only translates it into a filter; the filter itself runs in Python, and
            what it was understood to mean is printed back below.{" "}
            <strong>This narrows what you are looking at.</strong> It does not change your{" "}
            <Link href="/thesis" className="text-[color:var(--accent)] underline">
              standing thesis
            </Link>
            , which excludes companies from the pipeline altogether.
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

              {/* Where the answer is weaker than it looks. A query box that quietly
                  drops half a sentence is worse than a form, so every clause that was
                  not understood, not applicable, or names something nothing in the list
                  mentions is stated here rather than absorbed into the result. */}
              {(queryResult.data.warnings ?? []).length > 0 && (
                <ul className="mt-3 space-y-1.5 border-t border-[color:var(--rule)] pt-3">
                  {(queryResult.data.warnings ?? []).map((w) => (
                    <li
                      key={w}
                      className="caption max-w-none text-[color:var(--figure)]"
                    >
                      <span className="meta mr-2 text-[color:var(--muted)]">NOTE</span>
                      {w}
                    </li>
                  ))}
                </ul>
              )}

              {/* Promotion is a SECOND, deliberate action. Typing narrows the view;
                  only this button changes the fund's standing mandate. */}
              {thesisDiffFromFilter(thesis.data, queryResult.data.filter).changes.length >
                0 && (
                <div className="mt-3 flex flex-wrap items-center gap-3 border-t border-[color:var(--rule)] pt-3">
                  <button
                    type="button"
                    onClick={() => void promote()}
                    disabled={promoting}
                    className="meta border border-[color:var(--rule)] px-4 py-2 text-[color:var(--muted)] hover:border-[color:var(--accent)] hover:text-[color:var(--accent)] disabled:opacity-60"
                  >
                    {promoting ? "SAVING…" : "ADOPT AS STANDING THESIS"}
                  </button>
                  <span className="caption max-w-none text-[color:var(--muted)]">
                    Makes this permanent:{" "}
                    {thesisDiffFromFilter(thesis.data, queryResult.data.filter).changes.join(
                      "; ",
                    )}
                    . Companies outside it stop appearing at all.
                  </span>
                </div>
              )}

              {promoted && (
                <p className="caption mt-2 max-w-none text-[color:var(--accent)]">
                  {promoted}
                </p>
              )}
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

        {/* -------------------------------------------------- the ranked list */}
        <div className="flex flex-wrap items-center justify-between gap-3">
          <h2 className="meta text-[color:var(--figure)]">Ranked list</h2>
          <div
            className="flex items-center gap-1"
            role="group"
            aria-label="Ranked list view"
          >
            {(["cards", "table"] as View[]).map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setView(v)}
                aria-pressed={view === v}
                className="meta border px-3 py-1.5"
                style={{
                  color: view === v ? "var(--accent)" : "var(--muted)",
                  borderColor: view === v ? "var(--accent)" : "var(--rule)",
                }}
              >
                {v === "cards" ? "CARDS" : "TABLE"}
              </button>
            ))}
          </div>
        </div>

        {view === "cards" ? (
          <RankedCards
            companies={companies.data as RankedCompany[]}
            personal={personal}
            highlight={matched}
            onOrderChange={(ids) => {
              orderRef.current = ids;
              writeListState({ order: ids });
            }}
            onOpen={(id) => {
              // Same contract the table honours, so returning from a company page
              // restores the cursor and the scroll position either way.
              writeListState({
                selected: id,
                order: orderRef.current,
                scrollY: window.scrollY,
              });
              router.push(`/company/${encodeURIComponent(id)}`);
            }}
          />
        ) : (
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
        )}

        {/* ---------------------------------- who may be contacted, and who may not */}
        <OutreachQueue />
      </div>
    </Shell>
  );
}
