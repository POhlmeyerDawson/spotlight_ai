"use client";

/**
 * Memo | Dissent, side by side.
 *
 * The recommendation is locked SERVER-SIDE until the dissent is opened
 * (`api/main.py` nulls it unless `dissent_viewed=true`). We do not work around the
 * lock, we do not pre-fetch the unlocked memo, and we render the locked state
 * honestly rather than hiding the section until it arrives.
 */

import { useEffect, useState } from "react";
import { getDissent, getMemo, type Result } from "@/lib/api";
import type { Dissent, Memo } from "@/lib/types";
import { AXIS_LABEL } from "@/lib/types";
import { ErrorNote, Loading } from "./ui";

/** Renders [e-xx-01] citations as monospace chips so every claim visibly carries its id. */
function Cited({ text }: { text: string }) {
  const parts = text.split(/(\[[^\]]+\])/g);
  return (
    <>
      {parts.map((p, i) =>
        /^\[.+\]$/.test(p) ? (
          <code
            key={i}
            className="mx-0.5 bg-[color:var(--ink-09)] px-1.5 py-0.5 font-mono text-[12px] text-[var(--accent)]"
          >
            {p}
          </code>
        ) : (
          <span key={i}>{p}</span>
        ),
      )}
    </>
  );
}

export default function MemoDissent({ companyId }: { companyId: string }) {
  const [memo, setMemo] = useState<Result<Memo> | null>(null);
  const [dissent, setDissent] = useState<Result<Dissent> | null>(null);
  const [dissentOpen, setDissentOpen] = useState(false);
  const [loadingDissent, setLoadingDissent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mount-time load, always locked. The parent keys this component by company id, so
  // a different company remounts it and the lock resets with it — there is no path
  // where a previously-unlocked recommendation carries over to another company.
  useEffect(() => {
    let live = true;
    (async () => {
      try {
        const m = await getMemo(companyId, false);
        if (live) setMemo(m);
      } catch (e) {
        if (live) setError(e instanceof Error ? e.message : String(e));
      }
    })();
    return () => {
      live = false;
    };
  }, [companyId]);

  const openDissent = async () => {
    setLoadingDissent(true);
    setError(null);
    try {
      const d = await getDissent(companyId);
      setDissent(d);
      setDissentOpen(true);
      // Re-request the memo with dissent_viewed=true. The server decides, not us.
      setMemo(await getMemo(companyId, true));
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoadingDissent(false);
    }
  };

  if (!memo) return <Loading label="memo" />;

  const m = memo.data;
  const locked = m.recommendation === null;

  return (
    <div className="space-y-3">
      {error && <ErrorNote message={`Dissent load failed: ${error}`} />}

      <div className="grid gap-3 lg:grid-cols-2">
        {/* ---------------------------------------------------------------- memo */}
        <section className="border border-[color:var(--rule)] bg-[color:var(--ground)]">
          <header className="border-b border-[color:var(--rule)] px-5 py-3">
            <h3 className="meta text-[color:var(--figure)]">
              Investment memo
            </h3>
            <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
              Every claim cites its event id. Gaps are flagged, never filled.
            </p>
          </header>

          <div className="space-y-5 px-5 py-4">
            {m.sections.map((s) => (
              <div key={s.heading}>
                <h4 className="meta text-[color:var(--muted)]">
                  {s.heading}
                </h4>
                <p className="mt-1.5 text-[15px] leading-[1.55] text-[color:var(--muted)]">
                  <Cited text={s.body} />
                </p>
              </div>
            ))}

            {m.gaps.length > 0 && (
              <div
                className="border px-4 py-3"
                style={{
                  borderColor: "var(--figure)",
                  background: "color-mix(in oklab, var(--figure) 8%, transparent)",
                }}
              >
                <h4
                  className="meta"
                  style={{ color: "var(--figure)" }}
                >
                  ⚠ What this memo does not know
                </h4>
                <ul className="mt-2 space-y-1.5">
                  {m.gaps.map((g, i) => (
                    <li key={i} className="text-[14px] leading-snug text-[color:var(--muted)]">
                      <span className="mr-2 text-[color:var(--muted)]">—</span>
                      {g}
                    </li>
                  ))}
                </ul>
              </div>
            )}

            {/* ------------------------------------------- the locked recommendation */}
            <div>
              <h4 className="meta text-[color:var(--muted)]">
                Recommendation
              </h4>
              {locked ? (
                <div
                  className="mt-1.5 border-2 border-dashed px-5 py-6 text-center"
                  style={{
                    borderColor: "var(--figure)",
                    background: "var(--ink-09)",
                  }}
                >
                  <div className="text-[28px] leading-none" aria-hidden>
                    🔒
                  </div>
                  <p className="mt-2 text-[17px] font-medium text-[color:var(--figure)]">
                    Recommendation locked
                  </p>
                  <p className="mx-auto mt-1.5 max-w-md text-[14px] leading-[1.55] text-[color:var(--muted)]">
                    The server is withholding it:{" "}
                    <em>
                      “{m.recommendation_locked_reason ?? "open the dissent view first"}”
                    </em>
                    . You cannot read the recommendation before you have read the case
                    against it.
                  </p>
                  <button
                    type="button"
                    onClick={openDissent}
                    disabled={loadingDissent}
                    className="mt-4 border px-5 py-2.5 text-[15px] font-medium tracking-wide text-[color:var(--figure)] transition disabled:opacity-60"
                    style={{
                      borderColor: "var(--accent)",
                      background: "color-mix(in oklab, var(--accent) 14%, transparent)",
                    }}
                  >
                    {loadingDissent ? "Opening…" : "Open the dissent to unlock →"}
                  </button>
                </div>
              ) : (
                <p
                  className="mt-1.5 border-l-4 px-4 py-3 text-[16px] leading-[1.55] font-medium text-[color:var(--figure)]"
                  style={{
                    borderColor: "var(--accent)",
                    background: "color-mix(in oklab, var(--accent) 9%, transparent)",
                  }}
                >
                  <Cited text={m.recommendation!} />
                </p>
              )}
            </div>
          </div>
        </section>

        {/* ------------------------------------------------------------- dissent */}
        <section
          className="border bg-[color:var(--ground)]"
          style={{ borderColor: dissentOpen ? "var(--accent)" : "var(--rule)" }}
        >
          <header className="border-b border-[color:var(--rule)] px-5 py-3">
            <h3 className="meta text-[color:var(--figure)]">
              Dissent · the case against
            </h3>
            <p className="caption mt-0.5 max-w-none text-[color:var(--muted)]">
              Generated adversarially against the memo, from the same evidence set.
            </p>
          </header>

          {!dissentOpen ? (
            <div className="flex min-h-[280px] flex-col items-center justify-center px-6 py-10 text-center">
              <p className="max-w-sm text-[15px] leading-[1.55] text-[color:var(--muted)]">
                The dissent is not open yet. Opening it is what unlocks the recommendation
                on the left — the order is enforced by the server, not by this page.
              </p>
              <button
                type="button"
                onClick={openDissent}
                disabled={loadingDissent}
                className="mt-5 border px-5 py-2.5 text-[15px] font-medium tracking-wide text-[color:var(--figure)] transition disabled:opacity-60"
                style={{
                  borderColor: "var(--accent)",
                  background: "color-mix(in oklab, var(--accent) 14%, transparent)",
                }}
              >
                {loadingDissent ? "Opening…" : "Open the dissent"}
              </button>
            </div>
          ) : dissent ? (
            <div className="space-y-5 px-5 py-4">
              <div>
                <h4 className="meta text-[color:var(--muted)]">
                  Bear case
                </h4>
                <p className="mt-1.5 text-[15px] leading-[1.55] text-[color:var(--muted)]">
                  {dissent.data.bear_case}
                </p>
              </div>

              <div>
                <h4 className="meta text-[color:var(--muted)]">
                  Weakest evidence
                </h4>
                <ul className="mt-2 space-y-2">
                  {dissent.data.weakest_evidence.map((w, i) => (
                    <li
                      key={i}
                      className="border border-[color:var(--rule)] bg-[color:var(--ink-09)] px-3 py-2 text-[14px] leading-snug text-[color:var(--muted)]"
                    >
                      {w}
                    </li>
                  ))}
                </ul>
              </div>

              <div
                className="border px-4 py-3"
                style={{
                  borderColor: "var(--figure)",
                  background: "var(--ink-09)",
                }}
              >
                <h4
                  className="meta"
                  style={{ color: "var(--figure)" }}
                >
                  Load-bearing claim
                </h4>
                <p className="mt-1.5 text-[15px] leading-[1.55] font-medium text-[color:var(--figure)]">
                  {dissent.data.load_bearing_claim}
                </p>
                <p className="mt-1.5 text-[13px] text-[color:var(--muted)]">
                  If this is false, the thesis fails. Named, not hedged.
                </p>
              </div>

              <div>
                <h4 className="meta text-[color:var(--muted)]">
                  Bull/bear spread per axis
                </h4>
                <p className="mt-1 mb-2 text-[13px] text-[color:var(--muted)]">
                  How far apart the memo and the dissent land on each axis. A wide spread is
                  uncertainty, and it is reported per axis — never pooled.
                </p>
                <ul className="space-y-2">
                  {Object.entries(dissent.data.axis_spreads).map(([k, v]) => (
                    <li key={k} className="flex items-center gap-3">
                      <span className="w-36 shrink-0 text-[13px] text-[color:var(--muted)]">
                        {AXIS_LABEL[k as keyof typeof AXIS_LABEL] ?? k}
                      </span>
                      <span className="h-2.5 flex-1 bg-[color:var(--ink-09)]">
                        <span
                          className="block h-2.5 rounded-full"
                          style={{
                            width: `${Math.min(100, (v ?? 0) * 2)}%`,
                            background: "var(--figure)",
                            opacity: 0.75,
                          }}
                        />
                      </span>
                      <span className="mono w-12 shrink-0 text-right text-[14px] font-medium text-[color:var(--figure)]">
                        {v?.toFixed(0)}
                      </span>
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          ) : (
            <div className="p-5">
              <Loading label="dissent" />
            </div>
          )}
        </section>
      </div>
    </div>
  );
}
