"use client";

/**
 * Route `/company/[id]` — the workhorse.
 *
 * Three axes side by side · moving score line with a tightening band · trace
 * drill-down to a quoted span · per-claim trust · integrity flags · Proof Protocol ·
 * memo | dissent split with the server-side recommendation lock.
 *
 * There is no combined score on this page and no component here can produce one.
 */

import Link from "next/link";
import { use, useEffect, useState } from "react";
import { getCompany, getScoreHistory, type Result } from "@/lib/api";
import type { AxisKey, CompanyDetail, ScoreHistory } from "@/lib/types";
import { AXIS_KEYS } from "@/lib/types";
import AxisCard from "@/components/AxisCard";
import ClaimsTable from "@/components/ClaimsTable";
import IntegrityPanel from "@/components/IntegrityPanel";
import MemoDissent from "@/components/MemoDissent";
import ProofProtocolPanel from "@/components/ProofProtocolPanel";
import ScoreLine from "@/components/ScoreLine";
import Shell from "@/components/Shell";
import TraceDrawer from "@/components/TraceDrawer";
import { ErrorNote, GateBadge, Loading, Panel, SourceChip } from "@/components/ui";

function Anchors({ items }: { items: { id: string; label: string }[] }) {
  return (
    <nav className="flex flex-wrap gap-2" aria-label="Sections">
      {items.map((i) => (
        <a
          key={i.id}
          href={`#${i.id}`}
          className="border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-1.5 text-[13px] font-semibold text-[color:var(--muted)] transition hover:border-[color:var(--figure)] hover:text-[color:var(--figure)]"
        >
          {i.label}
        </a>
      ))}
    </nav>
  );
}

/**
 * Keyed by company id below, so navigating between companies remounts the whole view.
 * That is what guarantees an unlocked recommendation never leaks across companies.
 */
function CompanyView({ id }: { id: string }) {
  const [company, setCompany] = useState<Result<CompanyDetail> | null>(null);
  const [history, setHistory] = useState<Result<ScoreHistory> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [traceAxis, setTraceAxis] = useState<AxisKey | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const c = await getCompany(id);
        if (!live) return;
        setCompany(c);
        const h = await getScoreHistory(id, c.data.score_history);
        if (live) setHistory(h);
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      live = false;
    };
  }, [id]);

  if (error && !company) {
    return (
      <Shell title="company">
        <ErrorNote message={error} />
      </Shell>
    );
  }
  if (!company) {
    return (
      <Shell title="company">
        <Loading label="company" />
      </Shell>
    );
  }

  const c = company.data;
  const criticalFlags = c.integrity.filter((f) => f.severity === "critical");

  const anchors = [
    { id: "axes", label: "Three axes" },
    { id: "history", label: "Score history" },
    ...(c.proof_protocol ? [{ id: "proof", label: "Proof Protocol" }] : []),
    ...(c.integrity.length ? [{ id: "integrity", label: `Integrity (${c.integrity.length})` }] : []),
    { id: "claims", label: "Per-claim trust" },
    { id: "memo", label: "Memo | Dissent" },
  ];

  return (
    <Shell
      title={c.name}
      lede={c.one_liner}
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
        <div className="flex flex-wrap items-center justify-between gap-3">
          <Anchors items={anchors} />
          <Link href="/pipeline" className="meta text-[color:var(--muted)] hover:underline">
            ← Back to pipeline
          </Link>
        </div>

      {company.source === "fixture" && company.note && (
        <ErrorNote message={`Backend unreachable — rendering local fixtures. (${company.note})`} />
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
          <h2
            className="text-[13px] font-medium  uppercase"
            style={{ color: "var(--figure)" }}
          >
            ◍ Entity resolution — disclosed, not assumed
          </h2>
          <p className="mt-1.5 max-w-4xl text-[15px] leading-relaxed text-[color:var(--muted)]">
            {c.entity_resolution_note}
          </p>
        </div>
      )}

      {/* ------------------------------------------------------- the three axes */}
      <section id="axes" className="scroll-mt-20">
        <div className="mb-3 flex flex-wrap items-baseline justify-between gap-3">
          <h2 className="text-[15px] font-medium  text-[color:var(--figure)] uppercase">
            Three-axis screen
          </h2>
          <p className="text-[13px] text-[color:var(--muted)]">
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
        className="scroll-mt-20"
        right={history ? <SourceChip source={history.source} note={history.note} /> : undefined}
      >
        {history ? (
          <ScoreLine history={history.data} />
        ) : (
          <Loading label="score history" />
        )}
      </Panel>

      {/* ----------------------------------------------------- Proof Protocol */}
      {c.proof_protocol && (
        <section id="proof" className="scroll-mt-20">
          <ProofProtocolPanel pp={c.proof_protocol} />
        </section>
      )}

      {/* --------------------------------------------------------- integrity */}
      {c.integrity.length > 0 && (
        <Panel
          id="integrity"
          title="Integrity flags"
          subtitle="Everything the sanitizer caught, including what it did about it."
          className="scroll-mt-20"
        >
          <IntegrityPanel flags={c.integrity} />
        </Panel>
      )}

      {/* ------------------------------------------------------ per-claim trust */}
      <Panel
        id="claims"
        title="Per-claim trust"
        subtitle="One status per claim. No company-level trust number exists."
        className="scroll-mt-20"
      >
        <ClaimsTable claims={c.claims} />
      </Panel>

      {/* ---------------------------------------------------- memo | dissent */}
        <section id="memo" className="scroll-mt-20">
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
