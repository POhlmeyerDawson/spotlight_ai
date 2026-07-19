/**
 * Wire types for the VC Brain dashboard.
 *
 * These mirror `schema/events.py` (owner: A) and the routes in `api/main.py` (owner: D).
 * Rule that outranks every other: there is NO blended score type here. Three axes,
 * always separate, never averaged — not on the wire, not in a component.
 */

export type AxisKey = "founder" | "market" | "idea_vs_market";

export const AXIS_KEYS: AxisKey[] = ["founder", "market", "idea_vs_market"];

export const AXIS_LABEL: Record<AxisKey, string> = {
  founder: "Founder",
  market: "Market",
  idea_vs_market: "Idea-vs-Market",
};

/**
 * The palette allows no sixth hue (DESIGN.md §2), so the three axes are NOT three
 * series colours. They are told apart by position (fixed column order, always the
 * same), by marker shape, and by their labels. Every axis mark is drawn in the
 * plate accent; nothing about an axis is carried by hue.
 */
export const AXIS_MARKER: Record<AxisKey, "dot" | "square" | "diamond"> = {
  founder: "dot",
  market: "square",
  idea_vs_market: "diamond",
};

/** Roman numeral shown beside each axis — position made explicit, not colour. */
export const AXIS_INDEX: Record<AxisKey, string> = {
  founder: "I",
  market: "II",
  idea_vs_market: "III",
};

export type GateOutcome = "proceed" | "proof_protocol" | "no_call";

export type ClaimStatus =
  | "verified"
  | "contradicted"
  | "unverifiable"
  | "not_attempted";

export type EventKind =
  | "repo_activity"
  | "commit_burst"
  | "release"
  | "paper"
  | "hn_post"
  | "hn_comment"
  | "deck_claim"
  | "profile_fact"
  | "green_flag"
  | "validation_result"
  | "proof_challenge_issued"
  | "proof_artifact"
  | "proof_behavior"
  | "contradiction"
  | "integrity"
  | "entity_merge";

export type SourceKind =
  | "github"
  | "hn"
  | "arxiv"
  | "web"
  | "deck"
  | "proof_protocol"
  | "validator"
  | "manual";

/**
 * One axis of the three-axis screen. `band` is ± in score units and is always displayed.
 *
 * `score`, `trend` and `band` are NULLABLE on purpose. A cold-start company has axes
 * with nothing to score, and the backend reports that as null plus a `reason`. Rendering
 * null as 0 would turn "we have no evidence" into "we looked and they scored zero" —
 * those are different claims and the gate exists precisely to tell them apart. Every
 * component that draws an axis must handle null as ABSENCE, never as a number.
 */
export interface Axis {
  score: number | null; // 0..100
  trend: number | null; // signed momentum (structural, not a diff of scores)
  confidence: number; // 0..1
  band: number | null; // ± uncertainty, in score units
  evidence_event_ids: string[];
  /** Present when score is null OR when the evidence list is empty — the backend's
   *  stated reason. An axis with no receipts returns an EMPTY evidence list plus this,
   *  rather than padded placeholders, so the reason is what gets rendered. */
  reason?: string;
  /**
   * True when this axis was computed by the screen; false when it is a seeded value.
   * A seeded number must never be presented as a computed one, so this drives a chip
   * on the card rather than being dropped on the floor.
   */
  live?: boolean;
  /**
   * What `trend` is measured in. Two units are in play and they are NOT comparable:
   * `score_points_per_30d` is a rate, `direction_-1_to_1` is a sign. Rendering a
   * direction of 1.0 as "+1.0" next to a rate of +0.24 invites exactly the wrong read.
   */
  trend_unit?: string;
}

export const TREND_UNIT_DIRECTION = "direction_-1_to_1";

/**
 * An observation. `evidence_span` is the quoted text the trace drill-down must
 * bottom out in — a trace that stops at a source name is a broken trace.
 */
export interface EvidenceEvent {
  event_id: string;
  kind: EventKind;
  source: SourceKind;
  source_url: string | null;
  /** Where inside the source: "slide 7", "commit a1b2c3d", "HN item 38911204". */
  locator: string;
  observed_at: string; // ISO
  summary: string;
  evidence_span: string | null; // THE QUOTED SPAN
  confidence: number;
  integrity_flags: string[];
  /**
   * Signed contribution of this event to the axis score, in score units.
   * Null when the source lists the event as evidence but does not attribute a
   * per-event contribution to it. Shown as "—", never as 0.0.
   */
  contribution: number | null;
}

export interface ScorePoint {
  t: string; // ISO
  mu: number;
  band: number; // ± half-width; narrows as observations accumulate
  n_events: number;
  /** Optional marker for the event that landed at this step. */
  note?: string;
}

export type ScoreHistory = Record<AxisKey, ScorePoint[]>;

export interface ClaimVerdict {
  claim_id: string;
  claim_text: string;
  claim_source_span: string; // where the founder said it
  status: ClaimStatus;
  trust: number; // 0..1, per-claim. There is no company-level trust number.
  corroborating_url: string | null;
  corroborating_span: string | null;
  self_published: boolean;
  claim_asserted_at: string | null;
  counter_evidence_at: string | null;
  /** Present on NOT_ATTEMPTED — we say why we did not look. */
  not_attempted_reason?: string;
}

export interface IntegrityFlag {
  flag: string; // "prompt_injection_detected" | "ocr_low_conf" | "transliterated_name"
  severity: "critical" | "serious" | "warning";
  where: string; // "slide 7", "deck page 3"
  detail: string;
  /** The stripped/suspect text itself, quoted. The caught injection is a demo beat. */
  quoted_span: string | null;
  action_taken: string;
  /**
   * What this finding did to the DECISION, in the backend's own words ("decisive — a
   * planted instruction is itself the strongest signal in this file"). This is the only
   * part of an integrity flag that completes "this matters because…", so it leads the
   * card rather than sitting under the forensics.
   */
  effect_on_decision?: string;
  /** What it did to the SCORE, which is often "none — the sanitizer ran first". */
  effect_on_score?: string;
  /**
   * The control comparison's conclusion. This is what turns "we flagged a big commit
   * burst" into "we did not false-positive a fast builder, and here is the control that
   * proves it" — a claim about the detector, not just about this company.
   */
  control_note?: string;
}

export interface ProofBehavior {
  name: string;
  result: "pass" | "fail" | "partial";
  evidence_span: string;
  note: string;
}

/**
 * Cold-start challenge. The point of the panel is showing WHAT WAS PLANTED
 * (`ambiguous_requirement`, `planted_bad_constraint`) next to how they behaved.
 */
export interface ProofProtocol {
  challenge_id: string;
  prompt: string;
  central_claim: string;
  ambiguous_requirement: string;
  planted_bad_constraint: string;
  issued_at: string;
  responded_at: string | null;
  artifact_url: string | null;
  behaviors: ProofBehavior[];
  verdict: "signal" | "no_signal" | "pending";
  verdict_rationale: string;
  /** What the grader will look for. Present before grading has run. */
  grading_axes?: string[];
}

export interface CompanySummary {
  id: string;
  name: string;
  one_liner: string;
  sector: string;
  stage: string;
  geo: string;
  archetype: string;
  gate: GateOutcome;
  axes: Record<AxisKey, Axis>;
  /** Count of open integrity flags — surfaced in the list, not buried. */
  flag_count: number;
  as_of: string;
}

export interface CompanyDetail extends CompanySummary {
  events: EvidenceEvent[];
  claims: ClaimVerdict[];
  integrity: IntegrityFlag[];
  proof_protocol: ProofProtocol | null;
  score_history: ScoreHistory;
  entity_resolution_note: string | null;
  /**
   * How much of this record the source actually carried. Five companies have
   * hand-authored fixtures; the other eight are assembled from the event log and are
   * genuinely thinner. `sparse` renders as sparse — sections that have no data say so
   * instead of disappearing, which is what stops thin from reading as broken.
   */
  coverage: "full" | "sparse";
  /** Plain-English statement of what is missing and why. Shown, never hidden. */
  coverage_note: string | null;
}

export interface MemoSection {
  heading: string;
  body: string;
  /** Event IDs cited inline by this section. */
  citations: string[];
}

/**
 * One input to the confidence figure. The recommendation's confidence is the MINIMUM of
 * these, never a mean — the same weakest-link policy the three axes are ranked by. The
 * component holding the minimum is the `binding_component`, and naming it is the whole
 * point: "0.40" tells you nothing you can act on, "the market axis caps it at 0.40" does.
 */
export interface ConfidenceComponent {
  name: string;
  raw: number | null;
  unit: string;
  /** 0..1. Null means not applicable, and such components are excluded from the minimum. */
  support: number | null;
  basis: string;
}

export interface CheckSize {
  currency: string;
  min: number;
  target: number;
  max: number;
}

/**
 * The computed recommendation. This is decision input #1 and it leads the page.
 *
 * `amount_usd` is null on every non-PROCEED decision, and that is a FINAL ANSWER rather
 * than a smaller cheque — the reason string says which. Never render a null amount as $0.
 */
export interface Recommendation {
  decision: GateOutcome;
  amount_usd: number | null;
  currency: string;
  reason: string;
  check_size: CheckSize | null;
  check_size_source: string | null;
  gate: GateOutcome;
  governing_axis: { name: string; score: number } | null;
  confidence: {
    value: number;
    unit: string;
    method: string;
    /** Which component held the minimum. The binding constraint, named. */
    binding_component: string | null;
    components: ConfidenceComponent[];
  } | null;
}

export interface Memo {
  company_id: string;
  sections: MemoSection[];
  /** Gaps are flagged, never filled. */
  gaps: string[];
  /** null until the dissent is opened. The server enforces this; the UI must not fake it. */
  recommendation: string | null;
  recommendation_locked_reason?: string;
  /**
   * The structured recommendation. Present only when the server has released it, for
   * the same reason `recommendation` is: it arrives with the unlocked memo and not
   * before. A locked memo carries null here and the UI renders the lock.
   *
   * Optional because the hand-authored fixtures genuinely do not have one — they carry
   * a written recommendation and no computed cheque. The panel renders whichever it has
   * and never manufactures the missing half.
   */
  recommendation_detail?: Recommendation | null;
}

export interface Dissent {
  company_id: string;
  bear_case: string;
  weakest_evidence: string[];
  load_bearing_claim: string;
  /**
   * Per-axis distance between the memo and the dissent, normalised to SCORE UNITS
   * (0..100) by the adapter — the wire carries 0..1. An axis absent from this map was
   * NOT COMPUTED, which is a different statement from a spread of zero, and the two
   * must not render alike: identical-zero bars read as "bull and bear agree perfectly",
   * the opposite of what an uncomputed spread means.
   */
  axis_spreads: Partial<Record<AxisKey, number>>;
}

/**
 * One node of the trace, from `GET /companies/{id}/trace/{event_id}`.
 *
 * The drill-down must bottom out in a quoted span with its source URL. For a ROLLUP
 * event the top-level `quoted_span` is a summary this system generated ("1/24 applicable
 * green flags fired") — a real string, but not a receipt. `span_is_generated` marks that,
 * and `underlying_evidence` carries the actual receipts underneath it. Rendering the
 * rollup as though it were the receipt is the failure this field exists to prevent.
 */
export interface UnderlyingEvidence {
  event_id: string;
  kind: string;
  source: string;
  source_url: string | null;
  quoted_span: string | null;
  observed_at: string | null;
}

export interface EventTrace {
  event_id: string;
  quoted_span: string | null;
  has_span: boolean;
  span_is_generated: boolean;
  underlying_evidence: UnderlyingEvidence[];
  source_url: string | null;
  chain: { step: string; detail: string }[];
}

/**
 * The standing thesis, in the flat shape this UI edits.
 *
 * `core/thesis.py` consumes a NESTED shape (`sectors:[{id,label,weight,include}]`,
 * `stage:{include,exclude}`, `geo:{...}`, `check_size:{min,target,max}`,
 * `risk_appetite:{value:0..1}`). The translation lives in `lib/api.ts`
 * (`thesisFromApi` / `thesisToApi`) so exactly one place knows both shapes.
 *
 * This used to be written to the backend verbatim, which meant `stages`, `geos` and
 * `check_size_min` were keys NOTHING read: the panel looked like it configured the
 * fund and configured nothing.
 */
export interface Thesis {
  sectors: string[];
  stages: string[];
  geos: string[];
  check_size_min: number;
  check_size_max: number;
  risk_appetite: number; // 0..100 here, 0..1 on the wire
  notes: string;
  /**
   * The untouched document as served. Carried so a save can merge rather than
   * replace: the config has fields this UI does not model (`clearing_score`,
   * `ranking_policy`, `hard_filters`) and writing back only what we understand
   * would delete another process's work.
   */
  raw?: Record<string, unknown>;
}

export interface Trajectory {
  id: string;
  name: string;
  label: "winner" | "control";
  points: { t: string; mu: number; band: number }[];
  outcome: string;
}

export interface Backtest {
  as_of: string;
  truncation_note: string;
  threshold: number;
  /** The H12 gate. If controls clear the threshold the score measures fame. */
  fame_check_passed: boolean;
  fame_check_detail: string;
  hit_rate: number;
  n_winners: number;
  n_controls: number;
  trajectories: Trajectory[];
  correctly_deprioritized: {
    name: string;
    final_score: number;
    why: string;
    outcome: string;
  };
  lookahead_assertion: {
    events_checked: number;
    violations: number;
    detail: string;
  };
}

/**
 * The compound-query contract. `parsed` is a plain-English readback of what the query
 * was understood to mean ("sector in infra · rising trend · unverified claims") — it is
 * how "the model only translates, the filter runs in Python" becomes visible on screen.
 * Always render it, including on a zero-result query, where it is the whole explanation.
 */
export interface QueryResult {
  q: string;
  parsed: string;
  company_ids: string[];
  /** Server-reported hit count. May differ from company_ids.length if the server paginates. */
  count: number;
  /** The structured filter the server actually executed. Rendered as the receipt. */
  filter?: Record<string, unknown> | null;
  /**
   * Where the answer is weaker than it looks: a clause naming an industry nothing in
   * the list mentions, a clause the records carry no data for, or a clause that could
   * not be executed at all. These must be rendered — a query box that drops half a
   * sentence without saying so is worse than a form, because the user reads the
   * result as an answer to the question they actually asked.
   */
  warnings?: string[];
}
