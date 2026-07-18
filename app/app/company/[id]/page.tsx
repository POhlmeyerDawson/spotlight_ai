"use client";

/**
 * Route `/company/[id]` — the workhorse.
 *
 * Three axes side by side · moving score line with a tightening band · trace
 * drill-down to a quoted span · per-claim trust · integrity flags · Proof Protocol ·
 * memo | dissent split with the server-side recommendation lock.
 *
 * There is no combined score on this page and no component here can produce one.
 *
 * INFORMATION ARCHITECTURE
 * This page carries seven sections and judges arrive looking for a specific one, so it
 * has a sticky section bar that lists them in DEMO ORDER and marks the section you are
 * currently in. The trace drill-down is the section judges click, so "Three axes" is
 * first and its own row states what clicking an axis does. Nothing here requires
 * scrolling blindly to discover.
 *
 * Getting back is always one action: the breadcrumb, the ← control in the section bar,
 * or Escape. Prev/next walk the ranked order the list handed over, so stepping through
 * the pipeline never requires returning to it.
 */

import { useRouter } from "next/navigation";
import { use, useCallback, useEffect, useMemo, useState } from "react";
import { getCompanies, getCompany, getScoreHistory, type Result } from "@/lib/api";
import type { AxisKey, CompanyDetail, CompanySummary, ScoreHistory } from "@/lib/types";
import { AXIS_KEYS } from "@/lib/types";
import { readListState, writeListState } from "@/lib/listState";
import AxisCard from "@/components/AxisCard";
import ClaimsTable from "@/components/ClaimsTable";
import IntegrityPanel from "@/components/IntegrityPanel";
import MemoDissent from "@/components/MemoDissent";
import ProofProtocolPanel from "@/components/ProofProtocolPanel";
import ScoreLine from "@/components/ScoreLine";
import Shell from "@/components/Shell";
import TraceDrawer from "@/components/TraceDrawer";
import {
  EmptyState,
  ErrorNote,
  GateBadge,
  Loading,
  Panel,
  SourceChip,
} from "@/components/ui";

interface Section {
  id: string;
  label: string;
  /** Shown under the bar when this section is current — what the section is for. */
  hint?: string;
}

/**
 * Section bar. Sticky under the nav, marks the current section, and is the one control
 * that makes a seven-section page navigable without scrolling to find things.
 *
 * Scrollspy is IntersectionObserver rather than a scroll listener: it costs nothing when
 * nothing is moving, which matters on a page that also runs an SVG replay.
 */
function SectionBar({
  sections,
  onBack,
  prev,
  next,
}: {
  sections: Section[];
  onBack: () => void;
  prev: { id: string; name: string } | null;
  next: { id: string; name: string } | null;
}) {
  const router = useRouter();
  const [current, setCurrent] = useState(sections[0]?.id ?? "");

  useEffect(() => {
    const els = sections
      .map((s) => document.getElementById(s.id))
      .filter((e): e is HTMLElement => e !== null);
    if (!els.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        // The current section is the topmost one intersecting the band just below the
        // sticky header. Picking the topmost avoids the flicker you get from taking
        // whichever entry fired last.
        const visible = entries
          .filter((e) => e.isIntersecting)
          .sort((a, b) => a.boundingClientRect.top - b.boundingClientRect.top);
        if (visible[0]) setCurrent(visible[0].target.id);
      },
      { rootMargin: "-96px 0px -55% 0px", threshold: 0 },
    );
    els.forEach((e) => observer.observe(e));
    return () => observer.disconnect();
  }, [sections]);

  const go = (id: string) => {
    const el = document.getElementById(id);
    if (!el) return;
    setCurrent(id);
    el.scrollIntoView({ behavior: "smooth", block: "start" });
  };

  const hint = sections.find((s) => s.id === current)?.hint;

  return (
    <div className="flex flex-col gap-1.5">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
        <button
          type="button"
          onClick={onBack}
          className="meta border border-[color:var(--accent)] px-3 py-1.5 text-[color:var(--accent)]"
          title="Back to the ranked list (Escape)"
        >
          ← PIPELINE
        </button>

        <nav aria-label="Sections of this company" className="flex flex-wrap items-center gap-1">
          {sections.map((s) => {
            const active = s.id === current;
            return (
              <button
                key={s.id}
                type="button"
                onClick={() => go(s.id)}
                aria-current={active ? "true" : undefined}
                className="meta border-b-2 px-2.5 py-1.5"
                style={{
                  color: active ? "var(--accent)" : "var(--muted)",
                  borderBottomColor: active ? "var(--accent)" : "transparent",
                }}
              >
                {s.label}
              </button>
            );
          })}
        </nav>

        <div className="ml-auto flex items-center gap-1">
          <button
            type="button"
            disabled={!prev}
            onClick={() => prev && router.push(`/company/${encodeURIComponent(prev.id)}`)}
            title={prev ? `Previous: ${prev.name}` : "First in the ranked order"}
            className="meta border border-[color:var(--rule)] px-2.5 py-1.5 text-[color:var(--muted)] disabled:opacity-40"
          >
            ↑ PREV
          </button>
          <button
            type="button"
            disabled={!next}
            onClick={() => next && router.push(`/company/${encodeURIComponent(next.id)}`)}
            title={next ? `Next: ${next.name}` : "Last in the ranked order"}
            className="meta border border-[color:var(--rule)] px-2.5 py-1.5 text-[color:var(--muted)] disabled:opacity-40"
          >
            ↓ NEXT
          </button>
        </div>
      </div>
      {hint && <p className="meta text-[color:var(--muted)]">{hint}</p>}
    </div>
  );
}

/**
 * Keyed by company id below, so navigating between companies remounts the whole view.
 * That is what guarantees an unlocked recommendation never leaks across companies.
 */
function CompanyView({ id }: { id: string }) {
  const router = useRouter();
  const [company, setCompany] = useState<Result<CompanyDetail> | null>(null);
  const [notFound, setNotFound] = useState(false);
  const [history, setHistory] = useState<Result<ScoreHistory> | null>(null);
  const [traceAxis, setTraceAxis] = useState<AxisKey | null>(null);
  const [neighbours, setNeighbours] = useState<CompanySummary[]>([]);
  const [reloadKey, setReloadKey] = useState(0);

  const backToPipeline = useCallback(() => {
    writeListState({ selected: id });
    router.push("/pipeline");
  }, [id, router]);

  // No resets at the top of this effect: the component is keyed by company id in the
  // default export, so a different company remounts it and every piece of state starts
  // fresh. Only an explicit retry needs to clear, and that happens in its handler.
  useEffect(() => {
    let live = true;

    (async () => {
      // The summary is fetched first and handed to getCompany as the identity anchor:
      // if the detail endpoint has no record for this id, the page still renders THIS
      // company from its screening record rather than substituting another one.
      const list = await getCompanies();
      if (!live) return;
      setNeighbours(list.data);
      const summary = list.data.find((c) => c.id === id) ?? null;

      const c = await getCompany(id, summary);
      if (!live) return;
      if (!c) {
        setNotFound(true);
        return;
      }
      setCompany(c);

      const h = await getScoreHistory(id, c.data.score_history);
      if (live) setHistory(h);
    })();

    return () => {
      live = false;
    };
  }, [id, reloadKey]);

  // Escape returns to the list. The trace drawer handles Escape itself and stops the
  // event there, so closing the drawer never also navigates away from the page.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape" || traceAxis) return;
      const el = e.target as HTMLElement | null;
      if (el && (el.tagName === "INPUT" || el.tagName === "TEXTAREA")) return;
      backToPipeline();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [backToPipeline, traceAxis]);

  // Prev/next follow the order the list was actually sorted into when you left it,
  // falling back to the fetched order if you deep-linked straight to this page.
  const { prev, next } = useMemo(() => {
    const saved = readListState().order;
    const order = saved.length ? saved : neighbours.map((c) => c.id);
    const i = order.indexOf(id);
    const nameOf = (cid: string) =>
      neighbours.find((c) => c.id === cid)?.name ?? cid;
    return {
      prev: i > 0 ? { id: order[i - 1], name: nameOf(order[i - 1]) } : null,
      next:
        i >= 0 && i < order.length - 1
          ? { id: order[i + 1], name: nameOf(order[i + 1]) }
          : null,
    };
  }, [id, neighbours]);

  if (notFound) {
    return (
      <Shell
        title="company not found"
        crumbs={[{ label: "Pipeline", href: "/pipeline" }, { label: id }]}
      >
        <EmptyState
          title={`No record for "${id}".`}
          action={
            <button
              type="button"
              onClick={backToPipeline}
              className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
            >
              ← BACK TO PIPELINE
            </button>
          }
        >
          Neither the backend nor the local fixtures hold this company. Nothing is being
          substituted in its place — a page showing another company&apos;s evidence under
          this name would be worse than this one.
        </EmptyState>
      </Shell>
    );
  }

  if (!company) {
    return (
      <Shell
        title="company"
        crumbs={[{ label: "Pipeline", href: "/pipeline" }, { label: id }]}
      >
        <Loading
          label="company"
          stages={[
            "resolving the id against the screened list…",
            "reading the three-axis record…",
            "collecting evidence spans…",
          ]}
        />
      </Shell>
    );
  }

  const c = company.data;
  const criticalFlags = c.integrity.filter((f) => f.severity === "critical");

  // Sections in demo order. A section only appears when it has something in it, EXCEPT
  // the ones the demo always visits — those render an explicit "nothing recorded" so a
  // sparse record reads as sparse rather than as a page that failed to load.
  const sections: Section[] = [
    {
      id: "axes",
      label: "Three axes",
      hint: "Click any axis to open the trace: contributing events → quoted source span → original URL.",
    },
    { id: "history", label: "Score history", hint: "The band narrows as observations land." },
    ...(c.proof_protocol
      ? [
          {
            id: "proof",
            label: "Proof Protocol",
            hint: "Cold start: the system creates evidence rather than penalising its absence.",
          },
        ]
      : []),
    ...(c.integrity.length
      ? [
          {
            id: "integrity",
            label: `Integrity (${c.integrity.length})`,
            hint: "Everything the sanitizer caught, including what it did about it.",
          },
        ]
      : []),
    { id: "claims", label: "Per-claim trust", hint: "One status per claim. No company-level trust number exists." },
    {
      id: "memo",
      label: "Memo | Dissent",
      hint: "The recommendation stays locked until the dissent is opened — enforced by the server.",
    },
  ];

  return (
    <Shell
      title={c.name}
      lede={c.one_liner}
      crumbs={[{ label: "Pipeline", href: "/pipeline" }, { label: c.name }]}
      toolbar={
        <SectionBar sections={sections} onBack={backToPipeline} prev={prev} next={next} />
      }
      right={
        <div className="flex flex-wrap items-center gap-2">
          <GateBadge gate={c.gate} />
          <SourceChip source={company.source} note={company.note} />
        </div>
      }
      meta={
        <>
          {c.archetype}
          <br />
          {c.sector} · {c.stage} · {c.geo}
          <br />
          AS_OF {c.as_of.slice(0, 10)}
        </>
      }
    >
      <div className="space-y-6">
        {company.source === "fixture" && company.note && (
          <ErrorNote
            message={`Live detail unavailable — rendering what we have locally. (${company.note})`}
            onRetry={() => {
              setCompany(null);
              setHistory(null);
              setNotFound(false);
              setReloadKey((k) => k + 1);
            }}
          />
        )}

        {/* Thin is a fact about the record, so it is stated rather than hidden. */}
        {c.coverage === "sparse" && c.coverage_note && (
          <div className="border border-dashed border-[color:var(--rule)] px-4 py-3">
            <div className="meta text-[color:var(--muted)]">Sparse record</div>
            <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
              {c.coverage_note}
            </p>
          </div>
        )}

        {/* Critical integrity findings ride at the top — a caught injection must be findable. */}
        {criticalFlags.length > 0 && (
          <Panel
            title={`⚠ ${criticalFlags.length} critical integrity ${
              criticalFlags.length === 1 ? "finding" : "findings"
            }`}
            subtitle="Caught at ingestion. Surfaced here rather than buried in a log."
            className="border-[var(--signal)]"
          >
            <IntegrityPanel flags={criticalFlags} />
          </Panel>
        )}

        {c.entity_resolution_note && (
          <div
            className="border px-5 py-4"
            style={{
              borderColor: "var(--figure)",
              background: "color-mix(in oklab, var(--figure) 8%, transparent)",
            }}
          >
            <h2 className="meta" style={{ color: "var(--figure)" }}>
              ◍ Entity resolution — disclosed, not assumed
            </h2>
            <p className="mt-1.5 max-w-4xl text-[15px] leading-relaxed text-[color:var(--muted)]">
              {c.entity_resolution_note}
            </p>
          </div>
        )}

        {/* ------------------------------------------------------- the three axes */}
        <section id="axes" className="scroll-mt-32">
          <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
            <h2 className="meta text-[color:var(--figure)]">Three-axis screen</h2>
            <p className="caption max-w-none text-[color:var(--muted)]">
              Click any axis to trace it down to the quoted source span.
            </p>
          </div>
          <div className="grid gap-4 lg:grid-cols-3">
            {AXIS_KEYS.map((k) => (
              <AxisCard key={k} axisKey={k} axis={c.axes[k]} onOpenTrace={setTraceAxis} />
            ))}
          </div>
          <p className="mt-3 max-w-4xl text-[13px] leading-relaxed text-[color:var(--muted)]">
            These three numbers are never averaged, weighted, or combined. A company can be
            strong on market and disqualified on founder, and the page will keep showing you
            both — which is the whole point.
          </p>
        </section>

        {/* --------------------------------------------------- moving line + band */}
        <Panel
          id="history"
          title="Score history — the band tightens as evidence lands"
          subtitle="Local-linear-trend posterior per axis, replayed in observation order."
          className="scroll-mt-32"
          right={history ? <SourceChip source={history.source} note={history.note} /> : undefined}
        >
          {!history ? (
            <Loading label="score history" />
          ) : AXIS_KEYS.some((k) => history.data[k].length) ? (
            <ScoreLine history={history.data} />
          ) : (
            <EmptyState title="No score history recorded for this company.">
              The posterior is only replayable where observations were logged over time.
              This record has none, so there is no line to draw — the axes above are a
              single reading rather than a trajectory.
            </EmptyState>
          )}
        </Panel>

        {/* ----------------------------------------------------- Proof Protocol */}
        {c.proof_protocol && (
          <section id="proof" className="scroll-mt-32">
            <ProofProtocolPanel companyId={c.id} initial={c.proof_protocol} />
          </section>
        )}

        {/* --------------------------------------------------------- integrity */}
        {c.integrity.length > 0 && (
          <Panel
            id="integrity"
            title="Integrity flags"
            subtitle="Everything the sanitizer caught, including what it did about it."
            className="scroll-mt-32"
          >
            <IntegrityPanel flags={c.integrity} />
          </Panel>
        )}

        {/* ------------------------------------------------------ per-claim trust */}
        <Panel
          id="claims"
          title="Per-claim trust"
          subtitle="One status per claim. No company-level trust number exists."
          className="scroll-mt-32"
        >
          {c.claims.length ? (
            <ClaimsTable claims={c.claims} />
          ) : (
            <EmptyState title="No claims were extracted for this company.">
              Nothing was asserted that the validator could take a position on. An empty
              claims table is a statement that there is nothing to verify, not a failure
              to verify.
            </EmptyState>
          )}
        </Panel>

        {/* ---------------------------------------------------- memo | dissent */}
        <section id="memo" className="scroll-mt-32">
          <h2 className="meta mb-3 text-[color:var(--figure)]">Memo | Dissent</h2>
          <MemoDissent companyId={id} />
        </section>

        <TraceDrawer company={c} axisKey={traceAxis} onClose={() => setTraceAxis(null)} />
      </div>
    </Shell>
  );
}

export default function CompanyPage({
  params,
}: {
  params: Promise<{ id: string }>;
}) {
  const { id } = use(params);
  return <CompanyView key={id} id={id} />;
}
