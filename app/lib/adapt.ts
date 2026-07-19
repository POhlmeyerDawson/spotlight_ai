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
  ConfidenceComponent,
  Dissent,
  EventTrace,
  EvidenceEvent,
  GateOutcome,
  IntegrityFlag,
  Memo,
  ProofProtocol,
  QueryResult,
  Recommendation,
  ScoreHistory,
  ScorePoint,
  UnderlyingEvidence,
} from "./types";
import { AXIS_KEYS, TREND_UNIT_DIRECTION } from "./types";

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
  const trendUnit = str(raw.trend_unit) ?? undefined;
  // A DIRECTION is a sign, not a score, so the 0..1 -> 0..100 rescale must not touch it.
  // Without this guard a market axis trending "up" (1.0) renders as "+100.0".
  const trendFactor = trendUnit === TREND_UNIT_DIRECTION ? 1 : factor;

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
    trend: trend === null ? null : trend * trendFactor,
    confidence: num(raw.confidence) ?? 0,
    evidence_event_ids: ids.length ? ids : inline,
    reason: str(raw.reason) ?? undefined,
    // Only a literal `false` means seeded. An absent flag is unknown, not "not live",
    // and marking an unknown axis as seeded would be its own false claim.
    live: typeof raw.live === "boolean" ? raw.live : undefined,
    trend_unit: trendUnit,
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

/**
 * Severity, derived from the flag name when the payload does not state one.
 *
 * The live payload carries no `severity` field at all, so everything defaulted to
 * "warning" — which meant a caught prompt injection never qualified as critical and the
 * panel that rides at the top of the page for critical findings never fired. A planted
 * instruction is the single most decision-relevant thing the sanitizer can find; it
 * cannot be allowed to render at the same weight as low-confidence OCR.
 */
const CRITICAL_FLAGS = [
  "prompt_injection_detected",
  "injection_stripped",
  "fabricated_history",
];
const SERIOUS_FLAGS = ["keyword_stuffing", "low_substance_burst", "contradiction"];

function severityOf(flag: string, stated: string | null): IntegrityFlag["severity"] {
  if (stated === "critical" || stated === "serious" || stated === "warning") return stated;
  if (CRITICAL_FLAGS.includes(flag)) return "critical";
  if (SERIOUS_FLAGS.includes(flag)) return "serious";
  return "warning";
}

/** Renders a `measure` object as one readable line rather than dropping it. */
function measureLine(v: unknown): string | null {
  if (!isObj(v)) return null;
  const parts = Object.entries(v)
    .filter(([, val]) => typeof val === "number" || typeof val === "string")
    .map(([k, val]) => `${k.replace(/_/g, " ")} ${val}`);
  return parts.length ? parts.join(" · ") : null;
}

function toIntegrity(raw: unknown): IntegrityFlag[] {
  return arr(raw)
    .map((f, i): IntegrityFlag | null => {
      if (!isObj(f)) return null;
      const flag = str(f.flag) ?? str(f.kind) ?? str(f.name);
      if (!flag) return null;

      const control = isObj(f.control_comparison) ? f.control_comparison : null;

      return {
        flag,
        severity: severityOf(flag, str(f.severity)),
        // Live calls this `location`.
        where: str(f.where) ?? str(f.location) ?? str(f.locator) ?? `finding ${i + 1}`,
        // No `detail` field exists live; the measurements are the detail. Empty when
        // there is genuinely nothing — a card carrying the effect, the quoted payload
        // and the action taken does not need a line announcing that a fourth field was
        // absent. "No detail reported." was pure placeholder noise.
        detail: str(f.detail) ?? str(f.note) ?? measureLine(f.measure) ?? "",
        // Live calls the quoted payload `extracted_text`. Dropping it meant the caught
        // injection — the thing worth seeing — was never rendered.
        quoted_span: str(f.quoted_span) ?? str(f.span) ?? str(f.extracted_text),
        // Live calls this `handling`.
        action_taken: str(f.action_taken) ?? str(f.handling) ?? "No action reported.",
        effect_on_decision: str(f.effect_on_decision) ?? undefined,
        effect_on_score: str(f.effect_on_score) ?? undefined,
        control_note: control ? str(control.conclusion) ?? undefined : undefined,
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
 * The unlocked memo carries `recommendation` as an OBJECT (`{summary, claims,
 * computed_verdict}`), not a string. Reading it with `str()` therefore returned null on
 * every live response, and null is precisely the sentinel the UI reads as "still locked"
 * — so opening the dissent released the server's lock and the page kept showing the
 * padlock anyway. Prefer the computed verdict, which is the sentence that states the
 * decision, and fall back to the summary.
 */
function recommendationText(raw: unknown): string | null {
  const direct = str(raw);
  if (direct) return direct;
  if (!isObj(raw)) return null;
  const verdict = str(raw.computed_verdict);
  const summary = str(raw.summary);
  if (verdict && summary && !summary.startsWith(verdict)) return `${verdict} — ${summary}`;
  return verdict ?? summary;
}

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
      recommendation: recommendationText(raw.recommendation),
      recommendation_locked_reason: str(raw.recommendation_locked_reason) ?? undefined,
      recommendation_detail: toRecommendation(raw.investment_recommendation),
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
    recommendation: recommendationText(raw.recommendation),
    recommendation_locked_reason:
      str(raw.recommendation_locked_reason) ?? str(raw.decision_locked_reason) ?? undefined,
    recommendation_detail: toRecommendation(raw.investment_recommendation),
  };
}

// ---------------------------------------------------------------------------
// Recommendation
// ---------------------------------------------------------------------------

/**
 * `investment_recommendation` off the unlocked memo.
 *
 * Returns null rather than a stub when the block is absent, because a recommendation
 * panel with no decision in it is worse than no panel: the whole section exists to
 * answer "what should I do and what is stopping me".
 */
export function toRecommendation(raw: unknown): Recommendation | null {
  if (!isObj(raw)) return null;
  const decision = str(raw.decision);
  const gate = str(raw.gate);
  // Without a decision AND without a gate there is nothing to lead the page with.
  if (!decision && !gate) return null;

  const gateOr = (v: string | null, fb: GateOutcome): GateOutcome =>
    v && (GATES as string[]).includes(v) ? (v as GateOutcome) : fb;

  const cs = raw.check_size;
  const conf = raw.confidence;

  const governing = isObj(raw.governing_axis) ? raw.governing_axis : null;
  const gName = governing ? str(governing.name) : null;
  const gScore = governing ? num(governing.score) : null;

  return {
    decision: gateOr(decision, gateOr(gate, "no_call")),
    // Null is "no cheque", which the reason string explains. It is NEVER zero.
    amount_usd: num(raw.amount_usd),
    currency: str(raw.currency) ?? "USD",
    reason: str(raw.reason) ?? "No reason reported for this decision.",
    check_size: isObj(cs)
      ? {
          currency: str(cs.currency) ?? "USD",
          min: num(cs.min) ?? 0,
          target: num(cs.target) ?? 0,
          max: num(cs.max) ?? 0,
        }
      : null,
    check_size_source: str(raw.check_size_source),
    gate: gateOr(gate, gateOr(decision, "no_call")),
    governing_axis:
      gName && gScore !== null
        ? // Governing score arrives on 0..1; the page speaks score units everywhere else.
          { name: gName, score: Math.abs(gScore) <= 1 ? gScore * 100 : gScore }
        : null,
    confidence: isObj(conf)
      ? {
          value: num(conf.value) ?? 0,
          unit: str(conf.unit) ?? "",
          method: str(conf.method) ?? "",
          binding_component: str(conf.binding_component),
          components: arr(conf.components)
            .map((c): ConfidenceComponent | null => {
              if (!isObj(c)) return null;
              const name = str(c.name);
              if (!name) return null;
              return {
                name,
                raw: num(c.raw),
                unit: str(c.unit) ?? "",
                support: num(c.support),
                basis: str(c.basis) ?? "",
              };
            })
            .filter((c): c is ConfidenceComponent => c !== null),
        }
      : null,
  };
}

// ---------------------------------------------------------------------------
// Dissent
// ---------------------------------------------------------------------------

/**
 * The anti-memo. The route nests it under `anti_memo` alongside the decision and the
 * lock state, so both shapes are accepted.
 *
 * `axis_spreads` is the part that needed fixing. The wire carries 0..1; the page speaks
 * score units. Rendering 0.499 with `toFixed(0)` printed "0" against a 1%-wide bar for
 * every axis, which read as "bull and bear agree perfectly" — the exact opposite of a
 * half-scale disagreement. Axes the payload does not carry are OMITTED here rather than
 * defaulted to zero, so the component can tell "not computed" from "no spread".
 */
export function toDissent(raw: unknown): Dissent | null {
  const src = isObj(raw) && isObj(raw.anti_memo) ? raw.anti_memo : raw;
  if (!isObj(src)) return null;
  const bear = str(src.bear_case);
  if (!bear) return null;

  const spreadsRaw = isObj(src.axis_spreads) ? src.axis_spreads : {};
  // One scale decision for the whole map, as with the axes: mixing per-field guesses
  // would mis-scale a genuine 1-out-of-100 spread.
  const values = AXIS_KEYS.map((k) => num(spreadsRaw[k])).filter(
    (v): v is number => v !== null,
  );
  const factor = values.length && values.every((v) => Math.abs(v) <= 1) ? 100 : 1;

  const axis_spreads: Partial<Record<AxisKey, number>> = {};
  for (const k of AXIS_KEYS) {
    const v = num(spreadsRaw[k]);
    if (v !== null) axis_spreads[k] = v * factor;
  }

  return {
    company_id: str(src.company_id) ?? "",
    bear_case: bear,
    weakest_evidence: arr(src.weakest_evidence)
      .map((w) => str(w))
      .filter((w): w is string => w !== null),
    load_bearing_claim:
      str(src.load_bearing_claim) ?? "No load-bearing claim was named by the dissent.",
    axis_spreads,
  };
}

// ---------------------------------------------------------------------------
// Event trace
// ---------------------------------------------------------------------------

/**
 * `GET /companies/{id}/trace/{event_id}` — the bottom of the drill-down.
 *
 * This is the endpoint that makes the trace genuinely reach a commit span. The drawer
 * previously read events out of the local company payload and never called it, which is
 * why a rollup event looked like a dead end: its own span is a generated summary. Here
 * the generated-ness is preserved (`span_is_generated`) along with the real receipts
 * underneath it (`underlying_evidence`), so neither gets flattened into the other.
 */
export function toEventTrace(raw: unknown): EventTrace | null {
  if (!isObj(raw)) return null;
  const id = str(raw.event_id);
  if (!id) return null;

  const underlying: UnderlyingEvidence[] = arr(raw.underlying_evidence)
    .map((u): UnderlyingEvidence | null => {
      if (!isObj(u)) return null;
      const uid = str(u.event_id);
      if (!uid) return null;
      return {
        event_id: uid,
        kind: str(u.kind) ?? "unreported",
        source: str(u.source) ?? "unreported",
        source_url: str(u.source_url),
        quoted_span: str(u.quoted_span) ?? str(u.span),
        observed_at: str(u.observed_at),
      };
    })
    .filter((u): u is UnderlyingEvidence => u !== null);

  const span = str(raw.quoted_span) ?? str(raw.evidence_span);

  return {
    event_id: id,
    quoted_span: span,
    // `has_span` is the server's own answer. Fall back to whether we actually got one
    // rather than assuming true — the two disagreeing is exactly what we want visible.
    has_span: typeof raw.has_span === "boolean" ? raw.has_span : span !== null,
    span_is_generated: raw.span_is_generated === true,
    underlying_evidence: underlying,
    source_url: str(raw.source_url),
    chain: arr(raw.chain)
      .map((c) => {
        if (!isObj(c)) return null;
        const step = str(c.step);
        if (!step) return null;
        const d = c.detail;
        return { step, detail: typeof d === "string" ? d : JSON.stringify(d) };
      })
      .filter((c): c is { step: string; detail: string } => c !== null),
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
