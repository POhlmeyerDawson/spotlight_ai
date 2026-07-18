"use client";

/**
 * The single most legible visual in the system: a score that MOVES, with a
 * confidence band that TIGHTENS as observations accumulate. That is the
 * state-space model explained without a word of narration.
 *
 * Small multiples — one panel per axis, shared 0–100 scale — rather than three
 * overlapping bands in one frame. Because the panels never overlap, POSITION
 * distinguishes the axes and no second or third series hue is needed (§2). Marks
 * are drawn in the plate accent; the marker shape repeats the axis identity.
 *
 * Hand-written SVG, no charting dependency.
 */

import { useEffect, useRef, useState } from "react";
import type { AxisKey, ScoreHistory, ScorePoint } from "@/lib/types";
import { AXIS_INDEX, AXIS_KEYS, AXIS_LABEL, AXIS_MARKER } from "@/lib/types";

const W = 520;
const H = 230;
const PAD = { top: 14, right: 56, bottom: 24, left: 34 };
const PLOT_W = W - PAD.left - PAD.right;
const PLOT_H = H - PAD.top - PAD.bottom;

const yScale = (v: number) => PAD.top + PLOT_H * (1 - v / 100);
const xScale = (i: number, n: number) =>
  PAD.left + (n <= 1 ? PLOT_W / 2 : (PLOT_W * i) / (n - 1));

function linePath(pts: ScorePoint[], n: number) {
  return pts
    .map((p, i) => `${i === 0 ? "M" : "L"}${xScale(i, n).toFixed(2)},${yScale(p.mu).toFixed(2)}`)
    .join(" ");
}

function bandPath(pts: ScorePoint[], n: number) {
  if (!pts.length) return "";
  const up = pts.map(
    (p, i) =>
      `${i === 0 ? "M" : "L"}${xScale(i, n).toFixed(2)},${yScale(Math.min(100, p.mu + p.band)).toFixed(2)}`,
  );
  const down = pts
    .map(
      (p, i) => `L${xScale(i, n).toFixed(2)},${yScale(Math.max(0, p.mu - p.band)).toFixed(2)}`,
    )
    .reverse();
  return `${up.join(" ")} ${down.join(" ")} Z`;
}

function EndMarker({ kind, x, y }: { kind: "dot" | "square" | "diamond"; x: number; y: number }) {
  const r = 4.5;
  const props = {
    fill: "var(--accent)",
    stroke: "var(--ground)",
    strokeWidth: 2,
    style: { transition: "all 220ms linear" },
  };
  if (kind === "square") {
    return <rect x={x - r} y={y - r} width={r * 2} height={r * 2} {...props} />;
  }
  if (kind === "diamond") {
    return (
      <path d={`M${x} ${y - r - 1} L${x + r + 1} ${y} L${x} ${y + r + 1} L${x - r - 1} ${y}Z`} {...props} />
    );
  }
  return <circle cx={x} cy={y} r={r} {...props} />;
}

function AxisPanel({
  axis,
  points,
  step,
  onHover,
  hoverIndex,
}: {
  axis: AxisKey;
  points: ScorePoint[];
  step: number;
  onHover: (i: number | null) => void;
  hoverIndex: number | null;
}) {
  const svgRef = useRef<SVGSVGElement>(null);
  const n = points.length;
  const shown = points.slice(0, Math.max(1, step));
  const last = shown[shown.length - 1];
  const first = shown[0];

  const handleMove = (e: React.PointerEvent<SVGSVGElement>) => {
    const el = svgRef.current;
    if (!el) return;
    const r = el.getBoundingClientRect();
    const xPx = ((e.clientX - r.left) / r.width) * W;
    const f = (xPx - PAD.left) / PLOT_W;
    const i = Math.round(f * (n - 1));
    onHover(i >= 0 && i < shown.length ? i : null);
  };

  const hovered = hoverIndex !== null && hoverIndex < shown.length ? shown[hoverIndex] : null;
  const hx = hoverIndex !== null ? xScale(hoverIndex, n) : 0;

  return (
    <figure className="min-w-0 border border-[color:var(--rule)] p-3">
      <figcaption className="mb-1 flex flex-wrap items-baseline justify-between gap-x-3">
        <span className="meta text-[color:var(--figure)]">
          <span className="text-[color:var(--muted)]">{AXIS_INDEX[axis]}</span>{" "}
          {AXIS_LABEL[axis]}
        </span>
        <span className="mono text-[11px] text-[color:var(--muted)]">
          ±{first.band.toFixed(1)} → ±{last.band.toFixed(1)} at n={last.n_events}
        </span>
      </figcaption>

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="h-auto w-full touch-none"
        role="img"
        aria-label={`${AXIS_LABEL[axis]} score over time: ${first.mu.toFixed(
          0,
        )} to ${last.mu.toFixed(0)}, confidence band narrowing from plus or minus ${first.band.toFixed(
          1,
        )} to plus or minus ${last.band.toFixed(1)} over ${last.n_events} observations.`}
        onPointerMove={handleMove}
        onPointerLeave={() => onHover(null)}
      >
        {[0, 50, 100].map((v) => (
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
              x={PAD.left - 7}
              y={yScale(v) + 4}
              textAnchor="end"
              fontSize={10}
              fill="var(--muted)"
              className="mono"
            >
              {v}
            </text>
          </g>
        ))}

        {/* The band. Its narrowing left-to-right is the whole point of this chart. */}
        <path
          d={bandPath(shown, n)}
          fill="var(--accent)"
          fillOpacity={0.14}
          stroke="var(--accent)"
          strokeOpacity={0.3}
          strokeWidth={1}
          style={{ transition: "d 220ms linear" }}
        />

        <path
          d={linePath(shown, n)}
          fill="none"
          stroke="var(--accent)"
          strokeWidth={2}
          strokeLinecap="round"
          strokeLinejoin="round"
        />

        {hovered && hoverIndex !== null && (
          <g pointerEvents="none">
            <line
              x1={hx}
              x2={hx}
              y1={PAD.top}
              y2={PAD.top + PLOT_H}
              stroke="var(--figure)"
              strokeOpacity={0.35}
              strokeWidth={1}
            />
            <circle cx={hx} cy={yScale(hovered.mu)} r={4} fill="var(--figure)" />
          </g>
        )}

        <EndMarker
          kind={AXIS_MARKER[axis]}
          x={xScale(shown.length - 1, n)}
          y={yScale(last.mu)}
        />
        <text
          x={xScale(shown.length - 1, n) + 11}
          y={yScale(last.mu) + 6}
          fontSize={19}
          fill="var(--figure)"
          fontFamily="var(--font-instrument-serif)"
          style={{ transition: "all 220ms linear" }}
        >
          {last.mu.toFixed(0)}
        </text>

        <line
          x1={PAD.left}
          x2={W - PAD.right}
          y1={PAD.top + PLOT_H}
          y2={PAD.top + PLOT_H}
          stroke="var(--figure)"
          strokeOpacity={0.4}
          strokeWidth={1}
        />
      </svg>

      <div className="mono mt-1 min-h-[30px] text-[11px] text-[color:var(--muted)]">
        {hovered ? (
          <>
            {new Date(hovered.t).toISOString().slice(0, 10)} · score{" "}
            <span className="text-[color:var(--figure)]">{hovered.mu.toFixed(1)}</span> · band{" "}
            <span className="text-[color:var(--figure)]">±{hovered.band.toFixed(1)}</span> ·{" "}
            {hovered.n_events} obs
          </>
        ) : (
          <>Hover the line for the value at a date.</>
        )}
      </div>
    </figure>
  );
}

export default function ScoreLine({ history }: { history: ScoreHistory }) {
  const n = Math.max(...AXIS_KEYS.map((k) => history[k]?.length ?? 0));
  const [step, setStep] = useState(n);
  const [playing, setPlaying] = useState(false);
  const [hoverIndex, setHoverIndex] = useState<number | null>(null);

  useEffect(() => {
    if (!playing) return;
    const id = setInterval(() => {
      setStep((s) => {
        if (s >= n) {
          setPlaying(false);
          return n;
        }
        return s + 1;
      });
    }, 260);
    return () => clearInterval(id);
  }, [playing, n]);

  const replay = () => {
    setStep(1);
    setPlaying(true);
  };

  const bandNow = history.founder?.[Math.max(0, step - 1)]?.band;
  const bandStart = history.founder?.[0]?.band;

  return (
    <div>
      <div className="mb-3 flex flex-wrap items-center gap-3">
        <button
          type="button"
          onClick={replay}
          className="meta border border-[color:var(--accent)] bg-[color:var(--accent)] px-4 py-2 text-[color:var(--paper)]"
        >
          {playing ? "▮▮ PLAYING…" : "▶ REPLAY AS EVIDENCE LANDS"}
        </button>

        <label className="mono flex min-w-[210px] flex-1 items-center gap-3 text-[11px] text-[color:var(--muted)]">
          <span className="whitespace-nowrap">
            obs {step}/{n}
          </span>
          <input
            type="range"
            min={1}
            max={n}
            value={step}
            onChange={(e) => {
              setPlaying(false);
              setStep(Number(e.target.value));
            }}
            className="w-full accent-[color:var(--accent)]"
            aria-label="Observations included"
          />
        </label>

        {bandNow !== undefined && bandStart !== undefined && (
          <span className="mono text-[11px] text-[color:var(--muted)]">
            founder band{" "}
            <span className="text-[color:var(--figure)]">±{bandStart.toFixed(1)}</span> →{" "}
            <span className="text-[color:var(--figure)]">±{bandNow.toFixed(1)}</span>
          </span>
        )}
      </div>

      <div className="grid gap-3 lg:grid-cols-3">
        {AXIS_KEYS.map((k) =>
          history[k]?.length ? (
            <AxisPanel
              key={k}
              axis={k}
              points={history[k]}
              step={step}
              hoverIndex={hoverIndex}
              onHover={setHoverIndex}
            />
          ) : null,
        )}
      </div>

      <p className="caption mt-3 max-w-none text-[color:var(--muted)]">
        Three separate filters, three separate bands. The score moves because the posterior
        moves; the band narrows as observations accumulate. The three axes are never
        combined into one number.
      </p>
    </div>
  );
}
