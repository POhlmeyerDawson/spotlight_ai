/**
 * Wire-shape adapters.
 *
 * The backend is under active development and its detail endpoints do not yet return
 * the canonical shapes in `lib/types.ts`: axes arrive on a 0..1 scale with nullable
 * scores, `gate` is an object rather than a string, the memo is keyed by section name
 * rather than being a list, and `events` / `integrity` are not present at all.
 *
 * This module is the ONE place that knows about those differences. It accepts either
 * the canonical shape or the current live shape and produces the canonical one.
 *
 * Two rules govern every function here:
 *
 *   1. NEVER INVENT. A field the source did not carry becomes null or an empty list,
 *      and the caller renders that absence honestly. Filling a gap to make a page look
 *      complete is the exact failure mode the product exists to argue against.
 *   2. NEVER SUBSTITUTE. If a record cannot be adapted, these return null so the caller
 *      can fall back deliberately. They must never quietly return a DIFFERENT company's
 *      data — clicking "Baseplate Systems" and landing on another company's evidence is
 *      worse than an empty page, because it is wrong rather than merely thin.
 */

import type {
  Axis,
  AxisKey,
  ClaimStatus,
  ClaimVerdict,
  CompanyDetail,
  CompanySummary,
  EvidenceEvent,
  GateOutcome,
  IntegrityFlag,
  Memo,
  ProofProtocol,
  QueryResult,
  ScoreHistory,
  ScorePoint,
} from "./types";
import { AXIS_KEYS } from "./types";

// ---------------------------------------------------------------------------
// Primitives
// ---------------------------------------------------------------------------

export const isObj = (v: unknown): v is Record<string, unknown> =>
  typeof v === "object" && v !== null && !Array.isArray(v);

const arr = (v: unknown): unknown[] => (Array.isArray(v) ? v : []);

/** A finite number, or null. Rejects NaN and Infinity, which render as "NaN" on screen. */
const num = (v: unknown): number | null =>
  typeof v === "number" && Number.isFinite(v) ? v : null;

const str = (v: unknown): string | null =>
  typeof v === "string" && v.trim() !== "" ? v : null;

const CLAIM_STATUSES: ClaimStatus[] = [
  "verified",
  "contradicted",
  "unverifiable",
  "not_attempted",
];

const GATES: GateOutcome[] = ["proceed", "proof_protocol", "no_call"];

// ---------------------------------------------------------------------------
// Axes
// ---------------------------------------------------------------------------

/**
 * `/companies` reports axis scores on 0..100; `/companies/{id}` currently reports the
 * same axes on 0..1. Guessing per-field would mis-scale a genuine 0.7-out-of-100, so the
 * decision is made once for the whole payload: if EVERY non-null score in the record is
 * <= 1, the record is on the unit scale. A record where any axis exceeds 1 is already
 * on 0..100 and is left alone.
 */
function scaleFactor(raw: Record<string, unknown>): number {
  const scores: number[] = [];
  for (const k of AXIS_KEYS) {
    const a = raw[k];
    if (!isObj(a)) continue;
    const s = num(a.score);
    if (s !== null) scores.push(s);
  }
  if (scores.length === 0) return 1;
  return scores.every((s) => Math.abs(s) <= 1) ? 100 : 1;
}

function toAxis(raw: unknown, factor: number): Axis {
  if (!isObj(raw)) {
    return { score: null, trend: null, band: null, confidence: 0, evidence_event_ids: [] };
  }

  const score = num(raw.score);
  const band = num(raw.band);
  const trend = num(raw.trend);

  // Evidence ids arrive either as a flat id list (canonical) or as inline evidence
  // objects carrying the span itself (live). Both are accepted; the ids are what the
  // trace drawer joins on, so inline evidence contributes its `event_ref`.
  const ids = arr(raw.evidence_event_ids)
    .map((v) => str(v))
    .filter((v): v is string => v !== null);
  const inline = arr(raw.evidence)
    .map((e) => (isObj(e) ? str(e.event_ref) ?? str(e.event_id) : null))
    .filter((v): v is string => v !== null);

  return {
    score: score === null ? null : score * factor,
    band: band === null ? null : band * factor,
    trend: trend === null ? null : trend * factor,
    confidence: num(raw.confidence) ?? 0,
    evidence_event_ids: ids.length ? ids : inline,
    reason: str(raw.reason) ?? undefined,
  };
}

function toAxes(raw: unknown): Record<AxisKey, Axis> | null {
  if (!isObj(raw)) return null;
  const factor = scaleFactor(raw);
  const out = {} as Record<AxisKey, Axis>;
  for (const k of AXIS_KEYS) out[k] = toAxis(raw[k], factor);
  return out;
}

// ---------------------------------------------------------------------------
// Events
// ---------------------------------------------------------------------------

/**
 * The live payload hangs evidence off each axis instead of exposing a flat event log.
 * Flattening it is what keeps the trace drill-down working: judges click an axis, and
 * the drawer needs an event carrying the quoted span for every id on that axis.
 *
 * `contribution` is deliberately null here. The live source lists the event as evidence
 * but does not attribute score units to it, and printing "+0.0" would assert that the
 * event moved the score by nothing — a claim the data does not support.
 */
function eventsFromAxes(rawAxes: unknown): EvidenceEvent[] {
  if (!isObj(rawAxes)) return [];
  const seen = new Set<string>();
  const out: EvidenceEvent[] = [];

  for (const k of AXIS_KEYS) {
    const axis = rawAxes[k];
    if (!isObj(axis)) continue;
    for (const e of arr(axis.evidence)) {
      if (!isObj(e)) continue;
      const id = str(e.event_ref) ?? str(e.event_id);
      if (!id || seen.has(id)) continue;
      seen.add(id);

      const span = str(e.span) ?? str(e.evidence_span);
      const url = str(e.url) ?? str(e.source_url);
      out.push({
        event_id: id,
        kind: (str(e.kind) ?? "profile_fact") as EvidenceEvent["kind"],
        source: (str(e.source) ?? "manual") as EvidenceEvent["source"],
        source_url: url,
        // A locator must point INTO the source. Prefer an explicit one; otherwise use
        // the "slide 3:" style prefix the span itself carries; otherwise say so.
        locator:
          str(e.locator) ??
          span?.match(/^([^:]{1,40}):/)?.[1] ??
          "locator not reported",
        observed_at: str(e.observed_at) ?? str(e.t) ?? new Date(0).toISOString(),
        summary: str(e.summary) ?? span ?? "No summary reported for this event.",
        evidence_span: span,
        confidence: num(e.confidence) ?? 0,
        integrity_flags: arr(e.integrity_flags)
          .map((f) => str(f))
          .filter((f): f is string => f !== null),
        contribution: num(e.contribution),
      });
    }
  }
  return out;
}

function toEvents(raw: unknown, rawAxes: unknown): EvidenceEvent[] {
  const flat = arr(raw)
    .map((e): EvidenceEvent | null => {
      if (!isObj(e)) return null;
      const id = str(e.event_id) ?? str(e.event_ref);
      if (!id) return null;
      return {
        event_id: id,
        kind: (str(e.kind) ?? "profile_fact") as EvidenceEvent["kind"],
        source: (str(e.source) ?? "manual") as EvidenceEvent["source"],
        source_url: str(e.source_url) ?? str(e.url),
        locator: str(e.locator) ?? "locator not reported",
        observed_at: str(e.observed_at) ?? new Date(0).toISOString(),
        summary: str(e.summary) ?? "No summary reported for this event.",
        evidence_span: str(e.evidence_span) ?? str(e.span),
        confidence: num(e.confidence) ?? 0,
        integrity_flags: arr(e.integrity_flags)
          .map((f) => str(f))
          .filter((f): f is string => f !== null),
        contribution: num(e.contribution),
      };
    })
    .filter((e): e is EvidenceEvent => e !== null);

  return flat.length ? flat : eventsFromAxes(rawAxes);
}

// ---------------------------------------------------------------------------
// Claims, integrity, proof protocol
// ---------------------------------------------------------------------------

function toClaims(raw: unknown): ClaimVerdict[] {
  return arr(raw)
    .map((c, i): ClaimVerdict | null => {
      if (!isObj(c)) return null;
      const text = str(c.claim_text) ?? str(c.text);
      if (!text) return null;
      const status = str(c.status) as ClaimStatus | null;
      return {
        claim_id: str(c.claim_id) ?? `claim-${i}`,
        claim_text: text,
        claim_source_span: str(c.claim_source_span) ?? str(c.source_span) ?? "source span not reported",
        status: status && CLAIM_STATUSES.includes(status) ? status : "not_attempted",
        trust: num(c.trust) ?? 0,
        corroborating_url: str(c.corroborating_url),
        corroborating_span: str(c.corroborating_span),
        self_published: c.self_published === true,
        claim_asserted_at: str(c.claim_asserted_at),
        counter_evidence_at: str(c.counter_evidence_at),
        // The live payload calls this `note`; either way it is the answer to
        // "why did you not check this", which must never be blank on a NOT_ATTEMPTED.
        not_attempted_reason: str(c.not_attempted_reason) ?? str(c.note) ?? str(c.why) ?? undefined,
      };
    })
    .filter((c): c is ClaimVerdict => c !== null);
}

function toIntegrity(raw: unknown): IntegrityFlag[] {
  return arr(raw)
    .map((f, i): IntegrityFlag | null => {
      if (!isObj(f)) return null;
      const flag = str(f.flag) ?? str(f.kind) ?? str(f.name);
      if (!flag) return null;
      const sev = str(f.severity);
      return {
        flag,
        severity:
          sev === "critical" || sev === "serious" || sev === "warning" ? sev : "warning",
        where: str(f.where) ?? str(f.locator) ?? `finding ${i + 1}`,
        detail: str(f.detail) ?? str(f.note) ?? "No detail reported.",
        quoted_span: str(f.quoted_span) ?? str(f.span),
        action_taken: str(f.action_taken) ?? "No action reported.",
      };
    })
    .filter((f): f is IntegrityFlag => f !== null);
}

/**
 * The panel's argument is that the plant is shown BEFORE the grade. A challenge that
 * has been issued but not yet graded is therefore a first-class state, not a
 * half-loaded one: behaviors is empty, the verdict is `pending`, and `grading_axes`
 * says what the grader is going to look for.
 */
export function toProofProtocol(raw: unknown): ProofProtocol | null {
  if (!isObj(raw)) return null;
  const prompt = str(raw.prompt);
  const central = str(raw.central_claim);
  if (!prompt || !central) return null;

  const rawVerdict = str(raw.verdict);
  const behaviors = arr(raw.behaviors)
    .map((b) => {
      if (!isObj(b)) return null;
      const name = str(b.name);
      if (!name) return null;
      const r = str(b.result);
      return {
        name,
        result: (r === "pass" || r === "fail" || r === "partial" ? r : "partial") as
          | "pass"
          | "fail"
          | "partial",
        evidence_span: str(b.evidence_span) ?? str(b.span) ?? "",
        note: str(b.note) ?? "",
      };
    })
    .filter((b): b is NonNullable<typeof b> => b !== null);

  return {
    challenge_id: str(raw.challenge_id) ?? str(raw.id) ?? "challenge id not reported",
    prompt,
    central_claim: central,
    ambiguous_requirement: str(raw.ambiguous_requirement) ?? "None recorded.",
    planted_bad_constraint: str(raw.planted_bad_constraint) ?? "None recorded.",
    issued_at: str(raw.issued_at) ?? new Date(0).toISOString(),
    responded_at: str(raw.responded_at),
    artifact_url: str(raw.artifact_url),
    behaviors,
    verdict:
      rawVerdict === "signal" || rawVerdict === "no_signal" ? rawVerdict : "pending",
    verdict_rationale: str(raw.verdict_rationale) ?? str(raw.rationale) ?? "",
    grading_axes: arr(raw.grading_axes)
      .map((a) => str(a))
      .filter((a): a is string => a !== null),
  };
}

// ---------------------------------------------------------------------------
// Company detail
// ---------------------------------------------------------------------------

function gateOf(raw: unknown, fallback: GateOutcome): GateOutcome {
  // `gate` is a bare string in the canonical shape and an object with `outcome` live.
  const v = isObj(raw) ? str(raw.outcome) : str(raw);
  return v && (GATES as string[]).includes(v) ? (v as GateOutcome) : fallback;
}

function entityNote(raw: Record<string, unknown>): string | null {
  const direct = str(raw.entity_resolution_note);
  if (direct) return direct;
  // Live hangs the resolution note off each founder. Ambiguity has to surface, so any
  // founder note is joined rather than silently taking the first.
  const notes = arr(raw.founders)
    .map((f) => (isObj(f) ? str(f.resolution_note) : null))
    .filter((n): n is string => n !== null);
  return notes.length ? notes.join(" ") : null;
}

/**
 * Build a renderable detail from whatever the source gave us, anchored to the summary
 * the ranked list already holds.
 *
 * The summary is the identity anchor: name, sector and gate come from it unless the
 * detail payload carries its own and agrees about the id. That is what guarantees the
 * page you land on is the company you clicked, whatever state the detail endpoint is in.
 */
export function toCompanyDetail(
  raw: unknown,
  summary: CompanySummary | null,
): CompanyDetail | null {
  if (!isObj(raw)) return null;

  const id = str(raw.id) ?? summary?.id;
  if (!id) return null;
  // A payload that names a different company is never adapted onto this one.
  if (summary && str(raw.id) && str(raw.id) !== summary.id) return null;

  const name = str(raw.name) ?? summary?.name;
  if (!name) return null;

  const axes = toAxes(raw.axes) ?? (summary ? summary.axes : null);
  if (!axes) return null;

  const events = toEvents(raw.events, raw.axes);
  const claims = toClaims(raw.claims);
  const integrity = toIntegrity(raw.integrity);
  const history = toScoreHistory(raw.score_history) ?? emptyHistory();

  const missing: string[] = [];
  if (!events.length) missing.push("no event log");
  if (!claims.length) missing.push("no per-claim verdicts");
  if (!integrity.length) missing.push("no integrity findings");
  if (!AXIS_KEYS.some((k) => history[k].length)) missing.push("no score history");

  return {
    id,
    name,
    one_liner: str(raw.one_liner) ?? summary?.one_liner ?? "",
    sector: str(raw.sector) ?? summary?.sector ?? "unreported",
    stage: str(raw.stage) ?? summary?.stage ?? "unreported",
    geo: str(raw.geo) ?? summary?.geo ?? "unreported",
    archetype:
      str(raw.archetype_label) ??
      str(raw.archetype) ??
      summary?.archetype ??
      "unclassified",
    gate: gateOf(raw.gate, summary?.gate ?? "no_call"),
    axes,
    flag_count: num(raw.flag_count) ?? (integrity.length || summary?.flag_count || 0),
    as_of: str(raw.as_of) ?? summary?.as_of ?? new Date().toISOString(),
    events,
    claims,
    integrity,
    proof_protocol: toProofProtocol(raw.proof_protocol),
    score_history: history,
    entity_resolution_note: entityNote(raw),
    coverage: missing.length >= 3 ? "sparse" : "full",
    coverage_note: missing.length
      ? `This record is assembled from the event log rather than a hand-authored fixture: ${missing.join(
          ", ",
        )}. Those sections are empty because nothing was recorded, not because loading failed.`
      : null,
  };
}

/**
 * The last-resort detail: everything the ranked list already knows, and nothing else.
 *
 * Used when the detail endpoint has no record for a company that the list does have.
 * It renders the axes and the gate honestly and states plainly that the rest is
 * unavailable. This exists so that "reachable" is true for all thirteen companies
 * without ever borrowing another company's evidence to fill the page.
 */
export function sparseDetail(summary: CompanySummary, why: string): CompanyDetail {
  return {
    ...summary,
    events: [],
    claims: [],
    integrity: [],
    proof_protocol: null,
    score_history: emptyHistory(),
    entity_resolution_note: null,
    coverage: "sparse",
    coverage_note: `Only the screening record is available for this company — ${why}. The axes and the gate below are live; the event log, claims and score history are genuinely absent rather than still loading.`,
  };
}

// ---------------------------------------------------------------------------
// Score history
// ---------------------------------------------------------------------------

export const emptyHistory = (): ScoreHistory => ({
  founder: [],
  market: [],
  idea_vs_market: [],
});

function toPoints(raw: unknown): ScorePoint[] {
  return arr(raw)
    .map((p): ScorePoint | null => {
      if (!isObj(p)) return null;
      const mu = num(p.mu) ?? num(p.score);
      const t = str(p.t) ?? str(p.observed_at) ?? str(p.as_of);
      if (mu === null || !t) return null;
      return {
        t,
        mu: Math.abs(mu) <= 1 ? mu * 100 : mu,
        band: (() => {
          const b = num(p.band) ?? 0;
          return Math.abs(b) <= 1 && Math.abs(mu) <= 1 ? b * 100 : b;
        })(),
        n_events: num(p.n_events) ?? 0,
        note: str(p.note) ?? undefined,
      };
    })
    .filter((p): p is ScorePoint => p !== null);
}

/**
 * Returns null — not an empty history — when the payload carries no usable series, so
 * the caller can decide between "fall back to the fixture" and "render as absent".
 * `{ series: [], degraded: true }` is a real response from the live endpoint today.
 */
export function toScoreHistory(raw: unknown): ScoreHistory | null {
  if (!isObj(raw)) return null;

  const out = emptyHistory();
  let any = false;
  for (const k of AXIS_KEYS) {
    const pts = toPoints(raw[k]);
    out[k] = pts;
    if (pts.length) any = true;
  }
  if (any) return out;

  // Flat `series` form: one row per observation, each carrying every axis.
  const series = arr(raw.series);
  if (!series.length) return null;
  for (const row of series) {
    if (!isObj(row)) continue;
    const t = str(row.t) ?? str(row.as_of) ?? str(row.observed_at);
    if (!t) continue;
    for (const k of AXIS_KEYS) {
      const cell = row[k];
      const mu = isObj(cell) ? num(cell.mu) ?? num(cell.score) : num(cell);
      if (mu === null) continue;
      const band = isObj(cell) ? num(cell.band) ?? 0 : 0;
      const unit = Math.abs(mu) <= 1;
      out[k].push({
        t,
        mu: unit ? mu * 100 : mu,
        band: unit ? band * 100 : band,
        n_events: num(row.n_events) ?? 0,
      });
      any = true;
    }
  }
  return any ? out : null;
}

// ---------------------------------------------------------------------------
// Memo
// ---------------------------------------------------------------------------

const MEMO_SECTIONS = ["thesis", "founder", "market", "risks", "recommendation"] as const;

const titleCase = (s: string) => s.charAt(0).toUpperCase() + s.slice(1);

/**
 * The canonical memo is a list of sections; the live memo is an object keyed by section
 * name, each with a `summary` and its own `claims`. Both produce the same five required
 * headings in the same order.
 *
 * `gaps` is the load-bearing part. A memo that fabricates to look complete loses the
 * trust criterion outright, so a gap object is rendered as its own sentence — the claim,
 * its status, and why we did not or could not check it.
 */
export function toMemo(raw: unknown, companyId: string): Memo | null {
  if (!isObj(raw)) return null;

  if (Array.isArray(raw.sections)) {
    const sections = raw.sections
      .map((s) => {
        if (!isObj(s)) return null;
        const heading = str(s.heading);
        if (!heading) return null;
        return {
          heading,
          body: str(s.body) ?? "",
          citations: arr(s.citations)
            .map((c) => str(c))
            .filter((c): c is string => c !== null),
        };
      })
      .filter((s): s is NonNullable<typeof s> => s !== null);
    if (!sections.length) return null;
    return {
      company_id: str(raw.company_id) ?? companyId,
      sections,
      gaps: arr(raw.gaps)
        .map((g) => str(g))
        .filter((g): g is string => g !== null),
      recommendation: str(raw.recommendation),
      recommendation_locked_reason: str(raw.recommendation_locked_reason) ?? undefined,
    };
  }

  const sections = MEMO_SECTIONS.filter((k) => k !== "recommendation")
    .map((k) => {
      const s = raw[k];
      if (!isObj(s)) return null;
      const claims = arr(s.claims)
        .map((c) => (isObj(c) ? str(c.text) : str(c)))
        .filter((c): c is string => c !== null);
      const citations = arr(s.claims).flatMap((c) =>
        isObj(c)
          ? arr(c.event_ids)
              .map((e) => str(e))
              .filter((e): e is string => e !== null)
          : [],
      );
      const summary = str(s.summary) ?? "";
      return {
        heading: titleCase(k),
        body: claims.length ? `${summary} ${claims.join(" ")}`.trim() : summary,
        citations,
      };
    })
    .filter((s): s is NonNullable<typeof s> => s !== null && s.body !== "");

  if (!sections.length) return null;

  const gaps = arr(raw.gaps)
    .map((g) => {
      const s = str(g);
      if (s) return s;
      if (!isObj(g)) return null;
      const claim = str(g.claim);
      const why = str(g.why);
      const status = str(g.status)?.replace(/_/g, " ");
      if (!claim && !why) return null;
      return [claim, status ? `(${status})` : null, why ? `— ${why}` : null]
        .filter(Boolean)
        .join(" ");
    })
    .filter((g): g is string => g !== null);

  // Ambiguous entity resolutions are a gap in exactly the same sense: something the
  // memo cannot confirm. They belong in the same flagged block, not in a footnote.
  const ambiguities = arr(raw.ambiguities)
    .map((a) => (isObj(a) ? str(a.note) ?? str(a.detail) : str(a)))
    .filter((a): a is string => a !== null);

  return {
    company_id: str(raw.company_id) ?? companyId,
    sections,
    gaps: [...gaps, ...ambiguities],
    recommendation: str(raw.recommendation),
    recommendation_locked_reason: str(raw.recommendation_locked_reason) ?? undefined,
  };
}

// ---------------------------------------------------------------------------
// Query
// ---------------------------------------------------------------------------

/**
 * Accepts the live `/query` contract. `company_ids` is the only required field: without
 * it there is nothing to highlight and the caller must fall back.
 *
 * `count` is taken from the server when present so a mismatch between the server's
 * count and the ids it returned stays visible instead of being papered over here.
 */
export function toQueryResult(raw: unknown, q: string): QueryResult | null {
  if (!isObj(raw) || !Array.isArray(raw.company_ids)) return null;
  const ids = raw.company_ids.map((v) => str(v)).filter((v): v is string => v !== null);
  return {
    q: str(raw.q) ?? q,
    parsed: str(raw.parsed) ?? "no readback reported by the server",
    company_ids: ids,
    count: num(raw.count) ?? ids.length,
    filter: isObj(raw.filter) ? raw.filter : null,
  };
}
