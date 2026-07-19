/**
 * Typed API client for the VC Brain backend.
 *
 * Every call degrades rather than blanking: if the backend is down, slow, or returns a
 * shape we cannot use, we render an honest empty state and say so. A blank screen during
 * a 2.5-minute live demo is fatal — this module exists so that cannot happen.
 *
 * A FALLBACK MAY DEGRADE. IT MUST NEVER FABRICATE.
 *
 * When a call fails this module serves `lib/fixtures.ts`. The page-level banner says
 * "FIXTURE DATA", which sounds like enough, and was not. One layer down, those records
 * used to carry citations shaped exactly like real ones: `vcbrain.local/proof/...` links
 * that went nowhere, and `arxiv.org/abs/2401.09417`,
 * `news.ycombinator.com/item?id=38911204` links that went SOMEWHERE — to real, unrelated
 * papers and threads. A reviewer who accepted "this is fixture data", then clicked a
 * citation to check it, landed on a real arXiv paper about something else entirely. The
 * banner was true and the receipts still lied.
 *
 * Every citation in `fixtures.ts` is therefore now visibly non-real ON INSPECTION —
 * the reserved `example.invalid` TLD (RFC 2606, never delegated) or the non-web `deck://`
 * and `proof://` schemes, the same convention the seed corpus uses and
 * `tests/test_constructed_citations.py` enforces. Nothing a fixture cites can resolve to
 * a real page, so the banner and the receipts now agree.
 *
 * REPLACING THESE FIXTURES WITH HONEST EMPTY STATES IS STILL THE RIGHT END STATE, and
 * `lib/vc.ts` documents why it refused fixtures outright for the personalisation client.
 * That change rewrites the `Result<T>` / `source` contract across every page that renders
 * a `SourceChip`, so it wants a moment when those files are not being edited concurrently.
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
  EventTrace,
  Memo,
  ProofProtocol,
  QueryResult,
  ScoreHistory,
  Thesis,
} from "./types";

/**
 * Where the API lives.
 *
 * In production the backend is deployed alongside this app on the same Vercel
 * project, so the default is the SAME-ORIGIN path `/api`. That matters for two
 * reasons beyond tidiness: a same-origin call has no CORS to configure, and a
 * relative path cannot end up pointing at the visitor's own machine — which is
 * exactly what `http://localhost:8000` does once the site is deployed, and why
 * every call failed there.
 *
 * Locally the backend runs on its own port, so dev falls back to :8000.
 * NEXT_PUBLIC_API_BASE overrides both if the backend is ever hosted elsewhere.
 */
export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ??
  (process.env.NODE_ENV === "production" ? "/api" : "http://localhost:8000");

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
   * Measured against the live backend, `/companies` answers in ~1.5s. 8s clears that
   * by a wide margin and is still short enough that a genuinely dead backend falls
   * back before anyone in the room notices.
   *
   * This was briefly raised to 30s when the corpus grew to 126 companies and the list
   * took 48-70s. That was the wrong fix and it is recorded here so nobody repeats it:
   * the endpoint was issuing one database round trip PER COMPANY, and the timeout was
   * hiding an N+1, not absorbing honest latency. `store.prefetch` now loads the log in
   * one query and the list is back to ~1.5s at 126 companies. If this endpoint gets
   * slow again, find the query — do not raise this number.
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

/**
 * A non-2xx answer, carrying the status so callers can tell WHY the call failed.
 *
 * This exists because "the server refused" and "there is nothing there" are different
 * claims, and collapsing them is how a 503 came to be rendered as "no dissent exists for
 * this company" — a statement about the COMPANY that the server never made. A status code
 * is the difference between reporting our own outage and inventing a finding.
 */
export class HttpError extends Error {
  readonly status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = "HttpError";
    this.status = status;
  }
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
    if (!res.ok) {
      // FastAPI puts the human-readable cause in `detail`; showing it beats "503".
      const body: unknown = await res.json().catch(() => null);
      const detail = ad.isObj(body) ? body.detail : undefined;
      throw new HttpError(
        res.status,
        typeof detail === "string" && detail.trim()
          ? detail
          : `${res.status} ${res.statusText}`,
      );
    }
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

/**
 * The thesis is served NESTED and edited FLAT. These two functions are the only place
 * that knows both shapes.
 *
 * The old code did neither translation: it validated on `Array.isArray(v.sectors) &&
 * typeof v.risk_appetite === "number"`, which the real document never satisfies
 * (`sectors` is a list of objects, `risk_appetite` is `{value}`), so /thesis silently
 * failed validation on EVERY load and the page rendered the fixture while the backend
 * was up. The panel then wrote its flat shape straight back, producing `stages`,
 * `geos` and `check_size_min` keys that nothing on the server has ever read.
 */
function thesisFromApi(v: unknown): Thesis | null {
  if (!isObj(v)) return null;

  const sectors = Array.isArray(v.sectors)
    ? v.sectors
        .filter((s) => !isObj(s) || s.include !== false)
        .map((s) => (isObj(s) ? String(s.label ?? s.id ?? "") : String(s)))
        .filter(Boolean)
    : [];

  const stage = isObj(v.stage) ? v.stage : {};
  const geo = isObj(v.geo) ? v.geo : {};
  const check = isObj(v.check_size) ? v.check_size : {};
  const risk = isObj(v.risk_appetite) ? v.risk_appetite : { value: v.risk_appetite };
  const asNum = (x: unknown, dflt: number) => (typeof x === "number" ? x : dflt);
  const asList = (x: unknown) => (Array.isArray(x) ? x.map(String).filter(Boolean) : []);

  return {
    sectors,
    stages: asList(stage.include),
    geos: asList(geo.include),
    check_size_min: asNum(check.min, 250_000),
    check_size_max: asNum(check.max, 2_000_000),
    risk_appetite: Math.round(asNum(risk.value, 0.5) * 100),
    // `notes` is a top-level key of its own. Deriving it from stage.note/geo.note
    // would have made it read-only in practice: the save had nowhere to put an edit
    // back, so every word typed here would vanish on the next load.
    notes: typeof v.notes === "string" ? v.notes : "",
    raw: v,
  };
}

/**
 * Flat UI shape back onto the document `core/thesis.py` actually reads.
 *
 * Spreads `raw` first so every field this UI does not model — clearing_score,
 * ranking_policy, hard_filters — survives the round trip. The server merges on top of
 * the file's current contents as well; both halves matter, because a client that
 * posts only what it understands destroys the rest of the config by omission.
 */
function thesisToApi(t: Thesis): Record<string, unknown> {
  const priorSectors = Array.isArray(t.raw?.sectors) ? t.raw.sectors : [];
  const priorFor = (label: string) =>
    priorSectors.find(
      (s) =>
        isObj(s) &&
        [s.label, s.id].some(
          (x) => typeof x === "string" && x.toLowerCase() === label.toLowerCase(),
        ),
    );

  const slug = (s: string) => s.trim().toLowerCase().replace(/[^a-z0-9]+/g, "-");
  const weight = t.sectors.length ? Number((1 / t.sectors.length).toFixed(3)) : 0;

  const included = t.sectors.map((label) => {
    const prev = priorFor(label);
    return {
      ...(isObj(prev) ? prev : {}),
      id: isObj(prev) ? prev.id : slug(label),
      label,
      weight: isObj(prev) && typeof prev.weight === "number" ? prev.weight : weight,
      include: true,
    };
  });

  /**
   * Sectors explicitly marked `include: false` are carried through untouched.
   *
   * The panel only renders included sectors, so writing back just what it holds
   * silently DELETED entries like `{id:"fintech", include:false}` — an author's
   * deliberate "we have considered fintech and we do not do it" annotation, erased by
   * a UI that never showed it. Functionally `included_sectors()` ignores them either
   * way; that is not a licence to destroy the record.
   */
  const excluded = priorSectors.filter(
    (s) =>
      isObj(s) &&
      s.include === false &&
      !included.some((k) => String(k.label).toLowerCase() === String(s.label).toLowerCase()),
  );

  return {
    ...(t.raw ?? {}),
    sectors: [...included, ...excluded],
    stage: { ...(isObj(t.raw?.stage) ? t.raw.stage : {}), include: t.stages },
    geo: { ...(isObj(t.raw?.geo) ? t.raw.geo : {}), include: t.geos },
    check_size: {
      ...(isObj(t.raw?.check_size) ? t.raw.check_size : { currency: "USD" }),
      min: t.check_size_min,
      max: t.check_size_max,
      target: Math.round((t.check_size_min + t.check_size_max) / 2),
    },
    risk_appetite: {
      ...(isObj(t.raw?.risk_appetite) ? t.raw.risk_appetite : {}),
      value: Number((t.risk_appetite / 100).toFixed(2)),
    },
    notes: t.notes,
    updated_at: new Date().toISOString(),
  };
}

export function getThesis(): Promise<Result<Thesis>> {
  return withFallback("/thesis", fx.THESIS, thesisFromApi);
}

/** PUT, because `api/main.py` exposes `@app.put("/thesis")`. The panel POSTed, got a
 *  405 on every save, and reported "kept locally (API down)" while the API was fine. */
export async function putThesis(t: Thesis): Promise<Result<Thesis>> {
  try {
    const res = await fetch(`${API_BASE}/thesis`, {
      method: "PUT",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(thesisToApi(t)),
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return { data: thesisFromApi(await res.json()) ?? t, source: "live" };
  } catch (e) {
    return {
      data: t,
      source: "fixture",
      note: `PUT /thesis: ${reason(e)} — kept locally`,
      failed: true,
    };
  }
}

/**
 * Promote a parsed query filter into the STANDING thesis.
 *
 * Deliberately a separate, explicit action. Typing in the query box narrows the view;
 * it must never rewrite the fund's thesis as a side effect, because the two do
 * genuinely different things — the thesis EXCLUDES companies from the pipeline
 * (`core/thesis.in_scope`), while a query only dims rows that are already screened in.
 *
 * Returns the fields that would change, so the caller can show them before writing.
 */
export function thesisDiffFromFilter(
  t: Thesis,
  filter: Record<string, unknown> | null | undefined,
): { next: Thesis; changes: string[] } {
  const list = (v: unknown) => (Array.isArray(v) ? v.map(String).filter(Boolean) : []);
  const next = { ...t };
  const changes: string[] = [];

  const sectors = list(filter?.sectors);
  if (sectors.length) {
    next.sectors = sectors;
    changes.push(`sectors → ${sectors.join(", ")}`);
  }
  const stages = list(filter?.stages);
  if (stages.length) {
    next.stages = stages;
    changes.push(`stage.include → ${stages.join(", ")}`);
  }
  const geos = list(filter?.geos);
  if (geos.length) {
    next.geos = geos;
    changes.push(`geo.include → ${geos.join(", ")}`);
  }
  const lo = filter?.check_size_min_usd;
  const hi = filter?.check_size_max_usd;
  if (typeof lo === "number") {
    next.check_size_min = lo;
    changes.push(`check_size.min → ${lo.toLocaleString()}`);
  }
  if (typeof hi === "number") {
    next.check_size_max = hi;
    changes.push(`check_size.max → ${hi.toLocaleString()}`);
  }
  return { next, changes };
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
    // The LLM budget. This is NOT a plain read: the detail route screens the company
    // with compute=true, which is three LLM-judged axes. Warm it answers in ~2s, but
    // COLD it measured 8.7-9.6s across several companies — over the 8s read budget, so
    // the first open of any company fell back to fixtures and the page announced
    // "Live detail unavailable", "Sparse record", and "No memo has been written",
    // on a company whose 60 events were sitting right there. The endpoint was never
    // broken; the budget was wrong for what it does.
    const live = await get<unknown>(`/companies/${encodeURIComponent(id)}`, TIMEOUT.llm);
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
    // The LLM budget, not the read budget — the same fix `getDissent` already carries.
    // /memo GENERATES the memo: it screens, validates claims and writes five narrated
    // sections, measured at 7.9s warm and 9.0s cold. At the 8s read budget it aborted
    // on a cold cache and the page rendered "No memo has been written for this company"
    // — a claim about the COMPANY, when the truth was that our own request timed out
    // while the memo was on its way. Identical failure to the dissent one below, in the
    // second of the two places it could happen.
    const live = await get<unknown>(path, TIMEOUT.llm);
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

/**
 * Why a dissent could not be shown, when it could not be shown.
 *
 * `absent` is a claim about the COMPANY (the server answered, and there is no bear case
 * on file). `unavailable` is a claim about US (the council needs a model and did not run,
 * or the call failed in transit) — it says nothing about the company at all.
 *
 * These were one `null` return, and the UI reported both as "no dissent exists for this
 * company". A 503 from a backend with no model credentials therefore rendered as a
 * finding about the founder. That is the exact inversion this codebase exists to refuse.
 */
export type DissentMiss =
  | { kind: "absent" }
  | { kind: "unavailable"; reason: string; status?: number; needsModel: boolean };

export async function getDissent(
  id: string,
): Promise<Result<Dissent> | DissentMiss> {
  const fixture = fx.dissent(id);
  const path = `/companies/${encodeURIComponent(id)}/dissent`;
  try {
    // The LLM budget, not the read budget. The council runs three deep roles plus
    // a chair and measured ~11s; at the 8s read timeout this aborted every time
    // and the page reported "no dissent exists" for a dissent that was on its way.
    const live = await get<unknown>(path, TIMEOUT.llm);
    // The route returns the anti-memo nested under `anti_memo` alongside the decision
    // and the lock state; the adapter accepts either nesting. It also normalises
    // `axis_spreads` from the wire's 0..1 into score units, which is what stopped three
    // genuine half-scale disagreements from rendering as three empty bars.
    const adapted = ad.toDissent(live);
    if (adapted) return { data: adapted, source: "live" };
    if (fixture) {
      return { data: fixture, source: "fixture", note: `${path} returned an unreadable dissent` };
    }
    // The server answered and we could not read a dissent out of it. That is an
    // unreadable ANSWER, not a company with no bear case — so it is still `unavailable`.
    return {
      kind: "unavailable",
      reason: "the dissent came back in a shape this page cannot read",
      needsModel: false,
    };
  } catch (e) {
    if (fixture) {
      return { data: fixture, source: "fixture", note: `${path}: ${reason(e)}`, failed: true };
    }
    // 404 is the only answer that means "this company genuinely has no dissent". Every
    // other failure — 503 with no model configured, a timeout, a dead backend — is our
    // outage, and must not be reported as a fact about the company.
    const status = e instanceof HttpError ? e.status : undefined;
    if (status === 404) return { kind: "absent" };
    return {
      kind: "unavailable",
      reason: reason(e),
      status,
      // 503 is what the backend returns when the council has no model credentials.
      needsModel: status === 503,
    };
  }
}

/**
 * The bottom of the trace: one event, resolved to its quoted span, whether that span was
 * generated by this system, and the real receipts underneath it if so.
 *
 * Returns null rather than a fixture on failure. There is no honest fallback for a
 * receipt — showing a plausible span we did not fetch would invert the one claim this
 * drawer exists to make, so the caller renders "the trace could not be resolved" instead.
 */
export async function getTrace(
  companyId: string,
  eventId: string,
): Promise<EventTrace | null> {
  try {
    const live = await get<unknown>(
      `/companies/${encodeURIComponent(companyId)}/trace/${encodeURIComponent(eventId)}`,
      TIMEOUT.read,
    );
    return ad.toEventTrace(live);
  } catch {
    return null;
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
 * Deliberately shallow — it recognises structural clauses and reports back the
 * predicates it actually applied, so nothing on screen claims more than it did.
 *
 * A FALLBACK MAY DEGRADE; IT MAY NEVER FABRICATE. It filters only the live company
 * list it was handed, and it no longer reads claim-level evidence at all. It used to
 * dereference hand-authored fixture detail records: where a live id happened to
 * collide with a fixture id, "has a contradicted claim" was answered from an INVENTED
 * claim record and presented as a finding about a real founder — an outage rendered
 * as evidence. Claim predicates are now named and declined rather than guessed.
 */
export function interpretQuery(q: string, companies: CompanySummary[]): QueryResult {
  const s = q.toLowerCase().trim();
  const preds: string[] = [];
  const warnings: string[] = [];
  let out = companies;

  /**
   * What is left of the sentence after every recognised clause has been consumed.
   * Whatever survives is read as the SECTOR PHRASE — which is how the vocabulary
   * became open. The previous version knew three sectors (infra, data, ai) and an
   * investor asking for logistics or defense got the entire list back under the
   * heading "no predicate recognised — showing all".
   */
  let residual = ` ${s.replace(/[^a-z0-9]+/g, " ").trim()} `;
  const eat = (re: RegExp) => {
    const had = re.test(residual);
    residual = residual.replace(new RegExp(re.source, "g"), " ");
    return had;
  };

  /**
   * A clause about claim-level evidence. The claim record lives on the company DETAIL
   * endpoint, which is exactly what is unreachable when this interpreter is running,
   * so the clause is declared un-runnable and the rows are left un-narrowed by it.
   * Under-filtering and saying so is the only honest option; the alternative is
   * answering an evidence question from a file.
   */
  const declineClaim = (label: string) => {
    warnings.push(
      `"${label}" was NOT applied — claim-level evidence lives on the company detail endpoint, which is unreachable right now. The rows below were not narrowed by it. Retry for a live answer.`,
    );
  };

  // --- structural clauses, consumed so they cannot also read as a sector ----
  const STAGES: [string, RegExp][] = [
    ["pre-seed", /pre\s?seed/],
    ["series-a", /series a\b/],
    ["series-b", /series b\b/],
    ["growth", /growth stage/],
    ["seed", /\bseed\b/],
  ];
  const stages = STAGES.filter(([, re]) => eat(re)).map(([name]) => name);
  eat(/\bstage\b/);
  if (stages.length) {
    const known = companies.filter((c) => c.stage);
    out = out.filter((c) => !c.stage || stages.some((st) => c.stage.toLowerCase().includes(st)));
    preds.push(`stage in ${stages.join(", ")}`);
    if (known.length < companies.length) {
      warnings.push(
        `stage was not applied to ${companies.length - known.length} of ${companies.length} records — they carry no stage, and absent metadata is not treated as disqualifying`,
      );
    }
  }

  const GEOS = [
    "north america", "south america", "latin america", "latam", "europe", "emea", "apac",
    "southeast asia", "asia", "middle east", "africa", "oceania", "united states", "usa",
    "canada", "mexico", "brazil", "united kingdom", "uk", "ireland", "france", "germany",
    "spain", "italy", "netherlands", "sweden", "norway", "denmark", "poland", "estonia",
    "switzerland", "israel", "india", "china", "japan", "korea", "singapore", "indonesia",
    "vietnam", "australia", "new zealand", "nigeria", "kenya", "egypt", "south africa",
  ];
  const geos = GEOS.filter((g) => eat(new RegExp(`\\b${g}\\b`)));
  if (geos.length) {
    const known = companies.filter((c) => c.geo);
    out = out.filter((c) => !c.geo || geos.some((g) => c.geo.toLowerCase().includes(g)));
    preds.push(`geo in ${geos.join(", ")}`);
    if (known.length < companies.length) {
      warnings.push(
        `geography was not applied to ${companies.length - known.length} of ${companies.length} records — they carry no geography`,
      );
    }
  }

  // Cheque size is a property of YOUR FUND, not of a company, and no company record
  // carries one. Naming it and declining to filter on it is the honest move.
  const money = s.match(/\$\s*\d+(?:\.\d+)?\s*[kmb]?/g);
  if (money) {
    eat(/\b(cheque|check|ticket|size|raising|between|under|below|over|above|up to|at least|at most|less than|more than)\b/);
    eat(/\b\d+(\s+\d+)*\s*[kmb]?\b/);
    preds.push(`cheque ${money.join("–")}`);
    warnings.push(
      `cheque size did not narrow anything — no round size is recorded on any company. It is a property of your fund; set it on the standing thesis, where it governs the recommendation`,
    );
  }

  // `?? 0` on an ABSENT axis would silently exclude the company from both the rising
  // and falling filters, which is right: an uncomputed trend cannot be asserted either way.
  if (eat(/rising|\brise\b|positive trend|momentum|improving/)) {
    out = out.filter((c) => (c.axes.founder?.trend ?? 0) > 0);
    preds.push("founder.trend > 0");
  }
  if (eat(/falling|declin\w*|negative trend|deterior\w*/)) {
    out = out.filter((c) => (c.axes.founder?.trend ?? 0) < 0);
    preds.push("founder.trend < 0");
  }
  eat(/\btrend\b/);
  if (eat(/unverified|unverifiable|not attempted|no verification/)) {
    declineClaim("has claim in {UNVERIFIABLE, NOT_ATTEMPTED}");
  }
  if (eat(/contradict\w*/)) {
    declineClaim("has claim = CONTRADICTED");
  }
  eat(/\bclaims?\b/);
  if (eat(/revenue|\barr\b|traction/)) {
    declineClaim('claim_text ~ "revenue|arr|pilot|customer|partner|traction"');
  }
  if (eat(/cold start|no public|no footprint|invisible/)) {
    out = out.filter(
      (c) => c.gate === "proof_protocol" || /cold|invisible/i.test(c.archetype),
    );
    preds.push("gate = PROOF_PROTOCOL or archetype ~ cold/invisible");
  }
  if (eat(/injection|adversarial|integrity|flagged|flags?/)) {
    out = out.filter((c) => c.flag_count > 0);
    preds.push("flag_count > 0");
  }
  if (eat(/proof protocol/)) {
    out = out.filter((c) => c.gate === "proof_protocol");
    preds.push("gate = PROOF_PROTOCOL");
  }
  if (eat(/proceed/)) {
    out = out.filter((c) => c.gate === "proceed");
    preds.push("gate = PROCEED");
  }
  if (eat(/no call|no_call|\bpass\b|reject\w*/)) {
    out = out.filter((c) => c.gate === "no_call");
    preds.push("gate = NO_CALL");
  }
  eat(/\brouted to\b|\bgate\b/);

  // --- whatever is left is the sector, whatever industry it names ----------
  const STOP = new Set(
    "a an and or the of for in into on at to with without that which who show me find list all any some companies company startups startup founders founder teams team businesses round rounds based headquartered hq located doing building build built working work works space sector sectors industry industries market markets please still only just more most less than up new early late is are was were be been has have had do does did not no yes but so if then when where what how".split(
      " ",
    ),
  );
  const leftover = residual
    .split(/\s+/)
    .filter((w) => w.length > 1 && !STOP.has(w));

  if (leftover.length) {
    const phrase = leftover.join(" ");
    const taxonomy = (c: CompanySummary) =>
      ` ${`${c.sector} ${c.archetype}`.toLowerCase().replace(/[^a-z0-9]+/g, " ")} `;
    const prose = (c: CompanySummary) =>
      ` ${`${c.sector} ${c.archetype} ${c.name} ${c.one_liner}`.toLowerCase().replace(/[^a-z0-9]+/g, " ")} `;

    // One token is enough against the taxonomy (so "data tooling" finds data-infra);
    // the whole phrase is required against free prose (so "consumer social" does not
    // match a GPU company whose one-liner happens to contain the word "consumer").
    const hit = (c: CompanySummary) =>
      prose(c).includes(` ${phrase} `) || leftover.some((t) => taxonomy(c).includes(` ${t} `));

    const before = out;
    out = out.filter(hit);
    preds.push(`sector ~ "${phrase}"`);
    if (!companies.some(hit)) {
      warnings.push(
        `no screened record mentions "${phrase}" — this system has never sourced that industry, so the zero result is about our coverage, not about the query`,
      );
    } else if (before.length && !out.length) {
      warnings.push(`"${phrase}" exists in the list, but not together with the other clauses`);
    }
  }

  const parsed = preds.length
    ? preds.join(" · ")
    : "nothing in this query could be turned into a filter — every record is shown, unfiltered";
  return { q, parsed, warnings, company_ids: out.map((c) => c.id), count: out.length };
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
  const local = interpretQuery(q, companies);
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

    // `warnings` is carried here rather than in ad.toQueryResult so the honest-failure
    // channel survives even if the adapter is unaware of it.
    if (isObj(live) && Array.isArray(live.warnings)) {
      adapted.warnings = live.warnings.map(String).filter(Boolean);
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

// ===========================================================================
// ===========================================================================
// OUTBOUND — eligibility, drafting, the review queue.  [outreach workstream]
//
// Everything below this rule is ADDITIVE. Nothing above it was changed.
//
// THREE DELIBERATE DEPARTURES from the module above, each with its reason:
//
// 1. NO FIXTURE FALLBACK. The rest of this file serves a hand-authored record when
//    the backend is down, because a blank pipeline mid-demo is worse than a stale
//    one. That trade inverts here. A fixture draft is a fabricated claim about a
//    real person, and a fixture eligibility verdict would show a green light that
//    no gate ever returned. These functions therefore return a discriminated
//    result and the UI renders the failure.
//
// 2. `credentials: "include"`. The eligibility gate screens the signed-in VC's
//    stated red lines (api/routers/outbound.py::_red_lines). Without the cookie the
//    gate runs with an empty red-line list, which SILENTLY RELAXES it — the one
//    failure mode that router explicitly refuses. So the session must travel.
//
// 3. 422 is an OUTCOME, not an error. `POST /outbound/draft/{id}` answers 422 when
//    the generated text could not be grounded in a stored span; the draft was
//    recorded as `rejected_unverifiable` and discarded. That is the anti-
//    hallucination path firing correctly and it is surfaced as its own state.
//
// The existing `post()` helper is not reused for exactly (2) and (3): it sends no
// credentials and it flattens every non-2xx into one error shape.
//
// NOTE ON `recipient_email`: the wire carries it and it is always null (the
// reviewer supplies an address outside this system). It is deliberately ABSENT
// from `OutboundDraft` below, so no component can render or collect it. Routing
// outreach via LinkedIn exists to keep a founder's personal address out of the
// VC's hands; a convenience field here would undo that.
// ===========================================================================
// ===========================================================================

/** One re-run of a decision the system already made on its own terms. */
export interface EligibilityCheck {
  name: string;
  passed: boolean;
  detail: string;
}

export interface EligibilityVerdict {
  /** Slug (`vb-tensorpage`) when the route could resolve one. */
  id?: string;
  company_id: string;
  name: string;
  eligible: boolean;
  checks: EligibilityCheck[];
  /** Names of the checks that said no. Empty on an eligible company. */
  blocked_by: string[];
  why_not: string | null;
}

export interface EligibleResponse {
  as_of: string;
  /** True when a session was present, so the profile's red lines were screened. */
  profile_active: boolean;
  eligible: EligibilityVerdict[];
  /** The more useful half. A funnel that reports only survivors cannot be audited. */
  ineligible: EligibilityVerdict[];
  rule: string;
}

/**
 * A resolved receipt. The URL is attached by the backend from a stored event — the
 * model that wrote the prose was never shown one, so a fabricated link has no path
 * into the output. The shape is identical on outbound drafts and on standout
 * summaries because both resolve the same server-side `Ref`; one type, deliberately.
 */
export interface Citation {
  n: number;
  ref_id: string;
  event_id: string;
  source: string;
  source_url: string;
  kind: string;
  observed_at: string;
  evidence_span: string;
}

export type DraftStatus = "queued" | "approved" | "rejected" | "rejected_unverifiable";

export interface OutboundDraft {
  draft_id: string;
  company_id: string;
  company_name: string | null;
  recipient_name: string | null;
  status: DraftStatus;
  subject: string | null;
  body: string | null;
  citations: Citation[] | null;
  eligibility: EligibilityVerdict | null;
  rejection_reason: string | null;
  as_of: string;
  created_at: string;
  decided_at: string | null;
  decided_by: string | null;
}

export interface QueueResponse {
  status: DraftStatus;
  count: number;
  items: OutboundDraft[];
  note: string;
}

/** Resolves, always. `unverifiable` marks the 422 anti-hallucination rejection. */
export type OutRes<T> =
  | { ok: true; data: T }
  | { ok: false; error: string; status?: number; unverifiable?: boolean };

async function outbound<T>(
  path: string,
  init: RequestInit & { timeoutMs?: number } = {},
): Promise<OutRes<T>> {
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
      credentials: "include",
      headers: { accept: "application/json", ...(rest.headers ?? {}) },
    });
    const body: unknown = await res.json().catch(() => null);
    if (!res.ok) {
      const detail = isObj(body) ? body.detail : undefined;
      return {
        ok: false,
        status: res.status,
        unverifiable: res.status === 422,
        error: typeof detail === "string" ? detail : `${res.status} ${res.statusText}`,
      };
    }
    return { ok: true, data: body as T };
  } catch (e) {
    return {
      ok: false,
      error: timedOut ? `no response in ${timeoutMs / 1000}s` : reason(e),
    };
  } finally {
    clearTimeout(timer);
  }
}

const asOfQuery = (asOf?: string | null) =>
  asOf ? `as_of=${encodeURIComponent(asOf)}` : "";

/**
 * Who genuinely passes, and for everyone else exactly which check said no.
 *
 * Re-runs the gate, the validator, the integrity flags, the red lines and the memo's
 * own cheque calculation for every company, so it is slow by construction. The read
 * budget is widened accordingly rather than aborting work that was going to answer.
 */
export function getEligible(opts: { asOf?: string | null; companyId?: string } = {}) {
  const qs = [
    asOfQuery(opts.asOf),
    opts.companyId ? `company_id=${encodeURIComponent(opts.companyId)}` : "",
  ]
    .filter(Boolean)
    .join("&");
  return outbound<EligibleResponse>(`/outbound/eligible${qs ? `?${qs}` : ""}`, {
    timeoutMs: TIMEOUT.llm,
  });
}

/**
 * Generate, verify, queue. A `{ unverifiable: true }` failure is the system refusing
 * to hand a human text it could not ground — show it, do not retry it silently.
 */
export function postDraft(companyId: string, asOf?: string | null) {
  const qs = asOfQuery(asOf);
  return outbound<OutboundDraft>(
    `/outbound/draft/${encodeURIComponent(companyId)}${qs ? `?${qs}` : ""}`,
    { method: "POST", timeoutMs: TIMEOUT.llm },
  );
}

export function getOutboundQueue(status: DraftStatus = "queued") {
  return outbound<QueueResponse>(`/outbound/queue?status=${encodeURIComponent(status)}`);
}

/**
 * Record a human's disposition. `by` is required by the router and unvalidated on
 * purpose: an anonymous approval of a cold email about a named person is worse than
 * no approval. APPROVE DOES NOT SEND — there is no send endpoint anywhere.
 */
export function decideDraft(
  draftId: string,
  decision: "approve" | "reject",
  by: string,
  note?: string,
) {
  return outbound<OutboundDraft>(
    `/outbound/queue/${encodeURIComponent(draftId)}/${decision}`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ by, note: note?.trim() || null }),
    },
  );
}

// ===========================================================================
// RANKED-LIST EXTRAS AND THE STANDOUT SUMMARY.  [outreach workstream]
//
// `GET /companies` returns more than `CompanySummary` declares. Those extra fields
// are typed here rather than widened into `lib/types.ts`, which several workstreams
// share — TypeScript ignores unknown keys, so the wire has always carried them.
//
// THE STANDOUT SUMMARY IS THE BACKEND'S (api/standout.py). This client does not
// write one and must never call a model to. The distinctiveness is computed in
// Python over the whole corpus — a rare green-flag rule, a trait far off the corpus
// median, an evidence footprint at the edge of the distribution — and the model is
// only ever allowed to turn those computed findings into a sentence, against spans
// it must quote. `summary_source` says which happened, and it is rendered, because
// a card that hides whether a model wrote its prose is the ungrounded text this
// whole endpoint exists to replace.
//
// A row that has not been computed yet carries `status: "not_generated"` and a NULL
// summary. That is rendered as "not yet generated", never as an empty line — an
// empty line reads as a finding of nothing, which is a different claim.
// ===========================================================================

/** One computed difference between this company and the rest of the corpus. */
export interface Distinctive {
  kind: string;
  key: string;
  detail: string;
  /** How this company sits against the corpus — the comparison, in words. */
  comparison: string;
  direction: string;
  /** 0..1. How far from the field this is. */
  strength: number;
  citable: boolean;
  evidence_event_ids: string[];
}

export interface Standout {
  company_id: string;
  /** Null when nothing has been computed yet, or when nothing is distinctive. */
  summary: string | null;
  /** "model" — a model wrote the sentence from our findings. "computed" — Python did. */
  summary_source?: "model" | "computed";
  status?: "not_generated";
  citations?: Citation[];
  distinctives?: Distinctive[];
  distinctive_count?: number;
  /** Sentences the verifier deleted. Recorded, not hidden — a drop is the mechanism working. */
  dropped_sentences?: string[];
  reason?: string | null;
  generated_at?: string;
  cached?: boolean;
  hint?: string;
}

/** Extra fields `GET /companies` serves beyond the `CompanySummary` contract. */
export interface RankExtras {
  /** The backend's own core position, 1-based. */
  rank?: number;
  /** Integrity flag names, e.g. ["transliterated_name", "non_english_source"]. */
  flags?: string[];
  unverified_claims?: number;
  gate_rationale?: string;
  /** The UUID. The list's `id` is the slug. */
  company_id?: string;
  standout?: Standout;
}

export type RankedCompany = CompanySummary & RankExtras;

/**
 * There is NO logo field on any company route, and this is the only place one could
 * enter. It returns null for every company today, on purpose, so the absence is one
 * auditable line rather than thirteen silent ones.
 *
 * Do not make it guess a domain from the company name and hit a favicon service. It
 * would succeed often enough to look like it works and fail silently by attaching a
 * stranger's brand to a company in an investor's shortlist — a fabrication wearing
 * someone else's trademark, and exactly the class of error the rest of this system
 * refuses. `CompanyMark` renders a typographic monogram when this returns null.
 */
export function logoUrl(_c: RankedCompany): string | null {
  void _c;
  return null;
}

/**
 * Compute (or re-read) the standout summary for one company.
 *
 * Not called for the whole list on page load: the comparison plus its one model call
 * runs to roughly a second per company, and thirteen of them would hold the ranked
 * list hostage. The list renders what is already cached and offers this per row.
 */
export function getStandout(companyId: string, refresh = false) {
  return outbound<Standout>(
    `/companies/${encodeURIComponent(companyId)}/standout${refresh ? "?refresh=true" : ""}`,
    { timeoutMs: TIMEOUT.llm },
  );
}
