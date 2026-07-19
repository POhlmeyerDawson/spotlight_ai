"use client";

/**
 * Draft storage for user-authored council agents.
 *
 * WHY THIS IS LOCAL AND SAYS SO, LOUDLY.
 *
 * `api/routers/personal.py` serves `GET /personal/lenses` and nothing else — there is no
 * POST, PUT or DELETE for a lens, and `memory/profiles.py` has no table to put one in.
 * `PUT /profile` accepts only fund_name, focus_sectors and stated_red_lines. So an
 * authored council CANNOT currently be persisted to the account, and the frontend does
 * not get to pretend otherwise: this module keeps drafts in localStorage, namespaced by
 * user id, and every screen that reads it states in words that the drafts live in this
 * browser and do not yet reach the profile or the ranking.
 *
 * The alternative — showing a "Saved" toast against a request that was never made — is
 * the exact failure the product constraint forbids: it would put a council on screen
 * that the system does not actually hold.
 *
 * The shape mirrors `intelligence.custom_council.Lens` so that when the write route
 * lands, these records post without a translation layer:
 *   name        -> the lens label
 *   quality     -> what it looks for (the `kind` a derived lens carries)
 *   persona     -> the plain-language description the lens argues under
 *   weight      -> 0..1, normalised across the council at scoring time
 *   origin      -> "authored" | "template", never "derived" (only the API derives)
 */

export const MIN_LENSES = 2;
export const MAX_LENSES = 5;

export interface DraftAgent {
  id: string;
  name: string;
  /** The quality this agent adds score for — the whiteboard's "CyberSecurity" case. */
  quality: string;
  persona: string;
  weight: number;
  /** How this agent came to exist. NEVER "derived": the API owns that word. */
  origin: "authored" | "template";
}

const KEY = "vcbrain.council.draft";

function storageKey(userId: string) {
  return `${KEY}.${userId}`;
}

export function loadDrafts(userId: string): DraftAgent[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(storageKey(userId));
    if (!raw) return [];
    const parsed: unknown = JSON.parse(raw);
    if (!Array.isArray(parsed)) return [];
    return parsed.filter(isDraft);
  } catch {
    // A corrupt draft is an empty draft, never a crash on page load.
    return [];
  }
}

export function saveDrafts(userId: string, drafts: DraftAgent[]): boolean {
  if (typeof window === "undefined") return false;
  try {
    window.localStorage.setItem(storageKey(userId), JSON.stringify(drafts));
    return true;
  } catch {
    return false;
  }
}

function isDraft(v: unknown): v is DraftAgent {
  if (!v || typeof v !== "object") return false;
  const d = v as Partial<DraftAgent>;
  return (
    typeof d.id === "string" &&
    typeof d.name === "string" &&
    typeof d.quality === "string" &&
    typeof d.persona === "string" &&
    typeof d.weight === "number" &&
    (d.origin === "authored" || d.origin === "template")
  );
}

export function newAgent(): DraftAgent {
  return {
    id:
      typeof crypto !== "undefined" && "randomUUID" in crypto
        ? crypto.randomUUID()
        : `agent-${Date.now()}-${Math.random().toString(36).slice(2)}`,
    name: "",
    quality: "",
    persona: "",
    weight: 0.2,
    origin: "authored",
  };
}

/**
 * The demo template set.
 *
 * Marked `origin: "template"` on every record and rendered as a TEMPLATE everywhere it
 * appears, because the standing constraint is "do not assume anything, it should all be
 * real data". A template the VC knowingly accepts and then edits is real input — they
 * chose it. A silently pre-filled council presented as though the system read it out of
 * their history is not, and that is the line this flag exists to hold.
 *
 * Four agents, not five: the ceiling is five, and leaving a slot open is what makes the
 * set read as a starting point rather than a finished council.
 */
export function templateAgents(): DraftAgent[] {
  const base = [
    {
      name: "Security posture",
      quality: "security_engineering",
      persona:
        "You add score for founders who treat security as an engineering discipline: threat models written down, dependencies pinned, an incident they can describe honestly. A product handling other people's data with no security story is a discount, not a neutral.",
      weight: 0.3,
    },
    {
      name: "Distribution reality",
      quality: "distribution",
      persona:
        "You argue from evidence about how this reaches its buyer. A named channel that already works beats a plausible one that has not been tried.",
      weight: 0.25,
    },
    {
      name: "Technical depth",
      quality: "founder_technical_depth",
      persona:
        "You back people who have built the hard part themselves. Shipped artefacts count; titles do not.",
      weight: 0.25,
    },
    {
      name: "Evidence bar",
      quality: "evidence_density",
      persona:
        "You are unmoved by narrative. State whether the claims here are backed by fetched, quotable receipts, and discount what is not.",
      weight: 0.2,
    },
  ];
  return base.map((a) => ({ ...newAgent(), ...a, origin: "template" as const }));
}
