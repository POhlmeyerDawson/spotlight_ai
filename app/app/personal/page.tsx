"use client";

/**
 * Route `/personal` — your rank beside the core rank, disagreements first.
 *
 * THE ORDERING IS THE ARGUMENT (§3). A council tuned to a VC's history reproduces that
 * VC's blind spots with machine authority, so the companies where personal and core
 * disagree are the headline of this page and the agreements are a footnote at the
 * bottom labelled as confirmation. A system that can only show a VC their own taste back
 * is a mirror, not an analyst.
 *
 * The core order is never recomputed here. It arrives from the API as `core_rank`,
 * served unconditionally — including when personalisation is OFF, which is why this page
 * still has something true to show for a profile that cannot yet be personalised.
 * Preference re-orders the list; it never touches an axis score.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getPersonalRank, type PersonalRanking } from "@/lib/vc";
import { useSession } from "@/lib/useSession";
import Shell from "@/components/Shell";
import Reveal from "@/components/Reveal";
import { EmptyState, ErrorNote, Loading, Panel } from "@/components/ui";

/** Signed, glyphed, and never colour alone: a promotion and a demotion have to be
 *  distinguishable without colour perception. */
function Divergence({ value }: { value: number }) {
  if (value === 0) {
    return <span className="mono text-[color:var(--muted)]">▬ 0</span>;
  }
  const up = value > 0;
  return (
    <span
      className="mono"
      style={{ color: Math.abs(value) >= 3 ? "var(--accent)" : "var(--figure)" }}
      title={up ? "promoted against core rank" : "demoted against core rank"}
    >
      {up ? "▲ +" : "▼ −"}
      {Math.abs(value)}
    </span>
  );
}

export default function PersonalPage() {
  const { me } = useSession();
  const [ranking, setRanking] = useState<PersonalRanking | null>(null);
  const [error, setError] = useState<string | null>(null);

  /** Every state write happens AFTER the await, so nothing sets state synchronously in
   *  an effect body and no load cascades a render before the first paint. */
  const load = useCallback(async () => {
    const r = await getPersonalRank();
    if (!r.ok) {
      // 401 is the anonymous state, not an error: the page renders the invitation.
      setError(r.status === 401 ? null : `GET /personal/rank: ${r.error}`);
      setRanking(null);
      return;
    }
    setError(null);
    setRanking(r.data);
  }, []);

  useEffect(() => {
    let live = true;
    void (async () => {
      if (!me?.authenticated) {
        await Promise.resolve();
        if (live) setRanking(null);
        return;
      }
      await load();
    })();
    return () => {
      live = false;
    };
  }, [me?.authenticated, load]);

  const signedOut = me !== null && !me.authenticated;

  return (
    <Shell
      title="your rank, and core rank"
      lede={
        <span className="words">
          Preference re-orders the list. <em>It never moves an axis score.</em>
        </span>
      }
      crumbs={[{ label: "Account", href: "/" }, { label: "Your rank" }]}
      meta={
        <>
          CORE: OBJECTIVE
          <br />
          PERSONAL: YOUR PROFILE
          <br />
          DISAGREEMENTS FIRST
        </>
      }
    >
      {me === null && <Loading label="your session" />}

      {signedOut && (
        <Reveal>
          <Panel title="not signed in">
            <EmptyState
              title="a personal rank needs a profile, and a profile needs an owner"
              action={
                <>
                  <Link
                    href="/"
                    className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
                  >
                    SIGN IN OR REGISTER
                  </Link>
                  <Link
                    href="/pipeline"
                    className="meta border border-[color:var(--rule)] px-4 py-2"
                  >
                    THE CORE RANK →
                  </Link>
                </>
              }
            >
              The core rank is the objective product and is unaffected by any of this. It
              is on the pipeline page, with every axis and every claim, no account needed.
            </EmptyState>
          </Panel>
        </Reveal>
      )}

      {me?.authenticated && (
        <div className="grid gap-6">
          {error && <ErrorNote message={error} onRetry={() => void load()} />}
          {!ranking && !error && <Loading label="the personal ranking" />}

          {ranking && (
            <>
              <Reveal>
                <Panel
                  title={ranking.personalised ? "disagreements" : "personalisation is off"}
                  subtitle={ranking.reason}
                  emphasis={ranking.disagreements.length > 0}
                >
                  {!ranking.personalised ? (
                    <EmptyState
                      title="no personal order is published for this profile"
                      action={
                        <Link
                          href="/profile"
                          className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
                        >
                          BUILD THE PROFILE →
                        </Link>
                      }
                    >
                      An unpersonalised ranking does not publish a personal order at all —
                      a made-up ordering would be worse than none. The core rank below is
                      the real one and is unchanged.
                    </EmptyState>
                  ) : ranking.disagreements.length === 0 ? (
                    <EmptyState title="your council and the core rank agree everywhere">
                      No company moved three or more places. That is confirmation, not a
                      finding — and it is worth being suspicious of: a council that never
                      disagrees is reproducing the ranking it was given.
                    </EmptyState>
                  ) : (
                    <ul className="grid gap-3">
                      {ranking.disagreements.map((d) => (
                        <li
                          key={d.company_id}
                          className="border border-[color:var(--accent)] px-4 py-4"
                        >
                          <div className="flex flex-wrap items-baseline justify-between gap-3">
                            <h3 className="font-[family-name:var(--font-instrument-serif)] text-[28px] leading-none">
                              {d.name}
                            </h3>
                            <div className="mono text-[14px]">
                              CORE {d.core_rank} → YOURS {d.personal_rank}{" "}
                              <Divergence value={d.divergence} />
                            </div>
                          </div>
                          <p className="body-t mt-2 max-w-[72ch]">{d.explanation}</p>
                        </li>
                      ))}
                    </ul>
                  )}
                </Panel>
              </Reveal>

              <Reveal delay={60}>
                <Panel
                  title="both orders, side by side"
                  subtitle="Core rank is read from the objective endpoint and passed through untouched. Personal rank is the re-ordering your council produced from the same evidence."
                >
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-left">
                      <thead>
                        <tr className="meta text-[color:var(--muted)]">
                          <th className="border-b border-[color:var(--rule)] py-2 pr-4">
                            CORE
                          </th>
                          <th className="border-b border-[color:var(--rule)] py-2 pr-4">
                            YOURS
                          </th>
                          <th className="border-b border-[color:var(--rule)] py-2 pr-4">
                            COMPANY
                          </th>
                          <th className="border-b border-[color:var(--rule)] py-2 pr-4">
                            MOVE
                          </th>
                          <th className="border-b border-[color:var(--rule)] py-2 pr-4">
                            FIT
                          </th>
                          <th className="border-b border-[color:var(--rule)] py-2 pr-4">
                            CORE WEAKEST AXIS
                          </th>
                          <th className="border-b border-[color:var(--rule)] py-2">WHY</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(ranking.personalised
                          ? ranking.rows
                          : ranking.core_rank.map((c) => ({
                              company_id: c.company_id,
                              name: c.name,
                              core_rank: c.core_rank,
                              personal_rank: 0,
                              fit_score: 0,
                              core_weakest_score: 0,
                              divergence: 0,
                              top_lens: null,
                              why: "",
                            }))
                        ).map((row) => (
                          <tr key={row.company_id} className="align-top">
                            <td className="mono border-b border-[color:var(--rule)] py-2 pr-4 text-[14px]">
                              {row.core_rank}
                            </td>
                            <td className="mono border-b border-[color:var(--rule)] py-2 pr-4 text-[14px]">
                              {ranking.personalised ? row.personal_rank : "—"}
                            </td>
                            <td className="border-b border-[color:var(--rule)] py-2 pr-4 text-[15px]">
                              {row.name}
                            </td>
                            <td className="border-b border-[color:var(--rule)] py-2 pr-4 text-[14px]">
                              {ranking.personalised ? (
                                <Divergence value={row.divergence} />
                              ) : (
                                <span className="mono text-[color:var(--muted)]">—</span>
                              )}
                            </td>
                            <td className="mono border-b border-[color:var(--rule)] py-2 pr-4 text-[14px]">
                              {ranking.personalised ? row.fit_score.toFixed(3) : "—"}
                            </td>
                            <td className="mono border-b border-[color:var(--rule)] py-2 pr-4 text-[14px]">
                              {ranking.personalised
                                ? row.core_weakest_score.toFixed(3)
                                : "—"}
                            </td>
                            <td className="caption border-b border-[color:var(--rule)] py-2 max-w-[42ch] text-[color:var(--muted)]">
                              {row.why || "core order only"}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </Panel>
              </Reveal>

              {ranking.lenses.length > 0 && (
                <Reveal delay={60}>
                  <Panel
                    title="the lenses that did the re-ranking"
                    subtitle="Every personal adjustment names the lens and the weight behind it. A lens that cannot name the profile field that justified it is a preference invented on your behalf."
                    right={
                      <Link
                        href="/council"
                        className="meta border border-[color:var(--rule)] px-3 py-1.5"
                      >
                        EDIT THE COUNCIL
                      </Link>
                    }
                  >
                    <ul className="grid gap-3 md:grid-cols-2">
                      {ranking.lenses.map((l) => (
                        <li key={l.kind} className="border border-[color:var(--rule)] px-4 py-3">
                          <div className="meta flex justify-between text-[color:var(--muted)]">
                            <span>{l.kind.replace(/_/g, " ")}</span>
                            <span className="mono">W {l.weight.toFixed(3)}</span>
                          </div>
                          <p className="caption mt-1.5 max-w-none">{l.persona}</p>
                          <p className="meta mt-2 text-[color:var(--muted)]">
                            JUSTIFIED BY
                            <span className="mt-1 block normal-case tracking-normal">
                              {l.justified_by.join(" · ")}
                            </span>
                          </p>
                        </li>
                      ))}
                    </ul>
                  </Panel>
                </Reveal>
              )}

              {ranking.agreements.length > 0 && (
                <Reveal delay={60}>
                  <Panel title="where they agree — confirmation, not a finding">
                    <ul className="grid gap-1.5">
                      {ranking.agreements.map((a) => (
                        <li key={a} className="caption max-w-none text-[color:var(--muted)]">
                          {a}
                        </li>
                      ))}
                    </ul>
                  </Panel>
                </Reveal>
              )}
            </>
          )}
        </div>
      )}
    </Shell>
  );
}
