"use client";

/**
 * Route `/pipeline` — the working dashboard.
 *
 * Thesis config (config, not code) → NL compound query → ranked list with
 * per-axis scores and momentum. Renders on fixtures if the backend is down.
 */

import { useCallback, useEffect, useState } from "react";
import { getCompanies, getThesis, runQuery, type Result } from "@/lib/api";
import type { CompanySummary, QueryResult, Thesis } from "@/lib/types";
import CompanyList from "@/components/CompanyList";
import Shell from "@/components/Shell";
import ThesisPanel from "@/components/ThesisPanel";
import { ErrorNote, Loading, SourceChip } from "@/components/ui";

const EXAMPLES = [
  "infra founders with rising trend and unverified revenue",
  "cold start companies routed to proof protocol",
  "AI companies with integrity flags",
  "data tooling with a contradicted claim",
];

export default function PipelinePage() {
  const [companies, setCompanies] = useState<Result<CompanySummary[]> | null>(null);
  const [thesis, setThesis] = useState<Result<Thesis> | null>(null);
  const [error, setError] = useState<string | null>(null);

  const [q, setQ] = useState("");
  const [queryResult, setQueryResult] = useState<Result<QueryResult> | null>(null);
  const [querying, setQuerying] = useState(false);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const [c, t] = await Promise.all([getCompanies(), getThesis()]);
        if (!live) return;
        setCompanies(c);
        setThesis(t);
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  const submitQuery = useCallback(
    async (text: string) => {
      const trimmed = text.trim();
      setQ(text);
      if (!trimmed || !companies) {
        setQueryResult(null);
        return;
      }
      setQuerying(true);
      try {
        setQueryResult(await runQuery(trimmed, companies.data));
      } catch (e) {
        setError(e instanceof Error ? e.message : String(e));
      } finally {
        setQuerying(false);
      }
    },
    [companies],
  );

  if (error && !companies) {
    return (
      <Shell title="pipeline">
        <ErrorNote message={error} />
      </Shell>
    );
  }
  if (!companies || !thesis) {
    return (
      <Shell title="pipeline">
        <Loading label="pipeline" />
      </Shell>
    );
  }

  const matched = queryResult ? new Set(queryResult.data.company_ids) : null;

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
          />
        )}

        <ThesisPanel initial={thesis.data} />

        {/* --------------------------------------------- NL compound query */}
        <section className="border border-[color:var(--rule)] p-5">
          <label htmlFor="nlq" className="meta text-[color:var(--muted)]">
            Compound query
          </label>
          <form
            className="mt-2 flex flex-wrap gap-2"
            onSubmit={(e) => {
              e.preventDefault();
              void submitQuery(q);
            }}
          >
            <input
              id="nlq"
              value={q}
              onChange={(e) => setQ(e.target.value)}
              placeholder="infra founders with rising trend and unverified revenue"
              className="min-w-[280px] flex-1 border border-[color:var(--rule)] bg-transparent px-4 py-3 text-[16px] placeholder:text-[color:var(--muted)]"
            />
            <button
              type="submit"
              disabled={querying}
              className="meta border border-[color:var(--accent)] bg-[color:var(--accent)] px-5 py-3 text-[color:var(--paper)] disabled:opacity-60"
            >
              {querying ? "RUNNING…" : "RUN QUERY"}
            </button>
            {queryResult && (
              <button
                type="button"
                onClick={() => {
                  setQ("");
                  setQueryResult(null);
                }}
                className="meta border border-[color:var(--rule)] px-4 py-3 text-[color:var(--muted)]"
              >
                CLEAR
              </button>
            )}
          </form>

          <div className="mt-2 flex flex-wrap items-center gap-2">
            <span className="meta text-[color:var(--muted)]">Try</span>
            {EXAMPLES.map((ex) => (
              <button
                key={ex}
                type="button"
                onClick={() => void submitQuery(ex)}
                className="caption border border-[color:var(--rule)] px-3 py-1 text-[color:var(--muted)] hover:border-[color:var(--accent)] hover:text-[color:var(--accent)]"
              >
                {ex}
              </button>
            ))}
          </div>

          {queryResult && (
            <div className="mt-3 border border-[color:var(--rule)] px-4 py-3">
              <div className="meta text-[color:var(--muted)]">Predicates applied</div>
              <code className="mono mt-1 block text-[13px] text-[color:var(--accent)]">
                {queryResult.data.parsed}
              </code>
              <p className="caption mt-1.5 max-w-none text-[color:var(--muted)]">
                {queryResult.data.company_ids.length} of {companies.data.length} match.
                Non-matching rows are dimmed rather than removed — you can still see what
                was excluded and why.
              </p>
            </div>
          )}
        </section>

        <CompanyList companies={companies.data} highlight={matched} />
      </div>
    </Shell>
  );
}
