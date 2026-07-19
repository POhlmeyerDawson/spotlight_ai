"use client";

/**
 * Route `/council` — the council agent builder.
 *
 * Two columns, and the split between them is the honest part of this screen:
 *
 *   DERIVED — served by `GET /personal/lenses`. Each one names the profile field that
 *   justified it and carries provenance. The user cannot type these; the system read
 *   them out of the survey answers and the uploaded decisions.
 *
 *   AUTHORED — typed by the VC, or accepted from the template set and then edited.
 *
 * Keeping the two visibly apart is what makes the stated-vs-revealed gap mean anything:
 * if an authored agent could pass itself off as derived, "what you said" and "what you
 * did" would no longer be separable, which is the one thing §0 forbids.
 *
 * THE PERSISTENCE TRUTH, STATED ON SCREEN. There is no write route for a lens. The API
 * serves `GET /personal/lenses` and nothing else, and `PUT /profile` takes only
 * fund_name, focus_sectors and stated_red_lines. So authored agents are stored in this
 * browser and do NOT reach the profile or the ranking yet. A "Saved to your account"
 * message here would be a lie about what the system holds, so the banner says exactly
 * where the drafts are and what is missing.
 *
 * The 2..5 bounds are the backend's (`custom_council.MIN_LENSES` / `MAX_LENSES`) and are
 * enforced by REFUSING with a reason, never by silently clamping a number the user typed.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getLenses, type LensSet } from "@/lib/vc";
import { useSession } from "@/lib/useSession";
import {
  loadDrafts,
  newAgent,
  saveDrafts,
  templateAgents,
  MAX_LENSES,
  MIN_LENSES,
  type DraftAgent,
} from "@/lib/councilDraft";
import Shell from "@/components/Shell";
import Reveal from "@/components/Reveal";
import { EmptyState, ErrorNote, Loading, Panel } from "@/components/ui";

function AgentEditor({
  agent,
  onChange,
  onDelete,
  deleteBlockedReason,
}: {
  agent: DraftAgent;
  onChange: (next: DraftAgent) => void;
  onDelete: () => void;
  deleteBlockedReason: string | null;
}) {
  const set = <K extends keyof DraftAgent>(key: K, value: DraftAgent[K]) =>
    onChange({ ...agent, [key]: value });

  return (
    <li
      className="border px-4 py-4"
      style={{
        borderColor: agent.origin === "template" ? "var(--muted)" : "var(--rule)",
        borderStyle: agent.origin === "template" ? "dashed" : "solid",
      }}
    >
      <div className="meta flex flex-wrap items-center justify-between gap-2 text-[color:var(--muted)]">
        <span>
          {agent.origin === "template"
            ? "TEMPLATE — A STARTING POINT YOU EDIT"
            : "AUTHORED BY YOU"}
        </span>
        <button
          type="button"
          onClick={onDelete}
          disabled={Boolean(deleteBlockedReason)}
          title={deleteBlockedReason ?? "remove this agent"}
          className="border border-[color:var(--rule)] px-2 py-1 disabled:opacity-50"
        >
          DELETE
        </button>
      </div>
      {deleteBlockedReason && (
        <p className="caption mt-1.5 max-w-none text-[color:var(--muted)]">
          {deleteBlockedReason}
        </p>
      )}

      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1.5">
          <span className="meta text-[color:var(--muted)]">NAME</span>
          <input
            value={agent.name}
            onChange={(e) => set("name", e.target.value)}
            placeholder="CyberSecurity Agent"
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
          />
        </label>
        <label className="grid gap-1.5">
          <span className="meta text-[color:var(--muted)]">
            QUALITY IT ADDS SCORE FOR
          </span>
          <input
            value={agent.quality}
            onChange={(e) => set("quality", e.target.value)}
            placeholder="security_engineering"
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
          />
        </label>
      </div>

      <label className="mt-3 grid gap-1.5">
        <span className="meta text-[color:var(--muted)]">
          WHAT IT ARGUES — PLAIN LANGUAGE
        </span>
        <textarea
          rows={3}
          value={agent.persona}
          onChange={(e) => set("persona", e.target.value)}
          placeholder="You add score for founders who treat security as an engineering discipline…"
          className="border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px] leading-snug"
        />
      </label>

      <label className="mt-3 grid gap-1.5">
        <span className="meta flex justify-between text-[color:var(--muted)]">
          <span>WEIGHT</span>
          <span className="mono">{agent.weight.toFixed(2)}</span>
        </span>
        <input
          type="range"
          min={0}
          max={1}
          step={0.05}
          value={agent.weight}
          onChange={(e) => set("weight", Number(e.target.value))}
          className="accent-[color:var(--accent)]"
        />
      </label>
    </li>
  );
}

export default function CouncilPage() {
  const { me } = useSession();
  const [lensSet, setLensSet] = useState<LensSet | null>(null);
  const [lensError, setLensError] = useState<string | null>(null);

  const [drafts, setDrafts] = useState<DraftAgent[]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

  const userId = me?.user?.user_id ?? null;

  /** State is written only after the await, so no effect body sets state synchronously. */
  const loadLenses = useCallback(async () => {
    const r = await getLenses();
    if (!r.ok) {
      setLensError(r.status === 401 ? null : `GET /personal/lenses: ${r.error}`);
      setLensSet(null);
      return;
    }
    setLensError(null);
    setLensSet(r.data);
  }, []);

  useEffect(() => {
    let live = true;
    void (async () => {
      if (!me?.authenticated) {
        await Promise.resolve();
        if (live) setLensSet(null);
        return;
      }
      await loadLenses();
    })();
    return () => {
      live = false;
    };
  }, [me?.authenticated, loadLenses]);

  useEffect(() => {
    // Drafts are namespaced by user, so signing into a second account on the same
    // machine never shows the first account's council. Read after a tick: localStorage
    // does not exist during SSR, and a synchronous set here would cascade a render.
    let live = true;
    void (async () => {
      await Promise.resolve();
      if (live) setDrafts(userId ? loadDrafts(userId) : []);
    })();
    return () => {
      live = false;
    };
  }, [userId]);

  function commit(next: DraftAgent[]) {
    setDrafts(next);
    setNotice(null);
    if (!userId) return;
    const ok = saveDrafts(userId, next);
    setNotice(
      ok
        ? "Saved in this browser. Not on your account — the API has no write route for a lens yet."
        : "Could not write to this browser's storage; the edits are in memory only and will not survive a reload.",
    );
  }

  const derivedCount = lensSet?.lenses.length ?? 0;
  const total = derivedCount + drafts.length;

  function add() {
    setRefusal(null);
    if (total >= MAX_LENSES) {
      setRefusal(
        `The council is capped at ${MAX_LENSES} lenses and you already have ${total} ` +
          `(${derivedCount} derived from your profile, ${drafts.length} authored). ` +
          `Delete one, or lower a derived lens by changing the answers that produced it — nothing is being clamped silently.`,
      );
      return;
    }
    commit([...drafts, newAgent()]);
  }

  function useTemplate() {
    setRefusal(null);
    const room = MAX_LENSES - derivedCount;
    if (room <= 0) {
      setRefusal(
        `Your profile already derives ${derivedCount} lenses, which fills the ${MAX_LENSES}-lens ceiling. The template set has nowhere to go.`,
      );
      return;
    }
    const template = templateAgents().slice(0, room);
    commit(template);
    setRefusal(
      template.length < templateAgents().length
        ? `Loaded ${template.length} of ${templateAgents().length} template agents — the rest would exceed the ${MAX_LENSES}-lens ceiling alongside your ${derivedCount} derived lenses.`
        : null,
    );
  }

  const signedOut = me !== null && !me.authenticated;

  return (
    <Shell
      title="the council"
      lede={
        <span className="words">
          Personas that reweight the same evidence.{" "}
          <em>They never get evidence the core analysis cannot see.</em>
        </span>
      }
      crumbs={[{ label: "Account", href: "/" }, { label: "Council" }]}
      meta={
        <>
          {MIN_LENSES}–{MAX_LENSES} LENSES
          <br />
          DERIVED + AUTHORED
          <br />
          SAME EVIDENCE GRAPH
        </>
      }
    >
      {me === null && <Loading label="your session" />}

      {signedOut && (
        <Reveal>
          <Panel title="not signed in">
            <EmptyState
              title="a council belongs to a profile"
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
                    CORE RANK — NO ACCOUNT NEEDED
                  </Link>
                </>
              }
            >
              The core analysis runs a fixed council of its own and is unaffected by
              anything on this page.
            </EmptyState>
          </Panel>
        </Reveal>
      )}

      {me?.authenticated && (
        <div className="grid gap-6">
          <Reveal>
            <Panel title="where these are stored">
              <p className="body-t max-w-[72ch]">
                Derived lenses are served by the API and are live: they are what re-ranks
                your list today. Authored agents are held in{" "}
                <span className="mono">this browser only</span> — the API exposes{" "}
                <span className="mono">GET /personal/lenses</span> and no route to create,
                update or delete one, so they do not yet reach your profile or the
                ranking. Nothing here reports a save that did not happen.
              </p>
            </Panel>
          </Reveal>

          <div className="grid gap-6 lg:grid-cols-2">
            <Reveal>
              <Panel
                title="derived from your profile"
                subtitle="Read-only. Each names the profile field that justified it — a lens that cannot do that is a preference invented on your behalf."
              >
                {lensError && <ErrorNote message={lensError} onRetry={() => void loadLenses()} />}
                {!lensSet && !lensError && <Loading label="the derived lenses" />}

                {lensSet && (
                  <>
                    <p className="caption max-w-none text-[color:var(--muted)]">
                      {lensSet.personalisation_reason}
                    </p>

                    {lensSet.lenses.length === 0 ? (
                      <div className="mt-4">
                        <EmptyState
                          title="nothing has been derived yet"
                          action={
                            <Link
                              href="/profile"
                              className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
                            >
                              ANSWER THE TRADE-OFFS →
                            </Link>
                          }
                        >
                          There is no default council. Lenses come from answered trade-offs
                          and uploaded decisions, and until those exist the honest number
                          is zero.
                        </EmptyState>
                      </div>
                    ) : (
                      <ul className="mt-4 grid gap-3">
                        {lensSet.lenses.map((l) => (
                          <li
                            key={l.kind}
                            className="border border-[color:var(--accent)] px-4 py-3"
                          >
                            <div className="meta flex justify-between text-[color:var(--muted)]">
                              <span>{l.kind.replace(/_/g, " ")}</span>
                              <span className="mono">
                                W {l.weight.toFixed(3)} · CONF {l.confidence.toFixed(2)}
                              </span>
                            </div>
                            <p className="caption mt-1.5 max-w-none">{l.persona}</p>
                            <p className="meta mt-2 text-[color:var(--muted)]">
                              JUSTIFIED BY
                              <span className="mt-1 block normal-case tracking-normal">
                                {l.justified_by.join(" · ")}
                              </span>
                              <span className="mt-1 block normal-case tracking-normal">
                                {l.provenance.basis} · {l.provenance.method} · n=
                                {l.provenance.n}
                              </span>
                            </p>
                          </li>
                        ))}
                      </ul>
                    )}

                    {lensSet.not_derived.length > 0 && (
                      <div className="mt-5 border-t border-[color:var(--rule)] pt-4">
                        <h3 className="meta text-[color:var(--figure)]">
                          NOT DERIVED — AND WHY
                        </h3>
                        <ul className="mt-2 grid gap-2">
                          {lensSet.not_derived.map((n) => (
                            <li
                              key={n.field_name}
                              className="hatch border border-dashed border-[color:var(--muted)] px-3 py-2"
                            >
                              <div className="meta text-[color:var(--figure)]">
                                {n.field_name}
                              </div>
                              <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
                                {n.reason}
                              </p>
                            </li>
                          ))}
                        </ul>
                      </div>
                    )}
                  </>
                )}
              </Panel>
            </Reveal>

            <Reveal delay={60}>
              <Panel
                title="agents you author"
                subtitle={`The council holds ${MIN_LENSES} to ${MAX_LENSES} lenses in total. Below ${MIN_LENSES} it is a renamed axis, not a council; above ${MAX_LENSES} the personas stop being distinguishable.`}
                right={
                  <div className="meta text-right text-[color:var(--muted)]">
                    <span className="mono">{total}</span> OF {MAX_LENSES} TOTAL
                    <span className="mt-1 block">
                      {derivedCount} DERIVED · {drafts.length} AUTHORED
                    </span>
                  </div>
                }
              >
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={add}
                    className="meta border px-4 py-2"
                    style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
                  >
                    + NEW AGENT
                  </button>
                  <button
                    type="button"
                    onClick={useTemplate}
                    className="meta border border-[color:var(--rule)] px-4 py-2"
                    title="Loads a labelled template set for you to edit. It is not derived from your history."
                  >
                    USE THE TEMPLATE SET
                  </button>
                  {drafts.length > 0 && (
                    <button
                      type="button"
                      onClick={() => commit([])}
                      className="meta border border-[color:var(--rule)] px-4 py-2"
                    >
                      CLEAR AUTHORED
                    </button>
                  )}
                </div>

                {refusal && (
                  <div className="mt-3">
                    <ErrorNote message={refusal} />
                  </div>
                )}
                {notice && (
                  <p className="caption mt-3 max-w-none text-[color:var(--muted)]">
                    {notice}
                  </p>
                )}

                {total < MIN_LENSES && (
                  <p className="caption mt-3 max-w-none text-[color:var(--muted)]">
                    {total} lens(es) in total. The backend refuses to produce a fit score
                    below {MIN_LENSES} — a single lens is a renamed axis, not a council.
                  </p>
                )}

                {drafts.length === 0 ? (
                  <div className="mt-4">
                    <EmptyState title="no authored agents">
                      Add one, or start from the template set and edit it. The template is
                      labelled as a template wherever it appears — it is a starting point
                      you accepted, never something read out of your history.
                    </EmptyState>
                  </div>
                ) : (
                  <ul className="mt-4 grid gap-4">
                    {drafts.map((agent) => (
                      <AgentEditor
                        key={agent.id}
                        agent={agent}
                        onChange={(next) =>
                          commit(drafts.map((d) => (d.id === agent.id ? next : d)))
                        }
                        onDelete={() => commit(drafts.filter((d) => d.id !== agent.id))}
                        deleteBlockedReason={
                          total <= MIN_LENSES
                            ? `Deleting this leaves ${total - 1} lens(es); the council floor is ${MIN_LENSES}. Add another before removing this one.`
                            : null
                        }
                      />
                    ))}
                  </ul>
                )}
              </Panel>
            </Reveal>
          </div>
        </div>
      )}
    </Shell>
  );
}
