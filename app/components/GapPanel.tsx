"use client";

/**
 * The stated-vs-revealed gap (§2.3) — the most defensible thing in this feature.
 *
 * It is a finding about the USER, produced by the machinery that produces findings about
 * founders, and it is only possible because stated preference (the survey) and revealed
 * preference (the uploaded decisions) are stored and derived separately (§0). So it gets
 * the same furniture a founder finding gets: the claim, both sides it was computed from,
 * the magnitude, the confidence, and the provenance underneath.
 *
 * TWO RULES.
 *
 * `uncomputable` is rendered as loudly as a finding. A gap report that showed only what
 * it could compare would read as agreement on every dimension it lacked data for — a
 * fabrication about the user, and exactly the failure the NOT_ATTEMPTED claim status
 * exists to prevent on the founder side. It gets the hatched treatment for the same
 * reason: a gap we could not look into must look different from one we looked into and
 * found nothing.
 *
 * Confidence travels with every finding and is never rounded away. A divergence computed
 * from three investments is a real divergence computed from three investments, and the
 * number is what stops it being read as a verdict.
 */

import { useCallback, useEffect, useState } from "react";
import { getGap, type GapFinding, type GapReport, type Provenance } from "@/lib/vc";
import { EmptyState, ErrorNote, Loading, Panel } from "@/components/ui";

function ProvenanceLine({ p }: { p: Provenance }) {
  const bits = [
    p.basis,
    p.question_ids.length ? `${p.question_ids.length} question(s)` : null,
    p.decision_rows.length ? `rows ${p.decision_rows.join(", ")}` : null,
    `n=${p.n}`,
  ].filter(Boolean);
  return (
    <div className="meta mt-2 text-[color:var(--muted)]">
      {bits.join(" · ")}
      <span className="mt-1 block normal-case tracking-normal">{p.method}</span>
    </div>
  );
}

/** Magnitude and confidence as hairline rules — the same furniture as the score bars,
 *  and rule-work rather than a sixth hue. */
function Bar({ label, value }: { label: string; value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  return (
    <div>
      <div className="meta flex items-baseline justify-between text-[color:var(--muted)]">
        <span>{label}</span>
        <span className="mono">{value.toFixed(3)}</span>
      </div>
      <div className="mt-1 h-[3px] w-full bg-[color:var(--ink-09)]">
        <span
          className="block h-[3px] bg-[color:var(--accent)]"
          style={{ width: `${pct}%`, transition: "width 900ms var(--expo-out)" }}
        />
      </div>
    </div>
  );
}

function Finding({ f }: { f: GapFinding }) {
  return (
    <li className="border border-[color:var(--accent)] px-4 py-4">
      <div className="meta text-[color:var(--muted)]">
        {f.dimension.replace(/_/g, " ").toUpperCase()}
      </div>
      <p className="body-t mt-1.5 max-w-[70ch]">{f.finding}</p>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <div className="border border-[color:var(--rule)] px-3 py-2">
          <div className="meta text-[color:var(--muted)]">STATED — WHAT YOU ANSWERED</div>
          <div className="mono mt-1 text-[14px]">{f.stated}</div>
        </div>
        <div className="border border-[color:var(--rule)] px-3 py-2">
          <div className="meta text-[color:var(--muted)]">REVEALED — WHAT YOU DID</div>
          <div className="mono mt-1 text-[14px]">{f.revealed}</div>
        </div>
      </div>

      <div className="mt-4 grid gap-3 sm:grid-cols-2">
        <Bar label="MAGNITUDE" value={f.magnitude} />
        <Bar label="CONFIDENCE" value={f.confidence} />
      </div>
      {f.confidence < 0.35 && (
        <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
          Thin evidence. The divergence is real but it rests on{" "}
          {f.provenance.n} observation(s) — read it as a question to ask yourself, not a
          verdict about you.
        </p>
      )}
      <ProvenanceLine p={f.provenance} />
    </li>
  );
}

export default function GapPanel({ reloadKey = 0 }: { reloadKey?: number }) {
  const [report, setReport] = useState<GapReport | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** State is written only after the await, so no effect body sets state synchronously. */
  const load = useCallback(async () => {
    const r = await getGap();
    if (!r.ok) {
      setError(
        r.status === 401
          ? "the gap compares your answers with your decisions — sign in to compute one"
          : `GET /profile/gap: ${r.error}`,
      );
      return;
    }
    setError(null);
    setReport(r.data);
  }, []);

  useEffect(() => {
    void (async () => {
      await load();
    })();
  }, [load, reloadKey]);

  if (error) {
    return (
      <Panel title="stated vs revealed">
        <ErrorNote message={error} onRetry={() => void load()} />
      </Panel>
    );
  }
  if (!report) return <Loading label="the stated-vs-revealed gap" />;

  return (
    <Panel
      title="stated vs revealed"
      subtitle="Computed independently from the survey and from the uploaded decisions, then compared. This is a finding about you, produced by the same machinery that produces findings about founders."
      emphasis={report.findings.length > 0}
    >
      {report.findings.length > 0 ? (
        <ul className="grid gap-4">
          {report.findings.map((f) => (
            <Finding key={f.dimension} f={f} />
          ))}
        </ul>
      ) : (
        <EmptyState title="no divergence could be computed yet">
          A finding needs BOTH sides: answered trade-offs and uploaded decisions on the
          same dimension. Nothing here is an agreement — the dimensions we could not
          compare are listed below with the side that was missing.
        </EmptyState>
      )}

      {report.agreements.length > 0 && (
        <div className="mt-6 border-t border-[color:var(--rule)] pt-4">
          <h3 className="meta text-[color:var(--figure)]">
            WHERE THEY AGREE — CONFIRMATION, NOT A FINDING
          </h3>
          <ul className="mt-2 grid gap-1.5">
            {report.agreements.map((a) => (
              <li key={a} className="caption max-w-none text-[color:var(--muted)]">
                {a}
              </li>
            ))}
          </ul>
        </div>
      )}

      {report.uncomputable.length > 0 && (
        <div className="mt-6 border-t border-[color:var(--rule)] pt-4">
          <h3 className="meta text-[color:var(--figure)]">
            COULD NOT BE COMPARED — NOT AGREEMENT
          </h3>
          <ul className="mt-2 grid gap-2">
            {report.uncomputable.map((u) => (
              <li
                key={u.dimension}
                className="hatch border border-dashed border-[color:var(--muted)] px-3 py-2"
              >
                <div className="meta text-[color:var(--figure)]">
                  {u.dimension.replace(/_/g, " ").toUpperCase()} · MISSING:{" "}
                  {u.missing.toUpperCase()}
                </div>
                <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
                  {u.reason}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}

      <p className="meta mt-6 border-t border-[color:var(--rule)] pt-3 text-[color:var(--muted)]">
        COMPUTED {new Date(report.computed_at).toISOString().replace("T", " ").slice(0, 19)} UTC
      </p>
    </Panel>
  );
}
