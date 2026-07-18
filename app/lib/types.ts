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

/** One axis of the three-axis screen. `band` is ± in score units and is always displayed. */
export interface Axis {
  score: number; // 0..100
  trend: number; // signed momentum (structural, not a diff of scores)
  confidence: number; // 0..1
  band: number; // ± uncertainty, in score units
  evidence_event_ids: string[];
}

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
  /** Signed contribution of this event to the axis score, in score units. */
  contribution: number;
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
  flag: string; // "injection_stripped" | "ocr_low_conf" | "transliterated_name"
  severity: "critical" | "serious" | "warning";
  where: string; // "slide 7", "deck page 3"
  detail: string;
  /** The stripped/suspect text itself, quoted. The caught injection is a demo beat. */
  quoted_span: string | null;
  action_taken: string;
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
}

export interface MemoSection {
  heading: string;
  body: string;
  /** Event IDs cited inline by this section. */
  citations: string[];
}

export interface Memo {
  company_id: string;
  sections: MemoSection[];
  /** Gaps are flagged, never filled. */
  gaps: string[];
  /** null until the dissent is opened. The server enforces this; the UI must not fake it. */
  recommendation: string | null;
  recommendation_locked_reason?: string;
}

export interface Dissent {
  company_id: string;
  bear_case: string;
  weakest_evidence: string[];
  load_bearing_claim: string;
  axis_spreads: Partial<Record<AxisKey, number>>;
}

export interface Thesis {
  sectors: string[];
  stages: string[];
  geos: string[];
  check_size_min: number;
  check_size_max: number;
  risk_appetite: number; // 0..100
  notes: string;
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

export interface QueryResult {
  q: string;
  parsed: string;
  company_ids: string[];
}
