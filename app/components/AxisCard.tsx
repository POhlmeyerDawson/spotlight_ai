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
import {
  AXIS_INDEX,
  AXIS_LABEL,
  AXIS_MARKER,
  TREND_UNIT_DIRECTION,
} from "@/lib/types";
import { Trend } from "./ui";

/**
 * A direction is a sign, not a rate. Rendering "+1.0" for "trending up" beside a genuine
 * rate of "+0.24 points per 30 days" invites reading the first as four times the second,
 * so a direction axis gets a word and the rate axis gets its number and its unit.
 */
function AxisTrend({ axis }: { axis: Axis }) {
  if (axis.trend === null) return null;
  if (axis.trend_unit === TREND_UNIT_DIRECTION) {
    const t = axis.trend;
    const word = t > 0 ? "rising" : t < 0 ? "falling" : "flat";
    return (
      <span
        className="meta inline-flex items-center gap-1"
        style={{ color: t > 0 ? "var(--accent)" : "var(--muted)" }}
        title="Direction only — this axis reports a sign, not a rate."
      >
        <span aria-hidden>{t > 0 ? "▲" : t < 0 ? "▼" : "▬"}</span>
        {word}
      </span>
    );
  }
  return <Trend value={axis.trend} />;
}

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
  governing = false,
}: {
  axisKey: AxisKey;
  /**
   * Optional because the payload genuinely may not carry it. `RankedCompany.axes` is
   * typed `Record<AxisKey, Axis>`, which promises all three always exist — the API
   * does not keep that promise for live-sourced rows, and the gap between the type
   * and the payload is what crashed the pipeline page. Accepting undefined here
   * makes the component honest about what it can actually be handed.
   */
  axis: Axis | undefined;
  onOpenTrace: (a: AxisKey) => void;
  /** The weakest axis: the one min-axis ranking actually keys on. Marked, not reordered
   *  by colour — the cards are already sorted weakest-first by the page. */
  governing?: boolean;
}) {
  // ABSENT is a third state, distinct from both a score and a null score. The backend
  // never computed this axis, so there is no interval, no evidence and nothing to
  // trace — and saying "no evidence" here would assert we looked.
  if (!axis) {
    return (
      <div className="border-l border-[color:var(--rule)] pl-3">
        <div className="meta text-[color:var(--muted)]">{AXIS_LABEL[axisKey]}</div>
        <div className="mono mt-1 text-[13px] text-[color:var(--muted)]">not computed</div>
        <div className="hatch mt-1.5 h-[4px] w-full bg-[color:var(--ink-09)]" />
        <p className="caption mt-1 text-[color:var(--muted)]">
          This axis was not computed for this company. It is unmeasured, not zero, and
          there is nothing to trace.
        </p>
      </div>
    );
  }

  const count = axis.evidence_event_ids.length;
  /**
   * A NULL band is "we did not compute an interval", which is NOT the same claim as a
   * band of zero. Coalescing it to 0 drew a zero-width interval and printed "±0.0",
   * i.e. perfect certainty — the strongest possible claim, made on the weakest possible
   * grounds. The two states are now drawn differently and neither borrows the other's
   * confidence.
   */
  const hasBand = axis.band !== null;
  const band = axis.band ?? 0;
  /**
   * A null score is an ABSENCE OF EVIDENCE, not a zero, and the whole gate exists to
   * keep those apart. It is drawn as a hatched track with the backend's own stated
   * reason — the same hatch NOT_ATTEMPTED claims use, so "we did not look" and "there
   * was nothing to look at" share one visual language and neither reads as a low score.
   */
  const scored = axis.score !== null;
  const lo = scored ? Math.max(0, axis.score! - band) : 0;
  const hi = scored ? Math.min(100, axis.score! + band) : 0;

  return (
    <button
      type="button"
      onClick={() => onOpenTrace(axisKey)}
      // Nothing to trace means nothing to click. A live affordance that opens an empty
      // drawer reads as a broken feature; an inert card reads as an honest absence.
      disabled={count === 0}
      className="group w-full border p-5 text-left transition-colors enabled:hover:border-[color:var(--accent)] disabled:cursor-default"
      style={{
        // The governing axis gets weight, not a sixth hue: a heavier rule in the accent.
        borderColor: governing ? "var(--accent)" : "var(--rule)",
        borderWidth: governing ? 2 : 1,
      }}
      aria-label={
        scored
          ? `${AXIS_LABEL[axisKey]} axis, score ${axis.score!.toFixed(0)}${
              hasBand ? `, plus or minus ${band.toFixed(1)}` : ", no interval computed"
            }, ${count} contributing events.${
              count ? " Open the trace." : ""
            }`
          : `${AXIS_LABEL[axisKey]} axis, not scored: no observable evidence.`
      }
    >
      <div className="flex items-center justify-between gap-2">
        <h3 className="meta flex items-center gap-2 text-[color:var(--figure)]">
          <Marker kind={AXIS_MARKER[axisKey]} />
          <span className="text-[color:var(--muted)]">{AXIS_INDEX[axisKey]}</span>
          {AXIS_LABEL[axisKey]}
        </h3>
        <AxisTrend axis={axis} />
      </div>

      {governing && (
        <div className="meta mt-2 text-[color:var(--accent)]">
          ◂ WEAKEST AXIS — THIS IS WHAT THE RANKING KEYS ON
        </div>
      )}

      {/* A seeded number must never be read as a computed one. */}
      {axis.live === false && (
        <div className="meta mt-2 border border-dashed border-[color:var(--muted)] px-2 py-1 text-[color:var(--muted)]">
          SEEDED — NOT COMPUTED BY THE SCREEN
        </div>
      )}

      {scored ? (
        <>
          <div className="mt-4 flex items-end gap-2">
            <span className="font-[family-name:var(--font-instrument-serif)] text-[68px] leading-[0.82] tracking-[-0.02em]">
              {axis.score!.toFixed(0)}
            </span>
            <span className="mono mb-2 text-[18px] text-[color:var(--muted)]">
              {hasBand ? `±${band.toFixed(1)}` : "no interval"}
            </span>
          </div>

          {/* Band drawn to scale on the 0–100 track — uncertainty is never hidden. */}
          <div className="mt-5">
            {hasBand ? (
              <>
                <div className="relative h-[6px] w-full bg-[color:var(--ink-09)]">
                  <span
                    className="absolute top-0 h-[6px] bg-[color:var(--accent)] opacity-30"
                    style={{ left: `${lo}%`, width: `${Math.max(1, hi - lo)}%` }}
                  />
                  <span
                    className="absolute top-[-3px] h-[12px] w-[2px] bg-[color:var(--accent)]"
                    style={{ left: `calc(${axis.score!}% - 1px)` }}
                  />
                </div>
                <div className="meta mt-1.5 flex justify-between text-[color:var(--muted)]">
                  <span>{lo.toFixed(0)}</span>
                  <span>CONFIDENCE BAND</span>
                  <span>{hi.toFixed(0)}</span>
                </div>
              </>
            ) : (
              <>
                {/* Hatched, like every other absence in this app — no interval was
                    computed, which is not the same as an interval of zero width. */}
                <div className="relative h-[6px] w-full">
                  <div className="hatch absolute inset-0 bg-[color:var(--ink-09)]" />
                  <span
                    className="absolute top-[-3px] h-[12px] w-[2px] bg-[color:var(--accent)]"
                    style={{ left: `calc(${axis.score!}% - 1px)` }}
                  />
                </div>
                <div className="meta mt-1.5 text-[color:var(--muted)]">
                  NO INTERVAL COMPUTED — NOT A BAND OF ZERO
                </div>
              </>
            )}
          </div>
        </>
      ) : (
        <>
          <div className="mt-4 flex items-end gap-3">
            <span className="font-[family-name:var(--font-instrument-serif)] text-[68px] leading-[0.82] tracking-[-0.02em] text-[color:var(--muted)]">
              —
            </span>
            <span className="meta mb-3 text-[color:var(--muted)]">not scored</span>
          </div>
          <div className="mt-5">
            <div className="hatch h-[6px] w-full bg-[color:var(--ink-09)]" />
            <div className="meta mt-1.5 text-[color:var(--muted)]">
              NO OBSERVABLE EVIDENCE — AN ABSENCE, NOT A ZERO
            </div>
          </div>
          {axis.reason && (
            <p className="caption mt-3 max-w-none text-[color:var(--muted)]">
              {axis.reason}
            </p>
          )}
        </>
      )}

      <dl className="mt-5 flex border-t border-[color:var(--rule)] pt-3">
        <div className="flex-1">
          <dt className="meta text-[color:var(--muted)]">Confidence</dt>
          {/*
            Confidence is a property OF A MEASUREMENT. On an unscored axis there is no
            measurement to be confident about, and printing "0%" inside the same card
            whose hatch reads "AN ABSENCE, NOT A ZERO" contradicts it in the next line —
            it reads as "we measured this and are certain of nothing" rather than "we
            did not measure this". An absence gets the same em dash as the score.
          */}
          <dd
            className={`mono mt-0.5 text-[16px]${scored ? "" : " text-[color:var(--muted)]"}`}
            title={scored ? undefined : "Unmeasured — confidence does not apply."}
          >
            {scored ? `${(axis.confidence * 100).toFixed(0)}%` : "—"}
          </dd>
        </div>
        <div className="flex-1 border-l border-[color:var(--rule)] pl-4">
          <dt className="meta text-[color:var(--muted)]">Evidence</dt>
          <dd className="mono mt-0.5 text-[16px]">
            {count} {count === 1 ? "event" : "events"}
          </dd>
        </div>
      </dl>

      {/*
        An axis with no receipts returns an EMPTY evidence list plus a reason. Offering a
        "Trace →" affordance there would open a drawer with nothing in it, so the reason
        is rendered instead of a link that leads nowhere.
      */}
      {count > 0 ? (
        <div className="meta mt-3 text-[color:var(--accent)] group-hover:underline">
          Trace → contributing events → quoted span
        </div>
      ) : (
        <p className="caption mt-3 max-w-none text-[color:var(--muted)]">
          {axis.reason ??
            "No citable events are attached to this axis, so there is nothing to trace."}
        </p>
      )}
    </button>
  );
}
