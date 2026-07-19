"use client";

/**
 * The ranked list as cards — the search-ranking display.
 *
 * Each row carries, in this order:
 *   1. the mark — a stored logo if one exists, otherwise a typographic monogram.
 *      Never a fetched favicon and never a guessed domain (see CompanyMark.tsx).
 *   2. the company name;
 *   3. what STOOD OUT about this specific company, from `api/standout.py`;
 *   4. the VC-adjusted rank WITH the core rank beside it, always both.
 *
 * ON (3). The summary is the BACKEND'S and this component only renders it. That
 * matters more than it looks. `api/standout.py` computes distinctiveness in Python
 * over the whole corpus — a green-flag rule that fired here and almost nowhere else, a
 * trait score far off the corpus median, an evidence footprint at the edge of the
 * distribution — and the model is only permitted to turn those computed findings into
 * a sentence it must quote spans for. Sentences it cannot ground are deleted, not
 * flagged, and a company with nothing distinctive gets a summary saying so.
 *
 * So this file renders three things it would be easy to drop and must not:
 *   - `summary_source`, because "a model wrote this sentence from our findings" and
 *     "Python wrote this sentence" are different claims and a reader is entitled to
 *     know which one they are reading;
 *   - the computed `distinctives` behind the prose, each with its own comparison;
 *   - `dropped_sentences`, because a deleted sentence is the verifier working and
 *     hiding it would make the mechanism invisible exactly when it fired.
 *
 * A row whose summary has not been computed says "not yet generated" and offers the
 * button. It never renders an empty line, which would read as a finding of nothing.
 *
 * ON (4). §3 of docs/DIFFERENTIATOR.md is why this is not a toggle: "a council tuned
 * to a VC's history will reproduce that VC's blind spots, with machine authority", and
 * the stated mitigation is to always show core rank beside personal rank and surface
 * every company where they disagree sharply. Both numbers are always on the card, and
 * a divergence at or past the backend's own DIVERGENCE_HEADLINE of 3 places is the
 * card's headline rather than a footnote — three places on a thirteen-company list is
 * a different shortlist.
 *
 * Colour carries none of this. Divergence is told by numerals, a glyph, a sentence and
 * rule-work (DESIGN.md §2 — five hues, no sixth). `--signal` does not appear in this
 * file at all; it stays rationed to contradicted claims and caught injections.
 */

import Link from "next/link";
import { useCallback, useEffect, useMemo, useState } from "react";
import {
  getStandout,
  logoUrl,
  type RankedCompany,
  type Standout,
} from "@/lib/api";
import type { PersonalRanking, PersonalRankRow } from "@/lib/vc";
import { AXIS_KEYS, AXIS_INDEX, AXIS_LABEL } from "@/lib/types";
import CompanyMark from "./CompanyMark";
import { GateBadge, SourceRef } from "./ui";

/** The backend's own threshold (intelligence/custom_council.py::DIVERGENCE_HEADLINE).
 *  Mirrored, not invented — a divergence is a headline when the ranker says it is. */
const DIVERGENCE_HEADLINE = 3;

function RankNumerals({
  personal,
  core,
}: {
  personal: number | null;
  core: number | null;
}) {
  return (
    <div className="flex shrink-0 items-stretch border border-[color:var(--rule)]">
      <div className="min-w-[76px] px-3 py-2">
        <div className="meta text-[color:var(--muted)]">Your rank</div>
        <div className="font-[family-name:var(--font-instrument-serif)] text-[34px] leading-none">
          {personal ?? "—"}
        </div>
      </div>
      <div className="min-w-[76px] border-l border-[color:var(--rule)] px-3 py-2">
        <div className="meta text-[color:var(--muted)]">Core rank</div>
        <div className="font-[family-name:var(--font-instrument-serif)] text-[34px] leading-none text-[color:var(--muted)]">
          {core ?? "—"}
        </div>
      </div>
    </div>
  );
}

function Divergence({ row }: { row: PersonalRankRow }) {
  const d = row.divergence;
  if (d === 0) {
    return (
      <p className="caption max-w-none text-[color:var(--muted)]">
        Core and your council both place this {row.core_rank}. Agreement is confirmation,
        not a finding.
      </p>
    );
  }
  const headline = Math.abs(d) >= DIVERGENCE_HEADLINE;
  const promoted = d > 0;
  return (
    <div
      className="pl-3"
      style={{
        borderLeft: headline ? "2px solid var(--accent)" : "1px solid var(--rule)",
      }}
    >
      <div className="meta" style={{ color: headline ? "var(--accent)" : "var(--muted)" }}>
        <span aria-hidden>{promoted ? "▲" : "▼"}</span>{" "}
        {headline ? "Sharp disagreement" : "Disagreement"} · {Math.abs(d)}{" "}
        {Math.abs(d) === 1 ? "place" : "places"} {promoted ? "up" : "down"} on core
      </div>
      <p className="caption mt-1 max-w-none text-[color:var(--muted)]">{row.why}</p>
    </div>
  );
}

function Axes({ c }: { c: RankedCompany }) {
  return (
    <div className="flex flex-wrap gap-x-5 gap-y-1">
      {AXIS_KEYS.map((k) => {
        const a = c.axes[k];
        // Three distinct states, deliberately not collapsed. An absent axis means
        // the backend never computed it (live-sourced rows carry only `founder`);
        // a null score means it was computed and found nothing. Rendering the
        // first as the second would claim a measurement we never attempted.
        if (!a) {
          return (
            <span key={k} className="mono text-[12px] text-[color:var(--muted)]">
              <span className="meta">{AXIS_INDEX[k]}</span> {AXIS_LABEL[k]}{" "}
              <span title="This axis was not computed for this company — it is unmeasured, not zero.">
                not computed
              </span>
            </span>
          );
        }
        return (
          <span key={k} className="mono text-[12px] text-[color:var(--muted)]">
            <span className="meta">{AXIS_INDEX[k]}</span> {AXIS_LABEL[k]}{" "}
            {a.score === null || a.score === undefined ? (
              <span title={a.reason ?? "no evidence on this axis"}>no evidence</span>
            ) : (
              <span className="text-[color:var(--figure)]">
                {a.score.toFixed(1)}
                {a.band !== null && a.band !== undefined ? ` ±${a.band.toFixed(1)}` : ""}
              </span>
            )}
          </span>
        );
      })}
    </div>
  );
}

/** The standout block. Renders the summary, its provenance, and its receipts. */
function StandoutBlock({
  companyId,
  initial,
}: {
  companyId: string;
  initial: Standout | undefined;
}) {
  const [s, setS] = useState<Standout | undefined>(initial);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [open, setOpen] = useState(false);

  const compute = useCallback(
    async (e: React.MouseEvent) => {
      // The whole card navigates on click; this control must not take the reader
      // to another page while it is computing a summary for the one they are on.
      e.stopPropagation();
      setBusy(true);
      setError(null);
      const r = await getStandout(companyId);
      if (r.ok) setS(r.data);
      else setError(r.error);
      setBusy(false);
    },
    [companyId],
  );

  const pending = !s || s.status === "not_generated" || s.summary === null;

  return (
    <div className="mt-3 border-t border-[color:var(--rule)] pt-3">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <div className="meta text-[color:var(--muted)]">What stood out here</div>
        {!pending && s?.summary_source && (
          <span
            className="meta border px-1.5 py-0.5"
            style={{
              color: "var(--muted)",
              borderColor: "var(--rule)",
              borderStyle: s.summary_source === "model" ? "dashed" : "solid",
            }}
            title={
              s.summary_source === "model"
                ? "A model wrote this sentence from findings Python computed, and every specific term in it was checked against a quoted span."
                : "Python wrote this sentence directly from the computed comparison. No model was involved."
            }
          >
            {s.summary_source === "model" ? "MODEL PROSE, VERIFIED" : "COMPUTED PROSE"}
          </span>
        )}
      </div>

      {pending ? (
        <div className="mt-1">
          <p className="caption max-w-none text-[color:var(--muted)]">
            Not yet generated. The comparison runs against the whole corpus and is not
            computed inline for thirteen rows at once — it is one call, on request.
          </p>
          <button
            type="button"
            onClick={(e) => void compute(e)}
            disabled={busy}
            className="meta mt-1.5 border border-[color:var(--accent)] px-3 py-1 text-[color:var(--accent)] disabled:opacity-50"
          >
            {busy ? "COMPUTING…" : "COMPUTE WHAT STOOD OUT"}
          </button>
          {error && (
            <p className="caption mt-1.5 max-w-none text-[color:var(--figure)]">
              The summary could not be computed ({error}). Nothing is substituted in its
              place.
            </p>
          )}
        </div>
      ) : (
        <>
          <p className="mt-1 max-w-[80ch] text-[15px] leading-relaxed text-[color:var(--figure)]">
            {s!.summary}
          </p>
          {(s!.distinctives?.length || s!.dropped_sentences?.length) && (
            <>
              <button
                type="button"
                onClick={(e) => {
                  e.stopPropagation();
                  setOpen((o) => !o);
                }}
                className="meta mt-1.5 text-[color:var(--accent)]"
              >
                {open ? "HIDE" : "SHOW"} THE {s!.distinctives?.length ?? 0} COMPUTED
                {" "}
                {s!.distinctives?.length === 1 ? "COMPARISON" : "COMPARISONS"}
                {s!.dropped_sentences?.length
                  ? ` · ${s!.dropped_sentences!.length} SENTENCE(S) DROPPED`
                  : ""}
              </button>
              {open && (
                <div
                  className="mt-2 border-l border-[color:var(--rule)] pl-3"
                  onClick={(e) => e.stopPropagation()}
                >
                  <ul className="space-y-1.5">
                    {/* Not `.caption`: that class caps at 32ch, which is right for a
                        marginal note and wrong for a comparison sentence carrying two
                        figures. These are the reading content of the drill-down. */}
                    {(s!.distinctives ?? []).map((d) => (
                      <li
                        key={`${d.kind}:${d.key}`}
                        className="max-w-[80ch] text-[13px] leading-relaxed text-[color:var(--muted)]"
                      >
                        <span className="mono text-[color:var(--figure)]">
                          {d.kind.replace(/_/g, " ")}
                        </span>{" "}
                        — {d.detail}; {d.comparison}{" "}
                        <span className="mono">(strength {d.strength.toFixed(2)})</span>
                      </li>
                    ))}
                  </ul>
                  {s!.citations?.length ? (
                    <ul className="mt-2 space-y-2">
                      {s!.citations.map((cit) => (
                        <li key={cit.n}>
                          <blockquote className="evidence-span px-3 py-2">
                            “{cit.evidence_span}”
                          </blockquote>
                          <SourceRef url={cit.source_url} />
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {s!.dropped_sentences?.length ? (
                    <div className="mt-2">
                      <div className="meta text-[color:var(--muted)]">
                        Deleted by the verifier
                      </div>
                      <ul className="mt-1 space-y-1">
                        {s!.dropped_sentences.map((line) => (
                          <li
                            key={line}
                            className="max-w-[80ch] text-[13px] leading-relaxed text-[color:var(--figure)]"
                          >
                            {line}
                          </li>
                        ))}
                      </ul>
                      <p className="mt-1 max-w-[80ch] text-[13px] leading-relaxed text-[color:var(--muted)]">
                        These sentences asserted something no stored span supports, so
                        they were removed rather than shown with a warning on them.
                      </p>
                    </div>
                  ) : null}
                </div>
              )}
            </>
          )}
        </>
      )}
    </div>
  );
}

function Card({
  c,
  personalRow,
  coreRank,
  dim,
  onOpen,
}: {
  c: RankedCompany;
  personalRow: PersonalRankRow | null;
  coreRank: number | null;
  dim: boolean;
  onOpen: (id: string) => void;
}) {
  const headline =
    personalRow !== null && Math.abs(personalRow.divergence) >= DIVERGENCE_HEADLINE;

  return (
    <article
      onClick={() => onOpen(c.id)}
      className={`cursor-pointer border bg-[color:var(--ground)] p-4 hover:bg-[color:var(--ink-09)] ${
        dim ? "opacity-30" : ""
      }`}
      style={{ borderColor: headline ? "var(--accent)" : "var(--rule)" }}
    >
      <div className="flex flex-wrap items-start gap-4">
        <CompanyMark name={c.name} logo={logoUrl(c)} />

        <div className="min-w-[260px] flex-1">
          <div className="flex flex-wrap items-center gap-2">
            {/*
              The whole card is the click target, which is right for a pointer and
              useless without one: an <article onClick> is not focusable and not
              announced as actionable. The name is therefore a real link — it carries
              the keyboard path, the status-bar preview, and open-in-new-tab, none of
              which the card handler can provide. The card handler stays for the
              pointer, and the link stops the event so one click is one navigation.
            */}
            <h3 className="font-[family-name:var(--font-instrument-serif)] text-[26px] leading-tight">
              <Link
                href={`/company/${encodeURIComponent(c.id)}`}
                onClick={(e) => {
                  if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                  e.preventDefault();
                  e.stopPropagation();
                  onOpen(c.id);
                }}
                className="hover:underline"
              >
                {c.name}
              </Link>
            </h3>
            <GateBadge gate={c.gate} />
          </div>
          <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
            {c.one_liner}
          </p>
          <p className="meta mt-1 text-[color:var(--muted)]">
            {c.sector} · {c.stage} · {c.geo}
          </p>
        </div>

        <RankNumerals
          personal={personalRow ? personalRow.personal_rank : null}
          core={coreRank}
        />
      </div>

      <StandoutBlock companyId={c.id} initial={c.standout} />

      <div className="mt-3 flex flex-wrap items-start justify-between gap-3 border-t border-[color:var(--rule)] pt-3">
        <Axes c={c} />
        {personalRow && <Divergence row={personalRow} />}
      </div>
    </article>
  );
}

export default function RankedCards({
  companies,
  personal,
  highlight,
  onOpen,
  onOrderChange,
}: {
  companies: RankedCompany[];
  /** Null when no session, or when the profile is too thin to personalise. */
  personal: PersonalRanking | null;
  /** Ids matched by the active compound query. Non-matches dim, never disappear. */
  highlight?: Set<string> | null;
  onOpen: (id: string) => void;
  /** Reports the rendered order upward so the company page can walk prev/next
   *  against the order actually on screen — the personal one, when it is active. */
  onOrderChange?: (ids: string[]) => void;
}) {
  /** UUID -> row. The personal layer keys by UUID; the list keys by slug. */
  const byUuid = useMemo(() => {
    const m = new Map<string, RankedCompany>();
    for (const c of companies) if (c.company_id) m.set(c.company_id, c);
    return m;
  }, [companies]);

  const personalised = Boolean(personal?.personalised && personal.rows.length);

  const rowFor = useMemo(() => {
    const m = new Map<string, PersonalRankRow>();
    if (personalised && personal) {
      for (const r of personal.rows) {
        const c = byUuid.get(r.company_id);
        if (c) m.set(c.id, r);
      }
    }
    return m;
  }, [personal, personalised, byUuid]);

  /**
   * Core rank comes from the backend's own `core_rank` block when a session exists and
   * from the row's own `rank` field otherwise. It is never recomputed here — §0 of the
   * differentiator turns on the core order being READ and passed in as an input.
   */
  const coreFor = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of personal?.core_rank ?? []) {
      const c = byUuid.get(r.company_id);
      if (c) m.set(c.id, r.core_rank);
    }
    for (const c of companies) {
      if (!m.has(c.id) && typeof c.rank === "number") m.set(c.id, c.rank);
    }
    return m;
  }, [personal, byUuid, companies]);

  const ordered = useMemo(() => {
    const key = (c: RankedCompany) =>
      rowFor.get(c.id)?.personal_rank ?? coreFor.get(c.id) ?? Number.MAX_SAFE_INTEGER;
    return [...companies].sort((a, b) => key(a) - key(b));
  }, [companies, rowFor, coreFor]);

  const orderIds = useMemo(() => ordered.map((c) => c.id), [ordered]);
  useEffect(() => {
    onOrderChange?.(orderIds);
  }, [orderIds, onOrderChange]);

  const sharp = personalised
    ? ordered.filter(
        (c) => Math.abs(rowFor.get(c.id)?.divergence ?? 0) >= DIVERGENCE_HEADLINE,
      )
    : [];

  return (
    <div>
      <div className="mb-3 border border-[color:var(--rule)] px-4 py-3">
        <div className="meta text-[color:var(--muted)]">Ranking policy</div>
        {personalised ? (
          <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
            Ordered by <strong className="text-[color:var(--figure)]">your</strong> rank,
            with the core rank printed beside every row. The core order is READ from the
            objective endpoint and re-sorted by your council — it is never recomputed, so
            the two numbers are comparable.{" "}
            {sharp.length
              ? `${sharp.length} ${
                  sharp.length === 1 ? "company diverges" : "companies diverge"
                } by ${DIVERGENCE_HEADLINE} places or more; those are the finding.`
              : `Nothing diverges by ${DIVERGENCE_HEADLINE} places or more, so there is no finding here — only confirmation.`}
          </p>
        ) : (
          <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
            Core rank only.{" "}
            {personal?.reason ?? "No VC profile is active on this session."} The
            objective ranking is unaffected by personalisation being off — that is the
            design, not a degraded mode.
          </p>
        )}
      </div>

      {sharp.length > 0 && (
        <div className="mb-3 border border-[color:var(--accent)] px-4 py-3">
          <div className="meta text-[color:var(--accent)]">
            Where your council disagrees with core
          </div>
          <ul className="mt-1.5 space-y-1">
            {sharp.map((c) => {
              const r = rowFor.get(c.id)!;
              return (
                <li key={c.id} className="caption max-w-none text-[color:var(--muted)]">
                  <span className="mono text-[color:var(--figure)]">
                    {c.name} — core {r.core_rank} → yours {r.personal_rank} (
                    {r.divergence > 0 ? "+" : ""}
                    {r.divergence})
                  </span>{" "}
                  {personal?.disagreements.find((d) => d.company_id === r.company_id)
                    ?.explanation ?? r.why}
                </li>
              );
            })}
          </ul>
        </div>
      )}

      <div className="space-y-3">
        {ordered.map((c) => (
          <Card
            key={c.id}
            c={c}
            personalRow={rowFor.get(c.id) ?? null}
            coreRank={coreFor.get(c.id) ?? null}
            dim={highlight ? !highlight.has(c.id) : false}
            onOpen={onOpen}
          />
        ))}
      </div>

      <p className="caption mt-3 max-w-none text-[color:var(--muted)]">
        No mark on a card is a fetched or guessed logo. No company route serves a logo
        field, so every mark here is a typographic monogram of the company name. The
        &ldquo;what stood out&rdquo; line is served by the backend, which computes the
        comparison against the whole corpus in Python and lets a model write the sentence
        only against spans it has to quote — the chip beside it says which of the two
        produced the prose you are reading.
      </p>
    </div>
  );
}
