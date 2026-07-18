"use client";

/**
 * Route `/` — the opening surface, built as a true plate sequence.
 *
 * Six plates, every one different in ground, marker, density or presence (§5).
 * Plate 05 has no visual at all — that is a deliberate design act and it carries
 * the most candid copy. Plate 06 re-runs plate 01's generator at a raised
 * threshold, so the closing image is literally what survived the opening one.
 *
 * The governing idea is ours literally, not by analogy: a dense field of
 * thousands of builders resolves to the few who clear the evidence threshold.
 * The dashboard itself lives at /pipeline.
 */

import Link from "next/link";
import { useEffect, useState } from "react";
import { getCompanies } from "@/lib/api";
import type { CompanySummary } from "@/lib/types";
import HeatField from "@/components/HeatField";
import NodeField, { type SchematicNode } from "@/components/NodeField";
import { Poster, Registration, Stub } from "@/components/Poster";
import Reveal from "@/components/Reveal";

function toNodes(companies: CompanySummary[]): SchematicNode[] {
  return companies.map((c) => ({
    id: c.id,
    // Real: total contributing evidence events across the three axes.
    weight:
      c.axes.founder.evidence_event_ids.length +
      c.axes.market.evidence_event_ids.length +
      c.axes.idea_vs_market.evidence_event_ids.length,
    emphasis:
      c.gate === "proceed" ? "high" : c.gate === "proof_protocol" ? "mid" : "low",
    group: c.sector,
  }));
}

/**
 * §1.1 — if there is no data we render the empty state honestly rather than
 * falling back to a pretty fake. A decorative constellation captioned as if it
 * were the graph is the exact failure the doc warns about.
 */
function Schematic({
  nodes,
  loading,
  ...rest
}: {
  nodes: SchematicNode[];
  loading: boolean;
} & Omit<React.ComponentProps<typeof NodeField>, "nodes">) {
  if (loading) {
    return (
      <div className="flex h-full min-h-[26svh] items-end">
        <p className="meta text-[color:var(--muted)]">Loading screen records…</p>
      </div>
    );
  }
  if (nodes.length === 0) {
    return (
      <div className="flex h-full min-h-[26svh] items-end">
        <p className="caption text-[color:var(--muted)]">
          No records returned. Nothing is drawn here rather than a diagram standing in
          for data we do not have.
        </p>
      </div>
    );
  }
  return <NodeField nodes={nodes} {...rest} />;
}

export default function Pitch() {
  const [companies, setCompanies] = useState<CompanySummary[] | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      const r = await getCompanies();
      if (live) setCompanies(r.data);
    })();
    return () => {
      live = false;
    };
  }, []);

  const nodes = companies ? toNodes(companies) : [];
  const loading = companies === null;
  const n = companies?.length ?? 0;
  const cleared = companies?.filter((c) => c.gate === "proceed").length ?? 0;

  return (
    <div>
      {/* ================================================== PLATE 01 — paper */}
      <Poster
        ground="paper"
        captions={
          <p>
            Field study, plate one. Density plot sampled continuously on a nine-pixel
            lattice. Banding is quantised; intermediate values are not rendered. The
            pointer acts as a heat source.
          </p>
        }
        meta={
          <>
            VC BRAIN
            <br />
            SAN FRANCISCO, CALIFORNIA
            <br />
            PLATE 01 / 06
          </>
        }
        visual={
          <div className="absolute inset-0">
            <HeatField scrollStarve />
          </div>
        }
        stub={
          <Reveal quiet delay={120}>
            <div className="stub">
              <Stub label="Screen" value={loading ? "—" : `${n} companies`} />
              <Stub label="Axes" value="Founder · Market · Idea-vs-Market" />
              <Stub label="Blended score" value="None. Ever." />
              <Stub
                label="Dashboard"
                value={
                  <Link href="/pipeline" className="underline underline-offset-4">
                    /pipeline
                  </Link>
                }
              />
            </div>
          </Reveal>
        }
      >
        <Reveal>
          <h1 className="display">
            thousands build. <em>few are ever seen.</em>
          </h1>
        </Reveal>
      </Poster>

      {/* ================================================= PLATE 02 — cobalt */}
      <Poster
        ground="cobalt"
        captions={
          <p>
            Schematic, plate two. One node per company currently in the screen. Cluster
            membership is sector; marker radius is the count of contributing evidence
            events; filled markers cleared the gate. Within-cluster position is seeded
            and carries no ordinal meaning. Records shown are seeded demonstration
            companies, not real firms, and no node is labelled with a person.
          </p>
        }
        meta={
          <>
            SCHEMATIC
            <br />
            {loading ? "LOADING" : `${n} NODES`}
            <br />
            PLATE 02 / 06
          </>
        }
        visual={
          <div className="absolute inset-0">
            <Schematic
              nodes={nodes}
              loading={loading}
              marker="dot"
              edgeStyle="straight"
              neighbours={2}
              seed={20260718}
            />
          </div>
        }
      >
        <Reveal>
          <h2 className="quiet">
            we score what you did. <em>not where you were.</em>
          </h2>
        </Reveal>
      </Poster>

      {/* ========================================= PLATE 03 — paper, reading */}
      <Poster
        ground="paper"
        captions={
          <p>
            Reading plate, plate three. No diagram. Registration bar shown for scale
            reference only.
          </p>
        }
        meta={
          <>
            METHOD
            <br />
            THREE AXES
            <br />
            PLATE 03 / 06
          </>
        }
        visual={
          <div className="flex h-full items-end px-[clamp(1.5rem,2.6vw,2.75rem)]">
            <Reveal quiet className="w-full">
              <Registration />
            </Reveal>
          </div>
        }
      >
        <Reveal>
          <h2 className="quiet mb-8">
            three scores, never one. <em>a founder is not a market.</em>
          </h2>
        </Reveal>
        <div className="grid gap-x-10 gap-y-6 md:grid-cols-3">
          {[
            {
              k: "01",
              t: "Founder",
              d: "Built from behaviour in public artefacts — sustained authorship, how a bug report is handled, whether a promise ships. Never from school, employer, or investor.",
            },
            {
              k: "02",
              t: "Market",
              d: "Scored only from sources independent of the founder. A market number resting on the deck's own slide is reported as an absence of evidence, not as a 64.",
            },
            {
              k: "03",
              t: "Idea-vs-Market",
              d: "Whether the thing being built is the thing the market is short of. Held apart from the other two so a strong builder in a dead market still reads as one.",
            },
          ].map((c, i) => (
            <Reveal key={c.k} quiet delay={i * 60}>
              <div className="border-t border-[color:var(--rule)] pt-3">
                <div className="meta text-[color:var(--muted)]">AXIS {c.k}</div>
                <h3 className="mono mt-1 text-[15px] font-medium">{c.t}</h3>
                <p className="caption mt-2 max-w-none text-[color:var(--muted)]">{c.d}</p>
              </div>
            </Reveal>
          ))}
        </div>
      </Poster>

      {/* =========================================== PLATE 04 — ink, arced */}
      <Poster
        ground="ink"
        captions={
          <p>
            Schematic, plate four. Same records as plate two, redrawn: outlined square
            markers, quadratic edges, three-neighbour linkage. Inverted ground. Depth is
            carried by scale and alpha only; nothing here is shaded.
          </p>
        }
        meta={
          <>
            TRACE
            <br />
            EVIDENCE → SPAN
            <br />
            PLATE 04 / 06
          </>
        }
        visual={
          <div className="absolute inset-0">
            <Schematic
              nodes={nodes}
              loading={loading}
              marker="square"
              edgeStyle="arc"
              neighbours={3}
              seed={834211}
            />
          </div>
        }
      >
        <Reveal>
          <h2 className="quiet">
            every number opens. <em>down to the quoted line.</em>
          </h2>
        </Reveal>
      </Poster>

      {/* ================================= PLATE 05 — paper, SILENT (no visual) */}
      <Poster
        ground="paper"
        captions={<p>Plate five. No diagram on this plate.</p>}
        meta={
          <>
            GAPS
            <br />
            NOT ATTEMPTED
            <br />
            PLATE 05 / 06
          </>
        }
      >
        <Reveal>
          <h2 className="quiet mb-10">
            we show what we skipped. <em>completeness is the tell.</em>
          </h2>
        </Reveal>
        <Reveal quiet delay={60}>
          <div className="grid max-w-4xl gap-6 md:grid-cols-2">
            <p className="body-t">
              Every claim carries its own state: verified, contradicted, unverifiable, or
              not attempted. There is no company-level trust number to average them into,
              because averaging is how a single unchecked assertion disappears.
            </p>
            <p className="body-t text-[color:var(--muted)]">
              &ldquo;Not attempted&rdquo; means we did not look. It is rendered as loudly
              as a pass, with the reason attached. The recommendation stays locked until
              the dissent has been opened — enforced server-side, not by the interface.
            </p>
          </div>
        </Reveal>
      </Poster>

      {/* ================================ PLATE 06 — cobalt, field starved */}
      <Poster
        ground="cobalt"
        captions={
          <p>
            Field study, plate six. Same generator as plate one, threshold raised.
            Painted coverage falls to under a fifth. The surviving cells are that field
            at the higher cut, not a second drawing.
          </p>
        }
        meta={
          <>
            RESULT
            <br />
            {loading ? "LOADING" : `${cleared} OF ${n} CLEARED`}
            <br />
            PLATE 06 / 06
          </>
        }
        visual={
          <div className="absolute inset-0">
            <HeatField starve={0.34} />
          </div>
        }
        stub={
          <Reveal quiet delay={120}>
            <div className="stub">
              <Stub
                label="Pipeline"
                value={
                  <Link href="/pipeline" className="underline underline-offset-4">
                    Ranked list, thesis, query
                  </Link>
                }
              />
              <Stub
                label="Backtest"
                value={
                  <Link href="/backtest" className="underline underline-offset-4">
                    Calibration &amp; fame check
                  </Link>
                }
              />
              <Stub label="Lookahead violations" value="0 of 1,284 replayed events" />
              <Stub label="Status" value="Seeded demonstration records" />
            </div>
          </Reveal>
        }
      >
        <Reveal>
          <h2 className="quiet">
            the field is the argument. <em>what remains is the list.</em>
          </h2>
        </Reveal>
      </Poster>
    </div>
  );
}
