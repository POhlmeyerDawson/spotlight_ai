"use client";

/**
 * Calibration chart: winners' trajectories rising vs controls flat and below the
 * threshold. This is proof #1 of the whole pitch, and the visual claim it makes is
 * narrow and checkable — the controls stay under the line.
 *
 * Two groups only, so identity is carried by a legend, direct labels, and the
 * deliberately recessive control color. Hand-written SVG, no charting dependency.
 */

import { useMemo, useState } from "react";
import type { Trajectory } from "@/lib/types";

const W = 900;
const H = 420;
const PAD = { top: 20, right: 150, bottom: 34, left: 44 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

const yScale = (v: number) => PAD.top + PLOT_H * (1 - v / 100);
const xScale = (i: number, n: number) =>
  PAD.left + (n <= 1 ? 0 : (PLOT_W * i) / (n - 1));

export default function BacktestChart({
  trajectories,
  threshold,
}: {
  trajectories: Trajectory[];
  threshold: number;
}) {
  const [hover, setHover] = useState<string | null>(null);

  const { winners, controls, n } = useMemo(
    () => ({
      winners: trajectories.filter((t) => t.label === "winner"),
      controls: trajectories.filter((t) => t.label === "control"),
      n: Math.max(...trajectories.map((t) => t.points.length)),
    }),
    [trajectories],
  );

  const path = (t: Trajectory) =>
    t.points
      .map(
        (p, i) =>
          `${i === 0 ? "M" : "L"}${xScale(i, t.points.length).toFixed(2)},${yScale(p.mu).toFixed(2)}`,
      )
      .join(" ");

  /**
   * Direct labels collide when trajectories finish close together (the controls all
   * land in the 36–47 band by design). Push them apart vertically so every label
   * stays readable while its dot stays on the true value.
   */
  const labelY = useMemo(() => {
    const rows = trajectories
      .map((t) => ({ id: t.id, y: yScale(t.points[t.points.length - 1].mu) }))
      .sort((a, b) => a.y - b.y);
    const MIN_GAP = 16;
    for (let i = 1; i < rows.length; i++) {
      if (rows[i].y - rows[i - 1].y < MIN_GAP) rows[i].y = rows[i - 1].y + MIN_GAP;
    }
    return new Map(rows.map((r) => [r.id, r.y]));
  }, [trajectories]);

  const draw = (t: Trajectory) => {
    const winner = t.label === "winner";
    const color = winner ? "var(--accent)" : "var(--muted)";
    const dim = hover !== null && hover !== t.id;
    const last = t.points[t.points.length - 1];
    const cleared = last.mu >= threshold;
    const ly = labelY.get(t.id) ?? yScale(last.mu);
    const dotX = xScale(t.points.length - 1, t.points.length);
    const dotY = yScale(last.mu);

    return (
      <g
        key={t.id}
        opacity={dim ? 0.22 : 1}
        onPointerEnter={() => setHover(t.id)}
        onPointerLeave={() => setHover(null)}
        style={{ transition: "opacity 140ms" }}
      >
        <path
          d={path(t)}
          fill="none"
          stroke={color}
          strokeWidth={winner ? 2.5 : 2}
          strokeDasharray={winner ? undefined : "5 4"}
          strokeLinecap="round"
          strokeLinejoin="round"
        />
        {/* fat invisible hit target */}
        <path d={path(t)} fill="none" stroke="transparent" strokeWidth={16} />
        <circle
          cx={dotX}
          cy={dotY}
          r={winner ? 5 : 4}
          fill={color}
          stroke="var(--ground)"
          strokeWidth={2}
        />
        {/* leader line back to the true value when the label had to be nudged */}
        {Math.abs(ly - dotY) > 1.5 && (
          <line
            x1={dotX + 5}
            y1={dotY}
            x2={dotX + 9}
            y2={ly - 4}
            stroke={color}
            strokeWidth={1}
            opacity={0.6}
          />
        )}
        <text
          x={dotX + 12}
          y={ly}
          fontSize={13}
          fontWeight={winner ? 700 : 500}
          fill={winner ? "var(--figure)" : "var(--muted)"}
        >
          {t.name}
          <tspan className="mono" dx={6} fill="var(--muted)">
            {last.mu.toFixed(0)}
          </tspan>
          {winner && !cleared && (
            <tspan dx={6} fontSize={11} fill="var(--figure)">
              MISS
            </tspan>
          )}
        </text>
      </g>
    );
  };

  const hovered = trajectories.find((t) => t.id === hover);

  return (
    <figure>
      <div className="mb-2 flex flex-wrap items-center gap-x-6 gap-y-2 text-[13px]">
        <span className="flex items-center gap-2 text-[color:var(--figure)]">
          <svg width="26" height="10" aria-hidden>
            <line
              x1="0"
              y1="5"
              x2="26"
              y2="5"
              stroke="var(--accent)"
              strokeWidth="2.5"
            />
          </svg>
          Winners ({winners.length}) — solid
        </span>
        <span className="flex items-center gap-2 text-[color:var(--muted)]">
          <svg width="26" height="10" aria-hidden>
            <line
              x1="0"
              y1="5"
              x2="26"
              y2="5"
              stroke="var(--muted)"
              strokeWidth="2"
              strokeDasharray="5 4"
            />
          </svg>
          Matched controls ({controls.length}) — dashed
        </span>
        <span className="flex items-center gap-2 text-[color:var(--muted)]">
          <svg width="26" height="10" aria-hidden>
            <line
              x1="0"
              y1="5"
              x2="26"
              y2="5"
              stroke="var(--figure)"
              strokeWidth="2"
              strokeDasharray="3 3"
            />
          </svg>
          Threshold {threshold}
        </span>
      </div>

      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full"
        role="img"
        aria-label={`Backtest calibration. ${winners.length} winners rise toward and above the ${threshold} threshold; ${controls.length} matched controls stay flat and below it, the highest reaching ${Math.max(
          ...controls.map((c) => c.points[c.points.length - 1].mu),
        ).toFixed(0)}.`}
      >
        {[0, 25, 50, 75, 100].map((v) => (
          <g key={v}>
            <line
              x1={PAD.left}
              x2={W - PAD.right}
              y1={yScale(v)}
              y2={yScale(v)}
              stroke="var(--rule)"
              strokeWidth={1}
            />
            <text
              x={PAD.left - 8}
              y={yScale(v) + 4}
              textAnchor="end"
              fontSize={12}
              fill="var(--muted)"
              className="mono"
            >
              {v}
            </text>
          </g>
        ))}

        {/* The region controls must stay inside. Shading it makes the claim checkable. */}
        <rect
          x={PAD.left}
          y={yScale(threshold)}
          width={PLOT_W}
          height={PAD.top + PLOT_H - yScale(threshold)}
          fill="var(--muted)"
          opacity={0.05}
        />
        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={yScale(threshold)}
          y2={yScale(threshold)}
          stroke="var(--figure)"
          strokeWidth={2}
          strokeDasharray="3 3"
        />
        <text
          x={W - PAD.right + 8}
          y={yScale(threshold) - 7}
          fontSize={12}
          fontWeight={700}
          fill="var(--figure)"
        >
          THRESHOLD {threshold}
        </text>

        {controls.map(draw)}
        {winners.map(draw)}

        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={PAD.top + PLOT_H}
          y2={PAD.top + PLOT_H}
          stroke="var(--figure)"
          strokeWidth={1}
        />
        <text x={PAD.left} y={H - 10} fontSize={12} fill="var(--muted)">
          earliest footprint
        </text>
        <text
          x={W - PAD.right}
          y={H - 10}
          fontSize={12}
          textAnchor="end"
          fill="var(--muted)"
        >
          as_of (truncation date) · {n} observations
        </text>
      </svg>

      <figcaption className="mt-2 min-h-[42px] text-[14px]">
        {hovered ? (
          <span className="mono border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2 text-[color:var(--muted)]">
            <strong className="text-[color:var(--figure)]">{hovered.name}</strong>
            <span className="mx-2">·</span>
            {hovered.label}
            <span className="mx-2">·</span>
            final {hovered.points[hovered.points.length - 1].mu.toFixed(1)}
            <span className="mx-2">·</span>
            actual outcome: <strong className="text-[color:var(--figure)]">{hovered.outcome}</strong>
          </span>
        ) : (
          <span className="text-[color:var(--muted)]">
            Hover a trajectory for its actual outcome. Scores were computed with{" "}
            <code className="font-mono">as_of</code> pinned before any of these founders
            were publicly known.
          </span>
        )}
      </figcaption>
    </figure>
  );
}
