"use client";

/**
 * Route `/` — the application's entry point: sign in, or don't.
 *
 * THE CONSTRAINT THIS PAGE EXISTS TO HONOUR (docs/DIFFERENTIATOR.md §1): the core
 * product works logged out. So this is an entry point, not a gate. There is no redirect
 * anywhere in this file — an unauthenticated visitor is offered a form AND a link
 * straight to the objective ranking, with equal weight and the difference stated in
 * words. A login that is broken, slow, or unreachable therefore costs personalisation
 * and nothing else; it can never produce a blank page or a redirect loop, because there
 * is no redirect to loop.
 *
 * Failure messaging on LOGIN is fixed and generic — one sentence for a wrong password,
 * an unknown address, and a malformed one alike. Anything else turns the form into an
 * oracle for which emails have accounts, which is the same reason `api/routers/auth.py`
 * returns one 401 for every credential failure.
 *
 * REGISTER is the deliberate asymmetry: it does say when an address is taken, because
 * the alternative is telling a returning user "success" and then failing their login.
 */

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useState } from "react";
import { useSession } from "@/lib/useSession";
import { login, register } from "@/lib/vc";
import Shell from "@/components/Shell";
import Reveal from "@/components/Reveal";
import { Busy, ErrorNote, Panel } from "@/components/ui";
import { TIMEOUT } from "@/lib/api";

type Mode = "login" | "register";

/** The one sentence a failed sign-in is allowed to say. */
const GENERIC_FAILURE =
  "that email and password combination was not recognised. nothing about which half was wrong is disclosed, deliberately.";

const MIN_PASSWORD = 10;

export default function EntryPage() {
  const router = useRouter();
  const { me, refresh } = useSession();

  const [mode, setMode] = useState<Mode>("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fundName, setFundName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError(null);

    // Checked here as well as server-side so the failure names the actual problem
    // instead of arriving as a 422 the user has to interpret. This is a FORMAT rule,
    // not a credential check — it discloses nothing about who has an account.
    if (mode === "register" && password.length < MIN_PASSWORD) {
      setError(`the password needs at least ${MIN_PASSWORD} characters. length is the only requirement.`);
      return;
    }

    setBusy(true);
    try {
      const r =
        mode === "login"
          ? await login(email, password)
          : await register(email, password, fundName);

      if (!r.ok) {
        if (mode === "login") {
          // 429 is a throttle, not a credential answer — saying so is not disclosure and
          // withholding it would leave a user staring at "not recognised" for a password
          // that is in fact correct.
          setError(r.status === 429 ? r.error : r.status ? GENERIC_FAILURE : r.error);
        } else {
          setError(r.error);
        }
        return;
      }

      await refresh();
      // The profile page is where a fresh account has something to do. It states, in the
      // API's own words, why personalisation is off and what would turn it on.
      router.push("/profile");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Shell
      title={me?.authenticated ? "your account" : "sign in"}
      lede={
        <span className="words">
          The objective ranking is open to everyone.{" "}
          <em>A session buys personalisation, and nothing else.</em>
        </span>
      }
      meta={
        <>
          VC BRAIN
          <br />
          ENTRY POINT
          <br />
          SESSION: HTTPONLY COOKIE
        </>
      }
    >
      <div className="grid gap-6 lg:grid-cols-[minmax(0,1fr)_minmax(0,1fr)]">
        <Reveal>
          {me?.authenticated ? (
            <Panel
              title="signed in"
              subtitle="Everything below is scoped to this account. Nothing here is shared with the objective scorer."
            >
              <dl className="grid gap-3">
                <div>
                  <dt className="meta text-[color:var(--muted)]">ACCOUNT</dt>
                  <dd className="mono mt-1 text-[15px]">{me.user?.email}</dd>
                </div>
                <div>
                  <dt className="meta text-[color:var(--muted)]">PERSONALISATION</dt>
                  <dd className="mono mt-1 text-[15px]">
                    {me.personalisation_enabled ? "ON" : "OFF"}
                  </dd>
                  <dd className="caption mt-1 max-w-none text-[color:var(--muted)]">
                    {me.reason}
                  </dd>
                </div>
              </dl>
              <div className="mt-5 flex flex-wrap gap-2">
                <Link
                  href="/profile"
                  className="meta border border-[color:var(--accent)] px-4 py-2 text-[color:var(--accent)]"
                >
                  PROFILE, SURVEY &amp; UPLOAD
                </Link>
                <Link
                  href="/personal"
                  className="meta border border-[color:var(--rule)] px-4 py-2"
                >
                  YOUR RANK VS CORE
                </Link>
              </div>
            </Panel>
          ) : (
            <Panel
              title={mode === "login" ? "sign in" : "create an account"}
              subtitle={
                mode === "login"
                  ? "Email and password. No third-party identity provider — an OAuth callback is one more thing to fail on a conference network."
                  : "An account owns exactly one VC profile. Nothing is derived until you submit a survey or a decision history."
              }
              right={
                <button
                  type="button"
                  onClick={() => {
                    setMode(mode === "login" ? "register" : "login");
                    setError(null);
                  }}
                  className="meta border border-[color:var(--rule)] px-3 py-1.5"
                >
                  {mode === "login" ? "REGISTER INSTEAD" : "SIGN IN INSTEAD"}
                </button>
              }
            >
              <form onSubmit={submit} className="grid gap-4">
                <label className="grid gap-1.5">
                  <span className="meta text-[color:var(--muted)]">EMAIL</span>
                  <input
                    type="email"
                    required
                    autoComplete="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                    className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
                  />
                </label>

                <label className="grid gap-1.5">
                  <span className="meta text-[color:var(--muted)]">PASSWORD</span>
                  <input
                    type="password"
                    required
                    minLength={mode === "register" ? MIN_PASSWORD : undefined}
                    autoComplete={mode === "login" ? "current-password" : "new-password"}
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
                  />
                  {mode === "register" && (
                    <span className="caption max-w-none text-[color:var(--muted)]">
                      At least {MIN_PASSWORD} characters. Length is the only rule with
                      evidence behind it, so it is the only rule.
                    </span>
                  )}
                </label>

                {mode === "register" && (
                  <label className="grid gap-1.5">
                    <span className="meta text-[color:var(--muted)]">
                      FUND NAME — OPTIONAL
                    </span>
                    <input
                      type="text"
                      value={fundName}
                      onChange={(e) => setFundName(e.target.value)}
                      className="mono border border-[color:var(--rule)] bg-transparent px-3 py-2 text-[15px]"
                    />
                  </label>
                )}

                {error && <ErrorNote message={error} />}

                <div className="flex items-center gap-3">
                  <button
                    type="submit"
                    disabled={busy}
                    className="meta border px-4 py-2 disabled:opacity-50"
                    style={{ borderColor: "var(--accent)", color: "var(--accent)" }}
                  >
                    {mode === "login" ? "SIGN IN" : "CREATE ACCOUNT"}
                  </button>
                  {busy && (
                    <Busy
                      className="min-w-[180px]"
                      label={mode === "login" ? "VERIFYING…" : "CREATING ACCOUNT…"}
                      budgetMs={TIMEOUT.read}
                    />
                  )}
                </div>
              </form>
            </Panel>
          )}
        </Reveal>

        <Reveal delay={60}>
          <Panel
            title="you do not have to sign in"
            subtitle="The split is architectural, not a paywall: preference must never move an objective score, so the two live apart."
          >
            <div className="grid gap-4">
              <div className="border border-[color:var(--rule)] px-4 py-3">
                <div className="meta text-[color:var(--muted)]">OPEN TO EVERYONE</div>
                <p className="caption mt-1.5 max-w-none">
                  The core rank, all three axes, every claim with its status, the
                  evidence trace, the dissent, and the backtest. None of it changes when
                  you sign in — the same founder is not more capable at a bolder fund.
                </p>
                <div className="mt-3 flex flex-wrap gap-2">
                  <Link
                    href="/pipeline"
                    className="meta border border-[color:var(--accent)] px-3 py-1.5 text-[color:var(--accent)]"
                  >
                    CONTINUE WITHOUT SIGNING IN →
                  </Link>
                  <Link
                    href="/plates"
                    className="meta border border-[color:var(--rule)] px-3 py-1.5"
                  >
                    THE PLATES
                  </Link>
                </div>
              </div>

              <div className="border border-dashed border-[color:var(--rule)] px-4 py-3">
                <div className="meta text-[color:var(--muted)]">
                  NEEDS A SESSION
                </div>
                <p className="caption mt-1.5 max-w-none">
                  A personal re-rank beside the core one, the council lenses derived from
                  your own history, and the stated-vs-revealed gap. All of it is built
                  from what you submit — there is no seeded persona and no default
                  profile anywhere in this application.
                </p>
              </div>
            </div>
          </Panel>
        </Reveal>
      </div>
    </Shell>
  );
}
