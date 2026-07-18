"use client";

/**
 * One axis. Three of these sit side by side and are NEVER summarised into a
 * combined figure — a hard product rule, enforced by there being no component in
 * this app that accepts more than one axis at a time.
 *
 * Told apart by position and marker (§2: no sixth hue), not by three series
 * colours. The whole card is the trace affordance.
 */

import type { Axis, AxisKey } from "@/lib/types";
import { AXIS_INDEX, AXIS_LABEL, AXIS_MARKER } from "@/lib/types";
import { Trend } from "./ui";

function Marker({ kind }: { kind: "dot" | "square" | "diamond" }) {
  const common = { fill: "var(--accent)" };
  return (
    <svg width="11" height="11" viewBox="0 0 11 11" aria-hidden className="shrink-0">
      {kind === "dot" && <circle cx="5.5" cy="5.5" r="4.5" {...common} />}
      {kind === "square" && <rect x="1" y="1" width="9" height="9" {...common} />}
      {kind === "diamond" && <path d="M5.5 0.5 10.5 5.5 5.5 10.5 0.5 5.5Z" {...common} />}
    </svg>
  );
}

export default function AxisCard({
  axisKey,
  axis,
  onOpenTrace,
}: {
  axisKey: AxisKey;
  axis: Axis;
  onOpenTrace: (a: AxisKey) => void;
}) {
  const count = axis.evidence_event_ids.length;
  const lo = Math.max(0, axis.score - axis.band);
  const hi = Math.min(100, axis.score + axis.band);

  return (
    <button
      type="button"
      onClick={() => onOpenTrace(axisKey)}
      className="group w-full border border-[color:var(--rule)] p-5 text-left transition-colors hover:border-[color:var(--accent)]"
      aria-label={`${AXIS_LABEL[axisKey]} axis, score ${axis.score.toFixed(
        0,
      )} plus or minus ${axis.band.toFixed(1)}, ${count} contributing events. Open the trace.`}
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="meta flex items-center gap-2 text-[color:var(--figure)]">
          <Marker kind={AXIS_MARKER[axisKey]} />
          <span className="text-[color:var(--muted)]">{AXIS_INDEX[axisKey]}</span>
          {AXIS_LABEL[axisKey]}
        </h3>
        <Trend value={axis.trend} />
      </div>

      <div className="mt-4 flex items-end gap-2">
        <span className="font-[family-name:var(--font-instrument-serif)] text-[68px] leading-[0.82] tracking-[-0.02em]">
          {axis.score.toFixed(0)}
        </span>
        <span className="mono mb-2 text-[18px] text-[color:var(--muted)]">
          ±{axis.band.toFixed(1)}
        </span>
      </div>

      {/* Band drawn to scale on the 0–100 track — uncertainty is never hidden. */}
      <div className="mt-5">
        <div className="relative h-[6px] w-full bg-[color:var(--ink-09)]">
          <span
            className="absolute top-0 h-[6px] bg-[color:var(--accent)] opacity-30"
            style={{ left: `${lo}%`, width: `${Math.max(1, hi - lo)}%` }}
          />
          <span
            className="absolute top-[-3px] h-[12px] w-[2px] bg-[color:var(--accent)]"
            style={{ left: `calc(${axis.score}% - 1px)` }}
          />
        </div>
        <div className="meta mt-1.5 flex justify-between text-[color:var(--muted)]">
          <span>{lo.toFixed(0)}</span>
          <span>CONFIDENCE BAND</span>
          <span>{hi.toFixed(0)}</span>
        </div>
      </div>

      <dl className="mt-5 flex border-t border-[color:var(--rule)] pt-3">
        <div className="flex-1">
          <dt className="meta text-[color:var(--muted)]">Confidence</dt>
          <dd className="mono mt-0.5 text-[16px]">{(axis.confidence * 100).toFixed(0)}%</dd>
        </div>
        <div className="flex-1 border-l border-[color:var(--rule)] pl-4">
          <dt className="meta text-[color:var(--muted)]">Evidence</dt>
          <dd className="mono mt-0.5 text-[16px]">
            {count} {count === 1 ? "event" : "events"}
          </dd>
        </div>
      </dl>

      <div className="meta mt-3 text-[color:var(--accent)] group-hover:underline">
        Trace → contributing events → quoted span
      </div>
    </button>
  );
}
