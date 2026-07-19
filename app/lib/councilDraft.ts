"use client";

/**
 * Draft helpers for user-authored council agents.
 *
 * A draft is an EDIT BUFFER, not a store. Authored agents persist on the account via
 * `POST/PUT/DELETE /personal/lenses` (`api/routers/personal.py`), and the council screen
 * renders the server's `authored` list as the source of truth. This module only shapes
 * the record being typed before it is saved — which is why the localStorage layer that
 * used to live here is gone: keeping a second durable copy of the council in the browser
 * would mean two views of it that can disagree, and the API already returns the full
 * council payload on every write precisely so clients never have to reconcile.
 *
 * The shape mirrors `intelligence.custom_council.Lens` / `schema.vc.AuthoredLensWrite`:
 *   name        -> the lens label
 *   quality     -> what it looks for (the `kind` a derived lens carries)
 *   persona     -> the plain-language description the lens argues under
 *   weight      -> MIN_AUTHORED_WEIGHT..1, normalised across the council at scoring time
 *   origin      -> "authored" | "template", never "derived" (only the API derives)
 */

export const MIN_LENSES = 2;
export const MAX_LENSES = 5;
/** `schema.vc.MIN_AUTHORED_WEIGHT` — the server refuses a weight below this. */
export const MIN_AUTHORED_WEIGHT = 0.01;

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
