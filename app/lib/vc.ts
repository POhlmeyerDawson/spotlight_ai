/**
 * The personalisation client: session, VC profile, survey, decisions, gap, personal rank.
 *
 * WHY THIS IS NOT `lib/api.ts`. That module is fallback-first: when the backend is
 * unreachable it serves hand-authored fixtures so the objective product still renders.
 * That contract is exactly wrong here. A fixture profile is an INVENTED profile, and the
 * one rule this feature cannot break is that everything on screen about the user is
 * something the user actually submitted (docs/DIFFERENTIATOR.md §2.2 — a profile
 * inferred from four decisions must say so). So this module has no fixtures at all: a
 * failed call returns `{ ok: false, error }` and the page renders the failure, never a
 * plausible persona.
 *
 * What it shares with `lib/api.ts` is the transport discipline: NOTHING HERE REJECTS.
 * Every function resolves to a discriminated result, so no caller can be left with a
 * disabled button and nothing on screen to explain it.
 *
 * Two transport rules, both load-bearing:
 *   - `credentials: "include"` on every call. The session is an httpOnly cookie, so it
 *     is invisible to JS and travels only if the fetch asks for it. One missing flag
 *     here makes every authenticated route return 401 while login looks like it worked.
 *   - A 401 is a STATE, not an error. It means "anonymous", which is a legitimate,
 *     fully-rendering path (§1). Callers distinguish it via `status`.
 */

import { API_BASE, TIMEOUT } from "./api";

export type Res<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status?: number };

// ---------------------------------------------------------------------------
// Wire types. Mirrors of schema/vc.py — kept narrow to what the UI renders.
// ---------------------------------------------------------------------------

export interface Provenance {
  basis: string;
  method: string;
  question_ids: string[];
  decision_rows: number[];
  n: number;
}

export interface NotInferred {
  field_name: string;
  reason: string;
}

export interface User {
  user_id: string;
  email: string;
  created_at: string;
}

export interface Me {
  authenticated: boolean;
  user: User | null;
  personalisation_enabled: boolean;
  reason: string;
}

export interface SurveyOption {
  text: string;
  signals: Record<string, number>;
}

export interface SurveyQuestion {
  id: string;
  prompt: string;
  option_a: SurveyOption;
  option_b: SurveyOption;
}

export interface Survey {
  questions: SurveyQuestion[];
  /** question_id -> "a" | "b". Absent means UNANSWERED, which is not the same as neutral. */
  answers: Record<string, string>;
  answered: number;
  total: number;
}

export interface AxisWeights {
  founder: number;
  market: number;
  idea_vs_market: number;
  provenance: Provenance;
  confidence: number;
}

export interface ConvictionStyle {
  score: number;
  label: string;
  provenance: Provenance;
  confidence: number;
}

export interface Prior {
  key: string;
  count: number;
  share: number;
  provenance: Provenance;
}

export interface RedLine {
  statement: string;
  source: string;
  provenance: Provenance;
  confidence: number;
}

export interface Derived {
  axis_weights_stated: AxisWeights | null;
  axis_weights_revealed: AxisWeights | null;
  conviction_style_stated: ConvictionStyle | null;
  conviction_style_revealed: ConvictionStyle | null;
  sector_priors: Prior[];
  stage_priors: Prior[];
  red_lines: RedLine[];
  survey_answered: number;
  survey_total: number;
  decisions_count: number;
  invested_count: number;
  confidence: number;
  personalisation_enabled: boolean;
  personalisation_reason: string;
  not_inferred: NotInferred[];
}

export interface Profile {
  profile_id: string;
  user_id: string;
  fund_name: string | null;
  focus_sectors: string[];
  stated_red_lines: string[];
  updated_at: string;
  derived: Derived;
}

export interface RejectedRow {
  row_number: number;
  reason: string;
  raw: string;
}

export interface UploadResult {
  accepted: number;
  rejected: RejectedRow[];
  warnings: RejectedRow[];
  total_rows: number;
}

export interface GapFinding {
  dimension: string;
  stated: string;
  revealed: string;
  finding: string;
  magnitude: number;
  provenance: Provenance;
  confidence: number;
}

export interface GapUncomputable {
  dimension: string;
  missing: string;
  reason: string;
}

export interface GapReport {
  findings: GapFinding[];
  uncomputable: GapUncomputable[];
  agreements: string[];
  computed_at: string;
  personalisation_enabled: boolean;
  personalisation_reason: string;
}

export interface Lens {
  kind: string;
  persona: string;
  weight: number;
  justified_by: string[];
  provenance: Provenance;
  confidence: number;
}

export interface PersonalRankRow {
  company_id: string;
  name: string;
  core_rank: number;
  personal_rank: number;
  fit_score: number;
  core_weakest_score: number;
  /** Positive = the personal layer PROMOTED this against core. */
  divergence: number;
  top_lens: string | null;
  why: string;
}

export interface Disagreement {
  company_id: string;
  name: string;
  core_rank: number;
  personal_rank: number;
  divergence: number;
  explanation: string;
}

export interface CoreRankRow {
  company_id: string;
  name: string;
  core_rank: number;
}

export interface PersonalRanking {
  as_of: string;
  personalised: boolean;
  reason: string;
  lenses: Lens[];
  lenses_not_derived: NotInferred[];
  rows: PersonalRankRow[];
  disagreements: Disagreement[];
  agreements: string[];
  /** Served unconditionally, including when personalisation is off (§3). */
  core_rank: CoreRankRow[];
}

// ---------------------------------------------------------------------------
// Transport
// ---------------------------------------------------------------------------

/**
 * One request. Resolves, always.
 *
 * FastAPI puts the human-readable cause in `detail`, and on the personal routes that
 * `detail` is sometimes an object carrying the reason personalisation is off — which is
 * the most useful sentence on the screen, so it is unwrapped rather than stringified
 * into "[object Object]".
 */
async function call<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<Res<T>> {
  const { timeoutMs = TIMEOUT.read, ...rest } = init;
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      ...rest,
      signal: ctrl.signal,
      cache: "no-store",
      // The whole feature depends on this line.
      credentials: "include",
      headers: { accept: "application/json", ...(rest.headers ?? {}) },
    });
    const body: unknown = await res.json().catch(() => null);
    if (!res.ok) {
      return { ok: false, error: detailOf(body) ?? `${res.status} ${res.statusText}`, status: res.status };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return {
      ok: false,
      error: timedOut
        ? `no response in ${timeoutMs / 1000}s`
        : e instanceof Error
          ? e.message
          : String(e),
    };
  } finally {
    clearTimeout(timer);
  }
}

function detailOf(body: unknown): string | null {
  if (!body || typeof body !== "object") return null;
  const detail = (body as { detail?: unknown }).detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object") {
    const d = detail as { error?: unknown; reason?: unknown };
    const parts = [d.error, d.reason].filter((p): p is string => typeof p === "string");
    if (parts.length) return parts.join(" — ");
  }
  return null;
}

const json = (body: unknown): RequestInit => ({
  method: "POST",
  headers: { "content-type": "application/json" },
  body: JSON.stringify(body),
});

// ---------------------------------------------------------------------------
// Auth
// ---------------------------------------------------------------------------

/** The anonymous state, rendered when `/auth/me` cannot be reached at all.
 *
 *  This is NOT a fixture in the `lib/api.ts` sense — it invents nothing about a user.
 *  It is the honest reading of "we could not establish a session", and it is what keeps
 *  a dead auth service from taking the core product down with it (§1). */
export function anonymous(reason: string): Me {
  return { authenticated: false, user: null, personalisation_enabled: false, reason };
}

export async function getMe(): Promise<Me> {
  const r = await call<Me>("/auth/me");
  if (r.ok && typeof r.data?.authenticated === "boolean") return r.data;
  return anonymous(
    r.ok
      ? "/auth/me returned a shape this client cannot read — treating the session as absent"
      : `the session could not be checked (${r.error}) — the core objective ranking is unaffected`,
  );
}

/**
 * Register. The API deliberately DOES disclose that an email is taken here (see
 * `api/routers/auth.py`), because the alternative is telling a returning user "success"
 * and then failing their login. Login is the route that must not disclose, and does not.
 */
export function register(
  email: string,
  password: string,
  fundName?: string,
): Promise<Res<{ user: User }>> {
  return call<{ user: User }>(
    "/auth/register",
    json({ email, password, fund_name: fundName?.trim() || null }),
  );
}

export function login(email: string, password: string): Promise<Res<{ user: User }>> {
  return call<{ user: User }>("/auth/login", json({ email, password }));
}

export function logout(): Promise<Res<unknown>> {
  return call<unknown>("/auth/logout", { method: "POST" });
}

// ---------------------------------------------------------------------------
// Profile
// ---------------------------------------------------------------------------

export const getProfile = () => call<Profile>("/profile");

export const putProfile = (body: {
  fund_name?: string | null;
  focus_sectors?: string[];
  stated_red_lines?: string[];
}) => call<Profile>("/profile", { ...json(body), method: "PUT" });

export const getSurvey = () => call<Survey>("/profile/survey");

export const postSurvey = (answers: { question_id: string; choice: string }[]) =>
  call<{ stored: number; answered: number; total: number; derived: Derived }>(
    "/profile/survey",
    json({ answers }),
  );

/** The upload posts the file's text as the raw body; the API detects CSV vs JSON from
 *  the CONTENT, so the extension is never treated as a fact. */
export const postDecisions = (content: string) =>
  call<{ upload: UploadResult; derived: Derived }>("/profile/decisions", {
    method: "POST",
    headers: { "content-type": "text/plain" },
    body: content,
    timeoutMs: TIMEOUT.query,
  });

export const getGap = () => call<GapReport>("/profile/gap");

// ---------------------------------------------------------------------------
// Personal layer
// ---------------------------------------------------------------------------

export const getPersonalRank = () =>
  call<PersonalRanking>("/personal/rank", { timeoutMs: TIMEOUT.query });

export interface LensSet {
  personalisation_enabled: boolean;
  personalisation_reason: string;
  profile_confidence: number;
  lenses: Lens[];
  not_derived: NotInferred[];
  min_lenses: number;
  max_lenses: number;
  sufficient: boolean;
}

/**
 * The DERIVED council. Read-only, and that is a fact about the API rather than a choice
 * made here: `api/routers/personal.py` exposes GET only — there is no create, update or
 * delete route for a lens, and `PUT /profile` accepts only fund_name, focus_sectors and
 * stated_red_lines. User-authored agents therefore live in `lib/councilDraft.ts` until
 * a write route exists, and the council screen says so rather than implying otherwise.
 */
export const getLenses = () => call<LensSet>("/personal/lenses");
