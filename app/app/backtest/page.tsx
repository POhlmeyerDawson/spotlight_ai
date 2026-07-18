"use client";

/**
 * Route `/backtest` — the calibration report.
 *
 * Winners rising vs controls flat and below the threshold, the hit rate, the
 * no-lookahead assertion, and one failure the system correctly deprioritized. The
 * `fame_check_passed` status is the H12 gate and gets top billing: if controls clear
 * the threshold, the score is measuring fame and everything else on this page is void.
 */

import { useEffect, useState } from "react";
import { getBacktest, type Result } from "@/lib/api";
import type { Backtest } from "@/lib/types";
import BacktestChart from "@/components/BacktestChart";
import Shell from "@/components/Shell";
import { ErrorNote, Loading, Panel, SourceChip, Stat } from "@/components/ui";

export default function BacktestPage() {
  const [bt, setBt] = useState<Result<Backtest> | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const r = await getBacktest();
        if (live) setBt(r);
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      live = false;
    };
  }, []);

  if (error && !bt) {
    return (
      <Shell title="backtest">
        <ErrorNote message={error} />
      </Shell>
    );
  }
  if (!bt) {
    return (
      <Shell title="backtest">
        <Loading label="backtest" />
      </Shell>
    );
  }

  const b = bt.data;
  const highestControl = Math.max(
    ...b.trajectories
      .filter((t) => t.label === "control")
      .map((t) => t.points[t.points.length - 1].mu),
  );
  const clearedWinners = b.trajectories.filter(
    (t) => t.label === "winner" && t.points[t.points.length - 1].mu >= b.threshold,
  ).length;

  return (
    <Shell
      title="backtest & calibration"
      lede={
        <>
          Historical sources truncated to{" "}
          <code className="mono">{b.as_of.slice(0, 10)}</code>, replayed through the same
          code path as live. No special backtest mode — if it needed one, it would not be a
          backtest.
        </>
      }
      right={<SourceChip source={bt.source} note={bt.note} />}
      meta={
        <>
          REPLAY
          <br />
          {b.n_winners} WINNERS · {b.n_controls} CONTROLS
          <br />
          AS_OF {b.as_of.slice(0, 10)}
        </>
      }
    >
      <div className="space-y-6">
      {bt.source === "fixture" && bt.note && (
        <ErrorNote message={`Backend unreachable — rendering local fixtures. (${bt.note})`} />
      )}

      {/* ------------------------------------------------- the H12 fame gate */}
      <section
        className="border-2 px-5 py-4"
        style={{
          borderColor: b.fame_check_passed ? "var(--accent)" : "var(--signal)",
          background: b.fame_check_passed
            ? "color-mix(in oklab, var(--accent) 9%, transparent)"
            : "color-mix(in oklab, var(--signal) 12%, transparent)",
        }}
      >
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="min-w-0 flex-1">
            <h2 className="text-[13px] font-medium  text-[color:var(--muted)] uppercase">
              H12 hard gate · fame vs trajectory
            </h2>
            <p
              className="mt-1 flex items-center gap-3 text-[30px] leading-tight font-medium"
              style={{ color: b.fame_check_passed ? "var(--accent)" : "var(--signal)" }}
            >
              <span aria-hidden>{b.fame_check_passed ? "✓" : "✕"}</span>
              fame_check_passed = {String(b.fame_check_passed)}
            </p>
            <p className="mt-2 max-w-4xl text-[15px] leading-relaxed text-[color:var(--muted)]">
              {b.fame_check_detail}
            </p>
          </div>
          <div className="shrink-0">
            <Stat
              label="Highest control"
              value={highestControl.toFixed(1)}
              sub={`threshold is ${b.threshold} — controls stay under it`}
              color={highestControl >= b.threshold ? "var(--figure)" : "var(--accent)"}
            />
          </div>
        </div>
      </section>

      <div className="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
        <Stat
          label="Hit rate"
          value={`${(b.hit_rate * 100).toFixed(0)}%`}
          sub={`${clearedWinners} of ${b.n_winners} winners cleared ${b.threshold}`}
        />
        <Stat label="Winners replayed" value={b.n_winners} sub="pre-breakout sources only" />
        <Stat
          label="Matched controls"
          value={b.n_controls}
          sub="same era, comparable footprint, no breakout"
        />
        <Stat
          label="Lookahead violations"
          value={b.lookahead_assertion.violations}
          sub={`across ${b.lookahead_assertion.events_checked.toLocaleString()} replayed events`}
          color={b.lookahead_assertion.violations === 0 ? "var(--accent)" : "var(--figure)"}
        />
      </div>

      <Panel
        title="Score trajectories"
        subtitle="Winners rise. Controls stay flat and below the line. That separation is the entire claim."
      >
        <BacktestChart trajectories={b.trajectories} threshold={b.threshold} />
      </Panel>

      <div className="grid gap-4 lg:grid-cols-2">
        {/* ------------------------------------- the failure we got right */}
        <Panel
          title="One failure the system correctly deprioritized"
          subtitle="The most credible thing on this page, and it costs nothing to show."
        >
          <div className="flex flex-wrap items-baseline gap-4">
            <span className="text-[24px] font-medium text-[color:var(--figure)]">
              {b.correctly_deprioritized.name}
            </span>
            <span className="mono text-[24px] font-medium" style={{ color: "var(--accent)" }}>
              scored {b.correctly_deprioritized.final_score}
            </span>
            <span className="text-[14px] text-[color:var(--muted)]">
              vs threshold {b.threshold} — correctly below
            </span>
          </div>
          <p className="mt-3 text-[15px] leading-relaxed text-[color:var(--muted)]">
            {b.correctly_deprioritized.why}
          </p>
          <p
            className="mt-3 border-l-4 px-4 py-3 text-[15px] leading-relaxed text-[color:var(--figure)]"
            style={{
              borderColor: "var(--accent)",
              background: "color-mix(in oklab, var(--accent) 8%, transparent)",
            }}
          >
            {b.correctly_deprioritized.outcome}
          </p>
        </Panel>

        <div className="space-y-4">
          <Panel
            title="No-lookahead assertion"
            subtitle="What makes the claim credible rather than asserted."
          >
            <p className="text-[15px] leading-relaxed text-[color:var(--muted)]">
              {b.lookahead_assertion.detail}
            </p>
            <div className="mono mt-3 grid grid-cols-2 gap-3">
              <Stat
                label="Events replayed"
                value={b.lookahead_assertion.events_checked.toLocaleString()}
              />
              <Stat
                label="observed_at &gt; as_of"
                value={b.lookahead_assertion.violations}
                color={
                  b.lookahead_assertion.violations === 0 ? "var(--accent)" : "var(--figure)"
                }
              />
            </div>
          </Panel>

          <Panel title="Source truncation">
            <p className="text-[15px] leading-relaxed text-[color:var(--muted)]">{b.truncation_note}</p>
          </Panel>
        </div>
      </div>

      {/* --------------------------------------------- misses, stated plainly */}
      <Panel
        title="Winners we missed"
        subtitle="Reported next to the hit rate rather than under it."
      >
        {b.trajectories.filter(
          (t) => t.label === "winner" && t.points[t.points.length - 1].mu < b.threshold,
        ).length === 0 ? (
          <p className="text-[15px] text-[color:var(--muted)]">
            Every replayed winner cleared the threshold at <code>as_of</code>.
          </p>
        ) : (
          <ul className="space-y-2">
            {b.trajectories
              .filter(
                (t) => t.label === "winner" && t.points[t.points.length - 1].mu < b.threshold,
              )
              .map((t) => (
                <li
                  key={t.id}
                  className="flex flex-wrap items-baseline gap-3 border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-4 py-3"
                >
                  <span className="text-[17px] font-medium text-[color:var(--figure)]">{t.name}</span>
                  <span className="mono text-[17px] font-medium" style={{ color: "var(--figure)" }}>
                    {t.points[t.points.length - 1].mu.toFixed(1)}
                  </span>
                  <span className="text-[14px] text-[color:var(--muted)]">
                    below the {b.threshold} threshold · actual outcome: {t.outcome}
                  </span>
                </li>
              ))}
          </ul>
        )}
      </Panel>
      </div>
    </Shell>
  );
}
