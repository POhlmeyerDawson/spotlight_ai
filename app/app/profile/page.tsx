"use client";

/**
 * Route `/profile` — the VC profile: what you said, what you did, and the gap.
 *
 * THE EMPTY STATE IS THE FIRST-CLASS PATH HERE, not an error and not a placeholder. A
 * fresh account has no survey and no upload, and `/auth/me` says so in a precise
 * sentence: "profile confidence 0.0 is below the 0.35 threshold; 12 unanswered
 * questions, 0 decisions uploaded". That sentence is rendered verbatim, because it is
 * the API's own account of why personalisation is off and it is more useful than
 * anything this page could write about it.
 *
 * What this page must never do is invent a profile to fill the space. No seeded persona,
 * no example fund, no plausible default weights — the product constraint is that
 * everything on screen is real data the user submitted. `derived.not_inferred` is
 * therefore rendered as content: the fields we deliberately did NOT derive, each with the
 * reason, sitting beside the ones we did.
 *
 * Signed out, this page does not redirect. It states what a session is for and links to
 * the objective ranking, which is unaffected either way (§1).
 */

import Link from "next/link";
import { useCallback, useEffect, useState } from "react";
import { getProfile, putProfile, type Derived, type Profile } from "@/lib/vc";
import { refreshSession, useSession } from "@/lib/useSession";
import Shell from "@/components/Shell";
import Reveal from "@/components/Reveal";
import SurveyPanel from "@/components/SurveyPanel";
import DecisionUpload from "@/components/DecisionUpload";
import GapPanel from "@/components/GapPanel";
import { EmptyState, ErrorNote, Loading, Panel, Stat } from "@/components/ui";

const THRESHOLD = 0.35;

/** The derived side, rendered so that an absent field is as legible as a present one. */
function DerivedPanel({ d }: { d: Derived }) {
  const weights = d.axis_weights_stated;
  const conviction = d.conviction_style_stated;

  return (
    <Panel
      title="what has been derived"
      subtitle="Every value names the submissions it came from. A field with no basis is absent and says why — never filled with a plausible default."
    >
      <div className="grid gap-3 sm:grid-cols-3">
        <Stat
          label="PROFILE CONFIDENCE"
          value={d.confidence.toFixed(3)}
          sub={`threshold ${THRESHOLD} for personalisation`}
        />
        <Stat
          label="TRADE-OFFS ANSWERED"
          value={`${d.survey_answered}/${d.survey_total}`}
          sub={
            d.survey_answered === d.survey_total
              ? "complete"
              : `${d.survey_total - d.survey_answered} unanswered, counted as absent`
          }
        />
        <Stat
          label="DECISIONS UPLOADED"
          value={d.decisions_count}
          sub={`${d.invested_count} invested — priors are computed over invested rows only`}
        />
      </div>

      <div className="mt-5 grid gap-4 md:grid-cols-2">
        <div className="border border-[color:var(--rule)] px-4 py-3">
          <div className="meta text-[color:var(--muted)]">AXIS WEIGHTS — STATED</div>
          {weights ? (
            <>
              <dl className="mono mt-2 grid gap-1 text-[14px]">
                <div className="flex justify-between">
                  <dt>founder</dt>
                  <dd>{weights.founder.toFixed(3)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>market</dt>
                  <dd>{weights.market.toFixed(3)}</dd>
                </div>
                <div className="flex justify-between">
                  <dt>idea vs market</dt>
                  <dd>{weights.idea_vs_market.toFixed(3)}</dd>
                </div>
              </dl>
              <p className="meta mt-2 text-[color:var(--muted)]">
                CONFIDENCE {weights.confidence.toFixed(2)} · N={weights.provenance.n}
                <span className="mt-1 block normal-case tracking-normal">
                  {weights.provenance.method}
                </span>
              </p>
            </>
          ) : (
            <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
              Not derived. Answer trade-offs that carry axis signals and this fills in.
            </p>
          )}
        </div>

        <div className="border border-[color:var(--rule)] px-4 py-3">
          <div className="meta text-[color:var(--muted)]">CONVICTION STYLE — STATED</div>
          {conviction ? (
            <>
              <p className="mono mt-2 text-[15px]">
                {conviction.label} ({conviction.score.toFixed(3)})
              </p>
              <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
                −1 evidence-heavy · +1 conviction-heavy
              </p>
              <p className="meta mt-2 text-[color:var(--muted)]">
                CONFIDENCE {conviction.confidence.toFixed(2)} · N={conviction.provenance.n}
              </p>
            </>
          ) : (
            <p className="caption mt-2 max-w-none text-[color:var(--muted)]">
              Not derived — no answered trade-off carried a conviction signal.
            </p>
          )}
        </div>
      </div>

      {(d.sector_priors.length > 0 || d.stage_priors.length > 0) && (
        <div className="mt-4 grid gap-4 md:grid-cols-2">
          {([
            ["SECTOR PRIORS — REVEALED", d.sector_priors],
            ["STAGE PRIORS — REVEALED", d.stage_priors],
          ] as const).map(([label, priors]) => (
            <div key={label} className="border border-[color:var(--rule)] px-4 py-3">
              <div className="meta text-[color:var(--muted)]">{label}</div>
              <ul className="mono mt-2 grid gap-1 text-[14px]">
                {priors.map((p) => (
                  <li key={p.key} className="flex justify-between">
                    <span>{p.key}</span>
                    <span>
                      {p.count} · {(p.share * 100).toFixed(0)}%
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          ))}
        </div>
      )}

      {d.red_lines.length > 0 && (
        <div className="mt-4 border border-[color:var(--rule)] px-4 py-3">
          <div className="meta text-[color:var(--muted)]">RED LINES</div>
          <ul className="mt-2 grid gap-2">
            {d.red_lines.map((r) => (
              <li key={r.statement} className="caption max-w-none">
                <span className="mono">{r.statement}</span>
                <span className="meta ml-2 text-[color:var(--muted)]">
                  {r.source === "stated" ? "STATED BY YOU" : "REVEALED CANDIDATE — CONFIRM BEFORE IT COUNTS"}
                  {" · CONF "}
                  {r.confidence.toFixed(2)}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {d.not_inferred.length > 0 && (
        <div className="mt-5 border-t border-[color:var(--rule)] pt-4">
          <h3 className="meta text-[color:var(--figure)]">
            DELIBERATELY NOT DERIVED
          </h3>
          <ul className="mt-2 grid gap-2">
            {d.not_inferred.map((n) => (
              <li
                key={n.field_name}
                className="hatch border border-dashed border-[color:var(--muted)] px-3 py-2"
              >
                <div className="meta text-[color:var(--figure)]">{n.field_name}</div>
                <p className="caption mt-1 max-w-none text-[color:var(--muted)]">
                  {n.reason}
                </p>
              </li>
            ))}
          </ul>
        </div>
      )}
    </Panel>
  );
}

/** Fund identity. `focus_sectors` is not decoration — the gap's sector dimension is
 *  uncomputable without a stated side, and this is that side. */
function IdentityPanel({
  profile,
  onSaved,
}: {
  profile: Profile;
  onSaved: () => void;
}) {
  const [fundName, setFundName] = useState(profile.fund_name ?? "");
  const [sectors, setSectors] = useState(profile.focus_sectors.join(", "));
  const [redLines, setRedLines] = useState(profile.stated_red_lines.join("\n"));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const split = (v: string, sep: RegExp) =>
    v.split(sep).map((s) => s.trim()).filter(Boolean);

  return (
    <Panel
      title="fund"
      subtitle="Stated preference you type directly. Focus sectors are the stated side the gap compares your actual sector concentration against — without them that dimension is uncomputable, not agreement."
    >
      <div className="grid gap-4">
        <label className="grid gap-1.5">
          <span className="meta text-[color:var(--muted)]">FUND NAME</span>
          <input
            value={fundName}
            onChange={(e) => setFundName(e.target.value)}
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
          />
        </label>
        <label className="grid gap-1.5">
          <span className="meta text-[color:var(--muted)]">
            FOCUS SECTORS — COMMA SEPARATED
          </span>
          <input
            value={sectors}
            onChange={(e) => setSectors(e.target.value)}
            placeholder="ai, infra, data"
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
          />
        </label>
        <label className="grid gap-1.5">
          <span className="meta text-[color:var(--muted)]">
            RED LINES — ONE PER LINE
          </span>
          <textarea
            rows={3}
            value={redLines}
            onChange={(e) => setRedLines(e.target.value)}
            placeholder="no single-founder companies without a technical co-founder"
            className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
          />
        </label>
        {error && <ErrorNote message={error} />}
        <div>
          <button
            type="button"
            disabled={busy}
            onClick={async () => {
              setBusy(true);
              setError(null);
              const r = await putProfile({
                fund_name: fundName.trim() || null,
                focus_sectors: split(sectors, /,/),
                stated_red_lines: split(redLines, /\n/),
              });
              setBusy(false);
              if (!r.ok) {
                setError(`PUT /profile: ${r.error}`);
                return;
              }
              onSaved();
            }}
            className="meta border px-4 py-2 disabled:opacity-50"
            style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
          >
            {busy ? "SAVING…" : "SAVE FUND DETAILS"}
          </button>
        </div>
      </div>
    </Panel>
  );
}

export default function ProfilePage() {
  const { me } = useSession();
  const [profile, setProfile] = useState<Profile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  /** State is written only after the await, so no effect body sets state synchronously. */
  const load = useCallback(async () => {
    const r = await getProfile();
    if (!r.ok) {
      // 401 is not an error here — it is the anonymous state, rendered below.
      setError(r.status === 401 ? null : `GET /profile: ${r.error}`);
      setProfile(null);
      return;
    }
    setError(null);
    setProfile(r.data);
  }, []);

  useEffect(() => {
    let live = true;
    void (async () => {
      if (!me?.authenticated) {
        await Promise.resolve();
        if (live) setProfile(null);
        return;
      }
      await load();
    })();
    return () => {
      live = false;
    };
  }, [me?.authenticated, load, reloadKey]);

  /** Anything that can move personalisation on or off refreshes both the profile and
   *  the frame's session badge, so the two can never disagree. */
  const changed = useCallback(() => {
    setReloadKey((k) => k + 1);
    void refreshSession();
  }, []);

  const signedOut = me !== null && !me.authenticated;

  return (
    <Shell
      title="your profile"
      lede={
        <span className="words">
          Two inputs, deliberately separate: what you say, and what you did.{" "}
          <em>Comparing them is the point.</em>
        </span>
      }
      crumbs={[{ label: "Account", href: "/" }, { label: "Profile" }]}
      meta={
        <>
          STATED: SURVEY
          <br />
          REVEALED: DECISIONS
          <br />
          NEVER MERGED
        </>
      }
    >
      {me === null && <Loading label="your session" />}

      {signedOut && (
        <Reveal>
          <Panel title="not signed in">
            <EmptyState
              title="a profile needs an owner"
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
              {me.reason}. The objective ranking, the claims, the evidence traces and the
              backtest are all open without one.
            </EmptyState>
          </Panel>
        </Reveal>
      )}

      {me?.authenticated && (
        <div className="grid gap-6">
          {error && <ErrorNote message={error} onRetry={() => void load()} />}

          <Reveal>
            <Panel
              title="personalisation status"
              subtitle="The API's own account of whether your profile is strong enough to re-rank with, and what is missing."
              emphasis={me.personalisation_enabled}
            >
              <p className="body-t max-w-[70ch]">{me.reason}</p>
              {!me.personalisation_enabled && (
                <div className="mt-4 flex flex-wrap gap-2">
                  <a
                    href="#survey"
                    className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
                  >
                    ANSWER THE TRADE-OFFS →
                  </a>
                  <a
                    href="#decisions"
                    className="meta border border-[color:var(--rule)] px-4 py-2"
                  >
                    UPLOAD A DECISION HISTORY →
                  </a>
                </div>
              )}
              {me.personalisation_enabled && (
                <div className="mt-4 flex flex-wrap gap-2">
                  <Link
                    href="/personal"
                    className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
                  >
                    YOUR RANK BESIDE CORE RANK →
                  </Link>
                  <Link
                    href="/council"
                    className="meta border border-[color:var(--rule)] px-4 py-2"
                  >
                    THE COUNCIL →
                  </Link>
                </div>
              )}
            </Panel>
          </Reveal>

          {profile === null && !error && <Loading label="your profile" />}

          {profile && (
            <>
              <Reveal delay={60}>
                <IdentityPanel profile={profile} onSaved={changed} />
              </Reveal>
              <Reveal delay={60}>
                <DerivedPanel d={profile.derived} />
              </Reveal>
            </>
          )}

          <div id="survey" className="scroll-mt-24">
            <Reveal delay={60}>
              <SurveyPanel onSaved={changed} />
            </Reveal>
          </div>

          <div id="decisions" className="scroll-mt-24">
            <Reveal delay={60}>
              <DecisionUpload onUploaded={changed} />
            </Reveal>
          </div>

          <Reveal delay={60}>
            <GapPanel reloadKey={reloadKey} />
          </Reveal>
        </div>
      )}
    </Shell>
  );
}
