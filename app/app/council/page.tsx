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
 *   Stored ON THE ACCOUNT via `POST/PUT/DELETE /personal/lenses`, and scored by the
 *   ranking: `compose_council` folds these in beside the derived lenses at the weights
 *   `/personal/rank` actually uses.
 *
 * Keeping the two visibly apart is what makes the stated-vs-revealed gap mean anything:
 * if an authored agent could pass itself off as derived, "what you said" and "what you
 * did" would no longer be separable, which is the one thing §0 forbids.
 *
 * PERSISTENCE: every write route returns the same full council payload the GET does, and
 * this page replaces its state with that payload wholesale — the screen never shows a
 * council the server does not hold. An agent BEING TYPED is different: it lives in a
 * local draft until SAVE, and is labelled "not saved yet" for exactly as long as that
 * is true.
 *
 * The bounds are the backend's and the backend enforces them by REFUSING with a reason
 * (422 for an unreadable quality, 409 at the ceiling). Those refusals are rendered
 * verbatim — they are better copy than anything this page could invent.
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import {
  createLens,
  deleteLens,
  getLenses,
  updateLens,
  type AuthoredLensRecord,
  type LensSet,
  type LensWrite,
} from "@/lib/vc";
import { useSession } from "@/lib/useSession";
import {
  MAX_LENSES,
  MIN_AUTHORED_WEIGHT,
  MIN_LENSES,
  newAgent,
  templateAgents,
  type DraftAgent,
} from "@/lib/councilDraft";
import Shell from "@/components/Shell";
import Reveal from "@/components/Reveal";
import { EmptyState, ErrorNote, Loading, Panel } from "@/components/ui";

/** The editable fields, shared by the new-agent draft and the stored-record editor. */
function AgentFields({
  value,
  onChange,
  disabled,
}: {
  value: LensWrite;
  onChange: (next: LensWrite) => void;
  disabled: boolean;
}) {
  const set = <K extends keyof LensWrite>(key: K, v: LensWrite[K]) =>
    onChange({ ...value, [key]: v });

  return (
    <>
      <div className="mt-3 grid gap-3 sm:grid-cols-2">
        <label className="grid gap-1.5">
          <span className="meta text-[color:var(--muted)]">NAME</span>
          <input
            value={value.name}
            disabled={disabled}
            onChange={(e) => set("name", e.target.value)}
            placeholder="CyberSecurity Agent"
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px] disabled:opacity-50"
          />
        </label>
        <label className="grid gap-1.5">
          <span className="meta text-[color:var(--muted)]">
            QUALITY IT ADDS SCORE FOR
          </span>
          <input
            value={value.quality}
            disabled={disabled}
            onChange={(e) => set("quality", e.target.value)}
            placeholder="security_engineering"
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px] disabled:opacity-50"
          />
        </label>
      </div>

      <label className="mt-3 grid gap-1.5">
        <span className="meta text-[color:var(--muted)]">
          WHAT IT ARGUES — PLAIN LANGUAGE
        </span>
        <textarea
          rows={3}
          value={value.persona}
          disabled={disabled}
          onChange={(e) => set("persona", e.target.value)}
          placeholder="You add score for founders who treat security as an engineering discipline…"
          className="border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px] leading-snug disabled:opacity-50"
        />
      </label>

      <label className="mt-3 grid gap-1.5">
        <span className="meta flex justify-between text-[color:var(--muted)]">
          <span>WEIGHT</span>
          <span className="mono">{value.weight.toFixed(2)}</span>
        </span>
        <input
          type="range"
          min={MIN_AUTHORED_WEIGHT}
          max={1}
          step={0.01}
          value={value.weight}
          disabled={disabled}
          onChange={(e) => set("weight", Number(e.target.value))}
          className="accent-[color:var(--accent)]"
        />
      </label>
    </>
  );
}

/** A stored agent: renders the record, PUTs on save, DELETEs on delete. */
function StoredAgentEditor({
  record,
  busy,
  onSave,
  onDelete,
  deleteBlockedReason,
}: {
  record: AuthoredLensRecord;
  busy: boolean;
  onSave: (patch: LensWrite) => Promise<boolean>;
  onDelete: () => void;
  deleteBlockedReason: string | null;
}) {
  const [edit, setEdit] = useState<LensWrite>({
    name: record.name,
    quality: record.quality,
    persona: record.persona,
    weight: record.weight,
    origin: record.origin,
  });
  const dirty =
    edit.name !== record.name ||
    edit.quality !== record.quality ||
    edit.persona !== record.persona ||
    edit.weight !== record.weight;

  return (
    <li
      className="border px-4 py-4"
      style={{
        borderColor: record.origin === "template" ? "var(--muted)" : "var(--rule)",
        borderStyle: record.origin === "template" ? "dashed" : "solid",
      }}
    >
      <div className="meta flex flex-wrap items-center justify-between gap-2 text-[color:var(--muted)]">
        <span>
          {record.origin === "template"
            ? "TEMPLATE YOU ACCEPTED — SAVED TO YOUR ACCOUNT"
            : "AUTHORED BY YOU — SAVED TO YOUR ACCOUNT"}
        </span>
        <span className="flex gap-2">
          {dirty && (
            <button
              type="button"
              disabled={busy}
              onClick={() => void onSave(edit)}
              className="border px-2 py-1 disabled:opacity-50"
              style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
            >
              SAVE CHANGES
            </button>
          )}
          <button
            type="button"
            onClick={onDelete}
            disabled={busy || Boolean(deleteBlockedReason)}
            title={deleteBlockedReason ?? "remove this agent from your account"}
            className="border border-[color:var(--rule)] px-2 py-1 disabled:opacity-50"
          >
            DELETE
          </button>
        </span>
      </div>
      {deleteBlockedReason && (
        <p className="caption mt-1.5 max-w-none text-[color:var(--muted)]">
          {deleteBlockedReason}
        </p>
      )}
      <AgentFields value={edit} onChange={setEdit} disabled={busy} />
      {dirty && (
        <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
          Edited here, not saved yet — SAVE CHANGES sends it to your account.
        </p>
      )}
    </li>
  );
}

export default function CouncilPage() {
  const { me } = useSession();
  const [lensSet, setLensSet] = useState<LensSet | null>(null);
  const [lensError, setLensError] = useState<string | null>(null);

  /** Agents being typed, before their first save. Ephemeral on purpose. */
  const [drafts, setDrafts] = useState<DraftAgent[]>([]);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<string | null>(null);
  const [refusal, setRefusal] = useState<string | null>(null);

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

  const derivedCount = lensSet?.lenses.length ?? 0;
  const authored = lensSet?.authored ?? [];
  const total = derivedCount + authored.length + drafts.length;

  /** One write, one shared protocol: council state is REPLACED by the server's answer. */
  async function write(
    op: () => Promise<
      { ok: true; data: LensSet } | { ok: false; error: string; status?: number }
    >,
    saved: string,
  ): Promise<boolean> {
    setBusy(true);
    setRefusal(null);
    setNotice(null);
    try {
      const r = await op();
      if (!r.ok) {
        setRefusal(r.error);
        return false;
      }
      setLensSet(r.data);
      setNotice(saved);
      return true;
    } finally {
      setBusy(false);
    }
  }

  function addDraft() {
    setRefusal(null);
    if (total >= MAX_LENSES) {
      setRefusal(
        `The council is capped at ${MAX_LENSES} lenses and you already have ${total} ` +
          `(${derivedCount} derived, ${authored.length} authored, ${drafts.length} being typed). ` +
          `Delete one, or lower a derived lens by changing the answers that produced it — nothing is being clamped silently.`,
      );
      return;
    }
    setDrafts((d) => [...d, newAgent()]);
  }

  async function saveDraft(draft: DraftAgent) {
    const ok = await write(
      () =>
        createLens({
          name: draft.name,
          quality: draft.quality,
          persona: draft.persona,
          weight: draft.weight,
          origin: draft.origin,
        }),
      "Saved to your account. It scores the ranking now — YOUR RANK shows what it moved.",
    );
    if (ok) setDrafts((d) => d.filter((x) => x.id !== draft.id));
  }

  async function applyTemplate() {
    setRefusal(null);
    const room = MAX_LENSES - derivedCount - authored.length;
    if (room <= 0) {
      setRefusal(
        `Your council already holds ${derivedCount + authored.length} lenses, which fills the ${MAX_LENSES}-lens ceiling. The template set has nowhere to go.`,
      );
      return;
    }
    // Saved to the account one by one; the LAST response is the whole council, so
    // state converges on the server's view no matter how many landed.
    const template = templateAgents().slice(0, room);
    setBusy(true);
    setNotice(null);
    try {
      let last: LensSet | null = null;
      let failure: string | null = null;
      let created = 0;
      for (const t of template) {
        const r = await createLens({
          name: t.name,
          quality: t.quality,
          persona: t.persona,
          weight: t.weight,
          origin: t.origin,
        });
        if (!r.ok) {
          failure = r.error;
          break;
        }
        last = r.data;
        created += 1;
      }
      if (last) setLensSet(last);
      if (failure) setRefusal(failure);
      setNotice(
        created === 0
          ? null
          : created < templateAgents().length
            ? `Saved ${created} of ${templateAgents().length} template agents to your account — the rest would exceed the ${MAX_LENSES}-lens ceiling alongside your ${derivedCount} derived lenses.`
            : "Template set saved to your account. Edit any of them — they are yours now.",
      );
    } finally {
      setBusy(false);
    }
  }

  async function clearAuthored() {
    setBusy(true);
    setNotice(null);
    setRefusal(null);
    try {
      let last: LensSet | null = null;
      for (const record of authored) {
        const r = await deleteLens(record.lens_id);
        if (!r.ok) {
          setRefusal(r.error);
          break;
        }
        last = r.data;
      }
      if (last) setLensSet(last);
      setDrafts([]);
      if (last) setNotice("Authored agents removed from your account.");
    } finally {
      setBusy(false);
    }
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
                On your account, both halves. Derived lenses are read out of your survey
                answers and decisions; authored agents are saved through{" "}
                <span className="mono">POST/PUT/DELETE /personal/lenses</span> and score
                the ranking beside them — YOUR RANK explains every move by the lens that
                caused it. An agent you are still typing is labelled as unsaved until the
                moment it is not.
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
                      {derivedCount} DERIVED · {authored.length} AUTHORED
                      {drafts.length > 0 ? ` · ${drafts.length} UNSAVED` : ""}
                    </span>
                  </div>
                }
              >
                <div className="flex flex-wrap gap-2">
                  <button
                    type="button"
                    onClick={addDraft}
                    disabled={busy}
                    className="meta border px-4 py-2 disabled:opacity-50"
                    style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
                  >
                    + NEW AGENT
                  </button>
                  <button
                    type="button"
                    onClick={() => void applyTemplate()}
                    disabled={busy}
                    className="meta border border-[color:var(--rule)] px-4 py-2 disabled:opacity-50"
                    title="Saves a labelled template set to your account for you to edit. It is not derived from your history."
                  >
                    USE THE TEMPLATE SET
                  </button>
                  {authored.length > 0 && (
                    <button
                      type="button"
                      onClick={() => void clearAuthored()}
                      disabled={busy}
                      className="meta border border-[color:var(--rule)] px-4 py-2 disabled:opacity-50"
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

                {authored.length === 0 && drafts.length === 0 ? (
                  <div className="mt-4">
                    <EmptyState title="no authored agents">
                      Add one, or start from the template set and edit it. The template is
                      labelled as a template wherever it appears — it is a starting point
                      you accepted, never something read out of your history.
                    </EmptyState>
                  </div>
                ) : (
                  <ul className="mt-4 grid gap-4">
                    {authored.map((record) => (
                      <StoredAgentEditor
                        key={record.lens_id}
                        record={record}
                        busy={busy}
                        onSave={(patch) =>
                          write(
                            () => updateLens(record.lens_id, patch),
                            "Saved to your account. The change scores the ranking now.",
                          )
                        }
                        onDelete={() =>
                          void write(
                            () => deleteLens(record.lens_id),
                            "Removed from your account. It has stopped scoring.",
                          )
                        }
                        deleteBlockedReason={
                          derivedCount + authored.length <= MIN_LENSES
                            ? `Deleting this leaves ${derivedCount + authored.length - 1} lens(es); the council floor is ${MIN_LENSES}. Add another before removing this one.`
                            : null
                        }
                      />
                    ))}
                    {drafts.map((draft) => (
                      <li
                        key={draft.id}
                        className="border border-dashed px-4 py-4"
                        style={{ borderColor: "var(--accent)" }}
                      >
                        <div className="meta flex flex-wrap items-center justify-between gap-2 text-[color:var(--muted)]">
                          <span>BEING TYPED — NOT SAVED YET</span>
                          <span className="flex gap-2">
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() => void saveDraft(draft)}
                              className="border px-2 py-1 disabled:opacity-50"
                              style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
                            >
                              SAVE TO ACCOUNT
                            </button>
                            <button
                              type="button"
                              disabled={busy}
                              onClick={() =>
                                setDrafts((d) => d.filter((x) => x.id !== draft.id))
                              }
                              className="border border-[color:var(--rule)] px-2 py-1 disabled:opacity-50"
                            >
                              DISCARD
                            </button>
                          </span>
                        </div>
                        <AgentFields
                          value={draft}
                          disabled={busy}
                          onChange={(next) =>
                            setDrafts((d) =>
                              d.map((x) => (x.id === draft.id ? { ...x, ...next } : x)),
                            )
                          }
                        />
                      </li>
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
