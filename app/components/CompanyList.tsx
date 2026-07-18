"use client";

/**
 * The ranked list, set as a document table.
 *
 * Ranked by an EXPLICIT, stated policy — never by a mean of the axes. The policy
 * is printed above the table and the sort key is selectable, because the honest
 * version of "ranked" is "ranked by this, and here is what this is."
 */

import Link from "next/link";
import { useState } from "react";
import type { AxisKey, CompanySummary } from "@/lib/types";
import { AXIS_INDEX, AXIS_KEYS, AXIS_LABEL } from "@/lib/types";
import { GateBadge, Trend } from "./ui";

type SortKey = AxisKey | "momentum" | "certainty";

const GATE_RANK: Record<CompanySummary["gate"], number> = {
  proceed: 0,
  proof_protocol: 1,
  no_call: 2,
};

const SORT_LABEL: Record<SortKey, string> = {
  founder: "Founder axis",
  market: "Market axis",
  idea_vs_market: "Idea-vs-Market axis",
  momentum: "Founder momentum (trend)",
  certainty: "Narrowest founder band",
};

const SORT_POLICY: Record<SortKey, string> = {
  founder: "gate, then founder score descending. No axis is combined with another.",
  market: "gate, then market score descending. No axis is combined with another.",
  idea_vs_market:
    "gate, then idea-vs-market score descending. No axis is combined with another.",
  momentum: "gate, then founder trend descending — who is moving, not who is highest.",
  certainty:
    "gate, then narrowest founder band first — who we know most about, not who scores best.",
};

function AxisCell({ k, c }: { k: AxisKey; c: CompanySummary }) {
  const a = c.axes[k];
  const lo = Math.max(0, a.score - a.band);
  const hi = Math.min(100, a.score + a.band);
  return (
    <td className="border-l border-[color:var(--rule)] px-3 py-3 align-middle">
      <div className="flex items-baseline gap-2">
        <span className="font-[family-name:var(--font-instrument-serif)] text-[28px] leading-none">
          {a.score.toFixed(0)}
        </span>
        <span className="mono text-[11px] text-[color:var(--muted)]">
          ±{a.band.toFixed(1)}
        </span>
        <Trend value={a.trend} className="!text-[11px]" />
      </div>
      <div className="relative mt-1.5 h-[4px] w-full min-w-[104px] bg-[color:var(--ink-09)]">
        <span
          className="absolute top-0 h-[4px] bg-[color:var(--accent)] opacity-30"
          style={{ left: `${lo}%`, width: `${Math.max(1, hi - lo)}%` }}
        />
        <span
          className="absolute top-[-2px] h-[8px] w-[2px] bg-[color:var(--accent)]"
          style={{ left: `calc(${a.score}% - 1px)` }}
        />
      </div>
    </td>
  );
}

export default function CompanyList({
  companies,
  highlight,
}: {
  companies: CompanySummary[];
  /** Ids matched by the active NL query. Non-matches are dimmed, not removed. */
  highlight?: Set<string> | null;
}) {
  const [sort, setSort] = useState<SortKey>("founder");

  const sorted = [...companies].sort((a, b) => {
    const g = GATE_RANK[a.gate] - GATE_RANK[b.gate];
    if (g !== 0) return g;
    if (sort === "momentum") return b.axes.founder.trend - a.axes.founder.trend;
    if (sort === "certainty") return a.axes.founder.band - b.axes.founder.band;
    return b.axes[sort].score - a.axes[sort].score;
  });

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center justify-between gap-3">
        <p className="caption max-w-[70ch] text-[color:var(--muted)]">
          <span className="meta text-[color:var(--figure)]">RANK POLICY</span>{" "}
          {SORT_POLICY[sort]} There is no blended score to rank by, so the ranking states
          which single axis it used.
        </p>
        <label className="meta flex items-center gap-2 text-[color:var(--muted)]">
          Rank by
          <select
            value={sort}
            onChange={(e) => setSort(e.target.value as SortKey)}
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-1.5 text-[13px] text-[color:var(--figure)] normal-case"
          >
            {(Object.keys(SORT_LABEL) as SortKey[]).map((k) => (
              <option key={k} value={k}>
                {SORT_LABEL[k]}
              </option>
            ))}
          </select>
        </label>
      </div>

      <div className="overflow-x-auto border border-[color:var(--rule)]">
        <table className="w-full min-w-[1060px] border-collapse">
          <thead>
            <tr className="border-b border-[color:var(--figure)] text-left">
              <th className="meta px-4 py-2.5 text-[color:var(--muted)]">Company</th>
              {AXIS_KEYS.map((k) => (
                <th
                  key={k}
                  className="meta border-l border-[color:var(--rule)] px-3 py-2.5 text-[color:var(--figure)]"
                >
                  <span className="text-[color:var(--muted)]">{AXIS_INDEX[k]}</span>{" "}
                  {AXIS_LABEL[k]}
                </th>
              ))}
              <th className="meta border-l border-[color:var(--rule)] px-3 py-2.5 text-[color:var(--muted)]">
                Gate
              </th>
              <th className="meta border-l border-[color:var(--rule)] px-4 py-2.5 text-[color:var(--muted)]">
                Flags
              </th>
            </tr>
          </thead>
          <tbody>
            {sorted.map((c) => {
              const dim = highlight ? !highlight.has(c.id) : false;
              return (
                <tr
                  key={c.id}
                  className={`border-b border-[color:var(--rule)] last:border-b-0 hover:bg-[color:var(--ink-09)] ${
                    dim ? "opacity-30" : ""
                  }`}
                >
                  <td className="px-4 py-3">
                    <Link href={`/company/${c.id}`} className="group block">
                      <span className="font-[family-name:var(--font-instrument-serif)] text-[24px] leading-tight group-hover:underline">
                        {c.name}
                      </span>
                      <span className="caption mt-0.5 block max-w-[380px] text-[color:var(--muted)]">
                        {c.one_liner}
                      </span>
                      <span className="meta mt-1 block text-[color:var(--muted)]">
                        {c.archetype} · {c.sector} · {c.stage} · {c.geo}
                      </span>
                    </Link>
                  </td>
                  {AXIS_KEYS.map((k) => (
                    <AxisCell key={k} k={k} c={c} />
                  ))}
                  <td className="border-l border-[color:var(--rule)] px-3 py-3">
                    <GateBadge gate={c.gate} />
                  </td>
                  <td className="border-l border-[color:var(--rule)] px-4 py-3">
                    {c.flag_count > 0 ? (
                      <span
                        className="meta inline-flex items-center gap-1.5 border px-2 py-1"
                        // A flag COUNT aggregates OCR warnings and name merges as well
                        // as injections, so it is not automatically an integrity
                        // verdict. --signal stays reserved for the actual critical
                        // findings, which the company page surfaces individually.
                        style={{ color: "var(--figure)", borderColor: "var(--figure)" }}
                      >
                        <span aria-hidden>⚠</span>
                        {c.flag_count}
                      </span>
                    ) : (
                      <span className="meta text-[color:var(--muted)]">none</span>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </div>
  );
}
