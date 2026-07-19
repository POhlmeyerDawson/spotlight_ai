"use client";

/**
 * The ranked list, set as a document table.
 *
 * Ranked by an EXPLICIT, stated policy — never by a mean of the axes. The policy
 * is printed above the table and the sort key is selectable, because the honest
 * version of "ranked" is "ranked by this, and here is what this is."
 *
 * Navigation contract, one obvious click each way:
 *   - the whole row opens the company (not just the name);
 *   - j/k or ↑/↓ move the cursor, Enter opens it, so the list is drivable without a mouse;
 *   - the ranked order and the cursor are handed to the company page, which offers
 *     prev/next against the same order and returns you here with it intact.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { AxisKey, CompanySummary } from "@/lib/types";
import { AXIS_INDEX, AXIS_KEYS, AXIS_LABEL } from "@/lib/types";
import { writeListState } from "@/lib/listState";
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

const isSortKey = (v: string): v is SortKey => v in SORT_LABEL;

/**
 * A null score sorts LAST regardless of direction. An axis with nothing to score is an
 * absence, and an absence must never win a ranking by being treated as a zero or as an
 * infinity — it simply cannot be ranked on that axis.
 */
const rank = (v: number | null) => (v === null ? -Infinity : v);

function AxisCell({ k, c }: { k: AxisKey; c: CompanySummary }) {
  const a = c.axes[k];

  // An axis absent from the payload was never computed. Rendering it as "no
  // evidence" would claim we looked and found nothing; it also crashed the row
  // outright when the API served a founder-only summary.
  if (!a) {
    return (
      <td className="border-l border-[color:var(--rule)] px-3 py-3 align-middle">
        <span className="mono text-[13px] text-[color:var(--muted)]">not computed</span>
        <div className="hatch mt-1.5 h-[4px] w-full min-w-[104px] bg-[color:var(--ink-09)]" />
      </td>
    );
  }

  if (a.score === null) {
    return (
      <td className="border-l border-[color:var(--rule)] px-3 py-3 align-middle">
        <span className="mono text-[13px] text-[color:var(--muted)]">
          {a.reason?.startsWith("not computed") ? "not computed" : "no evidence"}
        </span>
        <div className="hatch mt-1.5 h-[4px] w-full min-w-[104px] bg-[color:var(--ink-09)]" />
      </td>
    );
  }

  /**
   * A NULL band is "we did not compute an interval", NOT an interval of zero. `?? 0`
   * printed "±0.0" — a claim of PERFECT CERTAINTY — and drew a zero-width bar, which is
   * the strongest possible claim made on the weakest possible grounds. AxisCard already
   * draws these two states apart; this cell never got the same treatment. No band means
   * no ± figure and no interval overlay, the same absence language used above.
   */
  const hasBand = a.band !== null;
  const band = a.band ?? 0;
  const lo = Math.max(0, a.score - band);
  const hi = Math.min(100, a.score + band);
  return (
    <td className="border-l border-[color:var(--rule)] px-3 py-3 align-middle">
      <div className="flex items-baseline gap-2">
        <span className="font-[family-name:var(--font-instrument-serif)] text-[28px] leading-none">
          {a.score.toFixed(0)}
        </span>
        <span
          className="mono text-[11px] text-[color:var(--muted)]"
          title={hasBand ? undefined : "No interval was computed for this axis."}
        >
          {hasBand ? `±${band.toFixed(1)}` : "± —"}
        </span>
        {a.trend !== null && <Trend value={a.trend} className="!text-[11px]" />}
      </div>
      <div className="relative mt-1.5 h-[4px] w-full min-w-[104px] bg-[color:var(--ink-09)]">
        {hasBand && (
          <span
            className="absolute top-0 h-[4px] bg-[color:var(--accent)] opacity-30"
            style={{ left: `${lo}%`, width: `${Math.max(1, hi - lo)}%` }}
          />
        )}
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
  initialSort = "founder",
  initialSelected = null,
  onSortChange,
  onOrderChange,
}: {
  companies: CompanySummary[];
  /** Ids matched by the active NL query. Non-matches are dimmed, not removed. */
  highlight?: Set<string> | null;
  initialSort?: string;
  /** Row to restore the cursor to — the company you last opened from here. */
  initialSelected?: string | null;
  onSortChange?: (s: string) => void;
  /** Reports the ranked order upward so the company page can walk it. */
  onOrderChange?: (ids: string[]) => void;
}) {
  const router = useRouter();
  const [sort, setSort] = useState<SortKey>(
    isSortKey(initialSort) ? initialSort : "founder",
  );

  const sorted = useMemo(
    () =>
      [...companies].sort((a, b) => {
        const g = GATE_RANK[a.gate] - GATE_RANK[b.gate];
        if (g !== 0) return g;
        // An absent axis is unrankable on that axis, exactly like a null score —
        // `rank` already sends it last rather than letting it win as a zero.
        if (sort === "momentum")
          return rank(b.axes.founder?.trend ?? null) - rank(a.axes.founder?.trend ?? null);
        if (sort === "certainty") {
          // Narrowest band first, so a missing band sorts last rather than first.
          const ab = a.axes.founder?.band ?? Infinity;
          const bb = b.axes.founder?.band ?? Infinity;
          return ab - bb;
        }
        return rank(b.axes[sort]?.score ?? null) - rank(a.axes[sort]?.score ?? null);
      }),
    [companies, sort],
  );

  const order = useMemo(() => sorted.map((c) => c.id), [sorted]);

  // The cursor. Starts on the row you last opened so returning from a company page
  // puts you back exactly where you were, including for keyboard users.
  const [cursor, setCursor] = useState(() => {
    const i = initialSelected ? order.indexOf(initialSelected) : -1;
    return i >= 0 ? i : 0;
  });

  const rowRefs = useRef<(HTMLTableRowElement | null)[]>([]);
  const [keyboardActive, setKeyboardActive] = useState(false);

  useEffect(() => {
    onOrderChange?.(order);
  }, [order, onOrderChange]);

  const open = useCallback(
    (id: string) => {
      writeListState({ selected: id, order, sort, scrollY: window.scrollY });
      router.push(`/company/${encodeURIComponent(id)}`);
    },
    [order, router, sort],
  );

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      // Never steal keys from a field the user is typing in — the compound query box
      // is right above this table and 'j' is a perfectly ordinary character.
      const el = e.target as HTMLElement | null;
      if (
        el &&
        (el.tagName === "INPUT" ||
          el.tagName === "TEXTAREA" ||
          el.tagName === "SELECT" ||
          el.isContentEditable)
      ) {
        return;
      }
      if (e.metaKey || e.ctrlKey || e.altKey) return;
      if (!order.length) return;

      const step = (d: number) => {
        e.preventDefault();
        setKeyboardActive(true);
        setCursor((c) => Math.max(0, Math.min(order.length - 1, c + d)));
      };

      if (e.key === "j" || e.key === "ArrowDown") step(1);
      else if (e.key === "k" || e.key === "ArrowUp") step(-1);
      else if (e.key === "Enter" && keyboardActive) {
        e.preventDefault();
        open(order[cursor]);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cursor, keyboardActive, open, order]);

  useEffect(() => {
    if (!keyboardActive) return;
    rowRefs.current[cursor]?.scrollIntoView({ block: "nearest" });
  }, [cursor, keyboardActive]);

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
            onChange={(e) => {
              const v = e.target.value;
              if (!isSortKey(v)) return;
              setSort(v);
              onSortChange?.(v);
            }}
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

      <p className="meta mb-2 text-[color:var(--muted)]">
        J / K or ↑ ↓ to move · ENTER to open · ESC on a company returns here
      </p>

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
            {sorted.map((c, i) => {
              const dim = highlight ? !highlight.has(c.id) : false;
              const onCursor = keyboardActive && i === cursor;
              return (
                <tr
                  key={c.id}
                  ref={(el) => {
                    rowRefs.current[i] = el;
                  }}
                  onClick={() => open(c.id)}
                  className={`cursor-pointer border-b border-[color:var(--rule)] last:border-b-0 hover:bg-[color:var(--ink-09)] ${
                    dim ? "opacity-30" : ""
                  }`}
                  style={
                    onCursor
                      ? {
                          background: "var(--ink-09)",
                          // A cursor needs to be visible without relying on hue alone.
                          boxShadow: "inset 3px 0 0 0 var(--accent)",
                        }
                      : undefined
                  }
                >
                  <td className="px-4 py-3">
                    <Link
                      href={`/company/${encodeURIComponent(c.id)}`}
                      onClick={(e) => {
                        // A modified click is the reader asking for a new tab or window,
                        // and the browser handles that correctly on its own — swallowing
                        // it here made the only real link on the row behave like a div.
                        if (e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
                        // Otherwise the row handler already navigates and records list
                        // state; letting the link fire too would double-push history.
                        e.preventDefault();
                      }}
                      className="group block"
                    >
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
