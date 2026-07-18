/**
 * Typed API client for the VC Brain backend.
 *
 * Every call is fallback-first: if the backend is down, slow, or returns a shape we
 * cannot use, we serve `lib/fixtures.ts` instead and mark the response `live: false`.
 * A blank screen during a 2.5-minute live demo is fatal — this module exists so that
 * cannot happen. No call in here can reject.
 */

import * as fx from "./fixtures";
import type {
  Backtest,
  CompanyDetail,
  CompanySummary,
  Dissent,
  Memo,
  QueryResult,
  ScoreHistory,
  Thesis,
} from "./types";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://localhost:8000";

const TIMEOUT_MS = 2500;

/** Where the data on screen came from. Rendered in the header — we never fake liveness. */
export type Source = "live" | "fixture";

export interface Result<T> {
  data: T;
  source: Source;
  /** Set when a live call was attempted and failed. Shown in the UI, never swallowed. */
  note?: string;
}

async function get<T>(path: string): Promise<T> {
  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(`${API_BASE}${path}`, {
      signal: ctrl.signal,
      cache: "no-store",
      headers: { accept: "application/json" },
    });
    if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
    return (await res.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/**
 * Try the backend; on any failure fall back to the fixture.
 * `valid` rejects a live response whose shape we cannot render, so a half-built
 * endpoint degrades to the fixture instead of rendering an empty page.
 */
async function withFallback<T>(
  path: string,
  fallback: T,
  valid: (v: unknown) => v is T,
): Promise<Result<T>> {
  try {
    const live = await get<unknown>(path);
    if (!valid(live)) {
      return {
        data: fallback,
        source: "fixture",
        note: `${path} returned an unexpected shape — showing fixture`,
      };
    }
    return { data: live, source: "live" };
  } catch (e) {
    const reason = e instanceof Error ? e.message : String(e);
    return { data: fallback, source: "fixture", note: `${path}: ${reason}` };
  }
}

const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

// ---------------------------------------------------------------------------
// Routes
// ---------------------------------------------------------------------------

export function getThesis(): Promise<Result<Thesis>> {
  return withFallback("/thesis", fx.THESIS, (v): v is Thesis =>
    isObj(v) && Array.isArray(v.sectors) && typeof v.risk_appetite === "number",
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
    const reason = e instanceof Error ? e.message : String(e);
    return { data: t, source: "fixture", note: `POST /thesis: ${reason} — kept locally` };
  }
}

export function getCompanies(): Promise<Result<CompanySummary[]>> {
  return withFallback(
    "/companies",
    fx.COMPANIES,
    (v): v is CompanySummary[] =>
      Array.isArray(v) && v.length > 0 && isObj(v[0]) && isObj(v[0].axes),
  );
}

export function getCompany(id: string): Promise<Result<CompanyDetail>> {
  const fallback = fx.companyDetail(id) ?? fx.companyDetail(fx.COMPANIES[0].id)!;
  return withFallback(
    `/companies/${id}`,
    fallback,
    (v): v is CompanyDetail =>
      isObj(v) && isObj(v.axes) && Array.isArray(v.events) && Array.isArray(v.claims),
  );
}

/**
 * NOTE: `api/main.py` does not expose this route yet (see the mismatch list in the
 * handoff). The history also ships inside GET /companies/{id}, so this falls back
 * to the detail payload before falling back to fixtures.
 */
export async function getScoreHistory(
  id: string,
  fromDetail?: ScoreHistory,
): Promise<Result<ScoreHistory>> {
  const fallback =
    fromDetail ??
    fx.companyDetail(id)?.score_history ??
    fx.companyDetail(fx.COMPANIES[0].id)!.score_history;
  return withFallback(
    `/companies/${id}/score-history`,
    fallback,
    (v): v is ScoreHistory =>
      isObj(v) && Array.isArray(v.founder) && Array.isArray(v.market),
  );
}

export function getMemo(id: string, dissentViewed: boolean): Promise<Result<Memo>> {
  const fallback = fx.memo(id, dissentViewed) ?? fx.memo(fx.COMPANIES[0].id, dissentViewed)!;
  return withFallback(
    `/companies/${id}/memo?dissent_viewed=${dissentViewed}`,
    fallback,
    (v): v is Memo => isObj(v) && Array.isArray(v.sections),
  );
}

export function getDissent(id: string): Promise<Result<Dissent>> {
  const fallback = fx.dissent(id) ?? fx.dissent(fx.COMPANIES[0].id)!;
  return withFallback(
    `/companies/${id}/dissent`,
    fallback,
    (v): v is Dissent => isObj(v) && typeof v.bear_case === "string",
  );
}

export function getBacktest(): Promise<Result<Backtest>> {
  return withFallback(
    "/backtest",
    fx.BACKTEST,
    (v): v is Backtest => isObj(v) && Array.isArray(v.trajectories),
  );
}

// ---------------------------------------------------------------------------
// NL compound query
// ---------------------------------------------------------------------------

/**
 * Local interpreter for the compound query, used when GET /query is unavailable.
 * Deliberately shallow — it recognises the demo's vocabulary and reports back the
 * predicates it actually applied, so nothing on screen claims more than it did.
 */
export function interpretQuery(
  q: string,
  companies: CompanySummary[],
  details: (id: string) => CompanyDetail | null,
): QueryResult {
  const s = q.toLowerCase().trim();
  const preds: string[] = [];
  let out = companies;

  const sector = (needle: string, label: string) => {
    if (s.includes(needle)) {
      out = out.filter((c) => c.sector.toLowerCase().includes(label));
      preds.push(`sector ~ "${label}"`);
    }
  };
  sector("infra", "infrastructure");
  sector("data", "data");
  if (/\bai\b|llm|model/.test(s)) {
    out = out.filter((c) => c.sector.toLowerCase().includes("ai"));
    preds.push('sector ~ "ai"');
  }

  if (/rising|rise|positive trend|momentum|improving/.test(s)) {
    out = out.filter((c) => c.axes.founder.trend > 0);
    preds.push("founder.trend > 0");
  }
  if (/falling|declin|negative trend|deterior/.test(s)) {
    out = out.filter((c) => c.axes.founder.trend < 0);
    preds.push("founder.trend < 0");
  }
  if (/unverified|unverifiable|not attempted|no verification/.test(s)) {
    out = out.filter((c) =>
      (details(c.id)?.claims ?? []).some(
        (cl) => cl.status === "unverifiable" || cl.status === "not_attempted",
      ),
    );
    preds.push("has claim in {UNVERIFIABLE, NOT_ATTEMPTED}");
  }
  if (/contradict/.test(s)) {
    out = out.filter((c) =>
      (details(c.id)?.claims ?? []).some((cl) => cl.status === "contradicted"),
    );
    preds.push("has claim = CONTRADICTED");
  }
  if (/revenue|arr|traction/.test(s)) {
    out = out.filter((c) =>
      (details(c.id)?.claims ?? []).some((cl) =>
        /revenue|arr|pilot|customer|partner|traction/i.test(cl.claim_text),
      ),
    );
    preds.push('claim_text ~ "revenue|arr|pilot|customer|partner|traction"');
  }
  if (/cold start|no public|no footprint|invisible/.test(s)) {
    out = out.filter((c) => c.gate === "proof_protocol" || /cold|invisible/i.test(c.archetype));
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

  return {
    q,
    parsed: preds.length ? preds.join("  AND  ") : "no predicate recognised — showing all",
    company_ids: out.map((c) => c.id),
  };
}

export async function runQuery(
  q: string,
  companies: CompanySummary[],
): Promise<Result<QueryResult>> {
  const local = interpretQuery(q, companies, (id) => fx.companyDetail(id));
  return withFallback(
    `/query?q=${encodeURIComponent(q)}`,
    local,
    (v): v is QueryResult => isObj(v) && Array.isArray(v.company_ids),
  );
}

export async function checkHealth(): Promise<boolean> {
  try {
    await get<unknown>("/health");
    return true;
  } catch {
    return false;
  }
}
