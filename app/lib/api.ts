/**
 * Typed API client for the VC Brain backend.
 *
 * Every call is fallback-first: if the backend is down, slow, or returns a shape we
 * cannot use, we serve something renderable and mark the response `live: false`.
 * A blank screen during a 2.5-minute live demo is fatal — this module exists so that
 * cannot happen.
 *
 * NO FUNCTION IN THIS MODULE MAY REJECT. Every promise here resolves to a `Result`,
 * because the failure mode that actually shipped was a rejected promise leaving a
 * button disabled forever with nothing on screen to explain it. Callers still clear
 * their loading state in a `finally`, but they should never need to.
 *
 * The second rule is that a fallback is never ANOTHER RECORD. If we cannot get the
 * company you asked for, you get that company rendered thin — never a different
 * company's evidence wearing its name.
 */

import * as ad from "./adapt";
import * as fx from "./fixtures";
import type {
  Backtest,
  CompanyDetail,
  CompanySummary,
  Dissent,
  Memo,
  ProofProtocol,
  QueryResult,
  ScoreHistory,
  Thesis,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

/**
 * Timeouts are per-call because the calls are not alike. A list read that has not
 * answered in 2.5s is broken and should fall back while the demo keeps moving; a proof
 * challenge is a real LLM round trip and cutting it off at 2.5s would abort work that
 * was going to succeed. Every budget here is also the number the UI counts against, so
 * the progress bar and the abort agree on when "too long" is.
 */
export const TIMEOUT = {
  /**
   * Reads that back the page.
   *
   * Measured against the live backend, `/companies` answers in ~1.5s. A 2.5s budget
   * therefore sat inside the noise: under parallel load the list intermittently timed
   * out and the page silently dropped from 13 live companies to 6 fixture ones, which
   * is a far worse failure on stage than waiting another second. 8s clears the measured
   * latency by a wide margin and is still short enough that a genuinely dead backend
   * falls back before anyone in the room notices.
   */
  read: 8000,
  /** The compound query. Long enough to survive a cold Python import, then it errors. */
  query: 15_000,
  /** Proof generate/grade — genuine LLM calls, several seconds is normal. */
  llm: 60_000,
} as const;

/** Where the data on screen came from. Rendered in the header — we never fake liveness. */
export type Source = "live" | "fixture";

export interface Result<T> {
  data: T;
  source: Source;
  /** Set when a live call was attempted and failed. Shown in the UI, never swallowed. */
  note?: string;
  /**
   * True when the live call failed outright (as opposed to succeeding with a shape we
   * chose not to use). The UI uses this to decide whether to offer a retry.
   */
  failed?: boolean;
}

/** Thrown-free fetch. Distinguishes a timeout from every other failure, because the
 *  user-facing sentence is different: "took longer than Ns" invites a retry. */
async function get<T>(path: string, timeoutMs: number): Promise<T> {
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, timeoutMs);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      signal: ctrl.signal,
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } catch (e) {
    // Rounding 2500ms to "3s" makes the message disagree with the configured budget,
    // so sub-10s waits keep one decimal.
    if (timedOut) {
      const s = timeoutMs / 1000;
      throw new Error(`no response in ${s < 10 ? s.toFixed(1) : s.toFixed(0)}s`);
    }
    throw e instanceof Error ? e : new Error(String(e));
  } finally {
    clearTimeout(timer);
  }
}

const reason = (e: unknown) => (e instanceof Error ? e.message : String(e));

/**
 * Try the backend; on any failure fall back.
 * `adapt` returns null for a live response we cannot render, which degrades to the
 * fallback instead of rendering an empty page.
 */
async function withFallback<T>(
  path: string,
  fallback: T,
  adapt: (v: unknown) => T | null,
  timeoutMs: number = TIMEOUT.read,
): Promise<Result<T>> {
  try {
    const live = await get<unknown>(path, timeoutMs);
    const adapted = adapt(live);
    if (adapted === null) {
      return {
        data: fallback,
        source: "fixture",
        note: `${path} returned a shape this page cannot render — showing fixture`,
      };
    }
    return { data: adapted, source: "live" };
  } catch (e) {
    return {
      data: fallback,
      source: "fixture",
      note: `${path}: ${reason(e)}`,
      failed: true,
    };
  }
}

const isObj = ad.isObj;

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

export function getThesis(): Promise<Result<Thesis>> {
  return withFallback("/thesis", fx.THESIS, (v) =>
    isObj(v) && Array.isArray(v.sectors) && typeof v.risk_appetite === "number"
      ? (v as unknown as Thesis)
      : null,
  );
}

/**
 * `api/main.py` exposes GET /thesis only. POST is attempted anyway (the thesis panel
 * is meant to write) and the edit is kept in local state either way, so the demo's
 * opening beat works whether or not the write endpoint exists yet.
 */
export async function putThesis(t: Thesis): Promise<Result<Thesis>> {
  try {
    const res = await fetch(`${API_BASE}/thesis`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(t),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return { data: t, source: "live" };
  } catch (e) {
    return {
      data: t,
      source: "fixture",
      note: `POST /thesis: ${reason(e)} — kept locally`,
      failed: true,
    };
  }
}

export function getCompanies(): Promise<Result<CompanySummary[]>> {
  return withFallback("/companies", fx.COMPANIES, (v) =>
    Array.isArray(v) && v.length > 0 && isObj(v[0]) && isObj(v[0].axes)
      ? (v as unknown as CompanySummary[])
      : null,
  );
}

/**
 * Detail for one company.
 *
 * The fallback ladder is deliberate and its order is the whole point:
 *
 *   1. the live record, adapted;
 *   2. the hand-authored fixture, but ONLY on an exact id match;
 *   3. the summary the ranked list already holds, rendered as a sparse record.
 *
 * There is no step that substitutes a different company. The previous version ended
 * with `fx.companyDetail(fx.COMPANIES[0].id)!`, so clicking any of the thirteen live
 * companies — whose ids share nothing with the fixture ids — silently rendered the
 * first fixture company's events, claims and memo under the clicked company's URL.
 *
 * `summary` should be passed whenever the caller has it (the list always does). Without
 * it, step 3 is unavailable and a company with no live record and no fixture returns
 * null, which the page renders as an honest "not found".
 */
export async function getCompany(
  id: string,
  summary?: CompanySummary | null,
): Promise<Result<CompanyDetail> | null> {
  const fixture = fx.companyDetail(id);

  try {
    const live = await get<unknown>(`/companies/${encodeURIComponent(id)}`, TIMEOUT.read);
    const adapted = ad.toCompanyDetail(live, summary ?? null);
    if (adapted) return { data: adapted, source: "live" };
    if (fixture) {
      return {
        data: fixture,
        source: "fixture",
        note: `/companies/${id} returned a shape this page cannot render — showing fixture`,
      };
    }
    if (summary) {
      return {
        data: ad.sparseDetail(summary, "the detail endpoint returned a record this page cannot read"),
        source: "live",
        note: `/companies/${id} returned an unreadable detail — showing the screening record only`,
      };
    }
    return null;
  } catch (e) {
    const why = reason(e);
    if (fixture) {
      return { data: fixture, source: "fixture", note: `/companies/${id}: ${why}`, failed: true };
    }
    if (summary) {
      return {
        data: ad.sparseDetail(summary, `the detail endpoint answered "${why}"`),
        source: "fixture",
        note: `/companies/${id}: ${why}`,
        failed: true,
      };
    }
    return null;
  }
}

/**
 * Score history. Falls back to the copy that ships inside GET /companies/{id} before
 * falling back to fixtures, and returns an EMPTY history rather than another company's
 * when neither exists — a flat "no history recorded" panel is correct for the eight
 * companies assembled from the event log.
 */
export async function getScoreHistory(
  id: string,
  fromDetail?: ScoreHistory,
): Promise<Result<ScoreHistory>> {
  const fallback =
    fromDetail ?? fx.companyDetail(id)?.score_history ?? ad.emptyHistory();
  return withFallback(
    `/companies/${encodeURIComponent(id)}/score-history`,
    fallback,
    ad.toScoreHistory,
  );
}

export async function getMemo(id: string, dissentViewed: boolean): Promise<Result<Memo> | null> {
  const fixture = fx.memo(id, dissentViewed);
  const path = `/companies/${encodeURIComponent(id)}/memo?dissent_viewed=${dissentViewed}`;
  try {
    const live = await get<unknown>(path, TIMEOUT.read);
    const adapted = ad.toMemo(live, id);
    if (adapted) return { data: adapted, source: "live" };
    return fixture
      ? { data: fixture, source: "fixture", note: `${path} returned an unreadable memo` }
      : null;
  } catch (e) {
    return fixture
      ? { data: fixture, source: "fixture", note: `${path}: ${reason(e)}`, failed: true }
      : null;
  }
}

export async function getDissent(id: string): Promise<Result<Dissent> | null> {
  const fixture = fx.dissent(id);
  const path = `/companies/${encodeURIComponent(id)}/dissent`;
  try {
    const live = await get<unknown>(path, TIMEOUT.read);
    if (isObj(live) && typeof live.bear_case === "string") {
      return { data: live as unknown as Dissent, source: "live" };
    }
    return fixture
      ? { data: fixture, source: "fixture", note: `${path} returned an unreadable dissent` }
      : null;
  } catch (e) {
    return fixture
      ? { data: fixture, source: "fixture", note: `${path}: ${reason(e)}`, failed: true }
      : null;
  }
}

export function getBacktest(): Promise<Result<Backtest>> {
  return withFallback("/backtest", fx.BACKTEST, (v) =>
    isObj(v) && Array.isArray(v.trajectories) ? (v as unknown as Backtest) : null,
  );
}

// ---------------------------------------------------------------------------
// Proof Protocol — the slow calls
// ---------------------------------------------------------------------------

/** POST with the LLM budget. Returns a discriminated result; never rejects. */
async function post(path: string, body?: unknown): Promise<
  { ok: true; data: unknown } | { ok: false; error: string }
> {
  const ctrl = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    ctrl.abort();
  }, TIMEOUT.llm);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      method: "POST",
      signal: ctrl.signal,
      headers: { "content-type": "application/json", accept: "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const json: unknown = await res.json().catch(() => null);
    if (!res.ok) {
      // FastAPI puts the human-readable cause in `detail`; showing it beats "500".
      const detail = isObj(json) ? (json.detail as string | undefined) : undefined;
      return { ok: false, error: detail ?? `${res.status} ${res.statusText}` };
    }
    return { ok: true, data: json };
  } catch (e) {
    return {
      ok: false,
      error: timedOut ? `no response in ${TIMEOUT.llm / 1000}s` : reason(e),
    };
  } finally {
    clearTimeout(timer);
  }
}

export async function issueProof(
  id: string,
): Promise<{ ok: true; data: ProofProtocol } | { ok: false; error: string }> {
  const r = await post(`/companies/${encodeURIComponent(id)}/proof`);
  if (!r.ok) return r;
  const pp = ad.toProofProtocol(r.data);
  return pp
    ? { ok: true, data: pp }
    : { ok: false, error: "the challenge came back without a prompt or a central claim" };
}

export async function gradeProof(
  id: string,
  challengeId: string,
  submission: { artifact_url: string; trace: string },
): Promise<{ ok: true; data: ProofProtocol } | { ok: false; error: string }> {
  const r = await post(
    `/companies/${encodeURIComponent(id)}/proof/${encodeURIComponent(challengeId)}/grade`,
    submission,
  );
  if (!r.ok) return r;
  const pp = ad.toProofProtocol(r.data);
  return pp
    ? { ok: true, data: pp }
    : { ok: false, error: "the grade came back in a shape this panel cannot render" };
}

// ---------------------------------------------------------------------------
// NL compound query
// ---------------------------------------------------------------------------

/**
 * Local interpreter for the compound query, used when GET /query is unavailable.
 *
 * Deliberately shallow — it recognises the demo's vocabulary and reports back the
 * predicates it actually applied, so nothing on screen claims more than it did.
 *
 * It resolves ids ONLY against the company list it was handed, and it never
 * dereferences a detail record it does not have. Claim-level predicates are simply
 * SKIPPED when no detail is available for a company, and the skip is named in the
 * readback. The old version assumed every id in the list had a fixture behind it; with
 * live ids it did not, and the resulting failure took the whole button down with it.
 */
export function interpretQuery(
  q: string,
  companies: CompanySummary[],
  details: (id: string) => CompanyDetail | null,
): QueryResult {
  const s = q.toLowerCase().trim();
  const preds: string[] = [];
  let out = companies;

  /** Claim predicates need a detail record. Count how often we could not get one. */
  let unresolvable = 0;
  const byClaim = (
    label: string,
    test: (c: CompanyDetail) => boolean,
  ) => {
    out = out.filter((c) => {
      const d = details(c.id);
      if (!d) {
        unresolvable += 1;
        return false;
      }
      return test(d);
    });
    preds.push(label);
  };

  const sector = (needle: string, label: string) => {
    if (s.includes(needle)) {
      out = out.filter((c) => c.sector.toLowerCase().includes(label));
      preds.push(`sector ~ "${label}"`);
    }
  };
  sector("infra", "infra");
  sector("data", "data");
  if (/\bai\b|llm|model/.test(s)) {
    out = out.filter((c) => c.sector.toLowerCase().includes("ai"));
    preds.push('sector ~ "ai"');
  }

  if (/rising|rise|positive trend|momentum|improving/.test(s)) {
    out = out.filter((c) => (c.axes.founder.trend ?? 0) > 0);
    preds.push("founder.trend > 0");
  }
  if (/falling|declin|negative trend|deterior/.test(s)) {
    out = out.filter((c) => (c.axes.founder.trend ?? 0) < 0);
    preds.push("founder.trend < 0");
  }
  if (/unverified|unverifiable|not attempted|no verification/.test(s)) {
    byClaim("has claim in {UNVERIFIABLE, NOT_ATTEMPTED}", (d) =>
      d.claims.some((cl) => cl.status === "unverifiable" || cl.status === "not_attempted"),
    );
  }
  if (/contradict/.test(s)) {
    byClaim("has claim = CONTRADICTED", (d) =>
      d.claims.some((cl) => cl.status === "contradicted"),
    );
  }
  if (/revenue|arr|traction/.test(s)) {
    byClaim('claim_text ~ "revenue|arr|pilot|customer|partner|traction"', (d) =>
      d.claims.some((cl) =>
        /revenue|arr|pilot|customer|partner|traction/i.test(cl.claim_text),
      ),
    );
  }
  if (/cold start|no public|no footprint|invisible/.test(s)) {
    out = out.filter(
      (c) => c.gate === "proof_protocol" || /cold|invisible/i.test(c.archetype),
    );
    preds.push("gate = PROOF_PROTOCOL or archetype ~ cold/invisible");
  }
  if (/injection|adversarial|integrity|flag/.test(s)) {
    out = out.filter((c) => c.flag_count > 0);
    preds.push("flag_count > 0");
  }
  if (/proceed/.test(s)) {
    out = out.filter((c) => c.gate === "proceed");
    preds.push("gate = PROCEED");
  }
  if (/no call|no_call|pass\b|reject/.test(s)) {
    out = out.filter((c) => c.gate === "no_call");
    preds.push("gate = NO_CALL");
  }

  const parsed = preds.length ? preds.join(" · ") : "no predicate recognised — showing all";
  return {
    q,
    parsed:
      unresolvable > 0
        ? `${parsed} — offline reading; ${unresolvable} ${
            unresolvable === 1 ? "company has" : "companies have"
          } no local claim record and could not be tested`
        : parsed,
    company_ids: out.map((c) => c.id),
    count: out.length,
  };
}

/**
 * Run the compound query.
 *
 * Resolves to a `Result` in every case, including timeout and network failure — the
 * caller can always clear its loading state and always has something to show. The live
 * path is preferred because the server's `parsed` readback is the demo beat: it is the
 * proof that the model only translated the sentence and Python ran the filter.
 *
 * Ids that the server returns but the current list does not contain are reported rather
 * than dropped silently, because a mismatch there means the list and the query are
 * reading different worlds and that is worth knowing on stage.
 */
export async function runQuery(
  q: string,
  companies: CompanySummary[],
): Promise<Result<QueryResult>> {
  const local = interpretQuery(q, companies, (id) => fx.companyDetail(id));
  const path = `/query?q=${encodeURIComponent(q)}`;

  try {
    const live = await get<unknown>(path, TIMEOUT.query);
    const adapted = ad.toQueryResult(live, q);
    if (!adapted) {
      return {
        data: local,
        source: "fixture",
        note: `${path} returned no company_ids — read locally instead`,
      };
    }

    const known = new Set(companies.map((c) => c.id));
    const unknown = adapted.company_ids.filter((id) => !known.has(id));
    return {
      data: adapted,
      source: "live",
      note: unknown.length
        ? `${unknown.length} matched id(s) are not in the current list: ${unknown.join(", ")}`
        : undefined,
    };
  } catch (e) {
    return {
      data: local,
      source: "fixture",
      note: `${path}: ${reason(e)}`,
      failed: true,
    };
  }
}

export async function checkHealth(): Promise<boolean> {
  try {
    await get<unknown>("/health", TIMEOUT.read);
    return true;
  } catch {
    return false;
  }
}
