/**
 * The ranked list's state, shared across a navigation.
 *
 * Going list → company → back must return you to the list exactly as you left it:
 * same query, same sort, same scroll position, with the company you opened still
 * marked. Rebuilding the list from scratch on every back-navigation is what makes a
 * dashboard feel like it is fighting you.
 *
 * The company page reads the same record to offer prev/next, so stepping through the
 * ranked list never requires bouncing back to the list at all.
 *
 * sessionStorage, not a URL param or a store: it is per-tab, it survives a reload, it
 * does not need a provider, and it is the correct lifetime for "what I was looking at".
 */

const KEY = "vcbrain.pipeline.v1";

export interface ListState {
  /** The compound query text, as typed. */
  q: string;
  /** Ids matched by the last run query, or null when no query is active. */
  matched: string[] | null;
  /** Plain-English readback of the last query. Restored so the receipt survives too. */
  parsed: string | null;
  /** Sort key, as chosen in the list header. */
  sort: string;
  /** Ranked order at the moment of navigation — the order prev/next walks. */
  order: string[];
  /** The company opened from the list, so the row can be re-marked on return. */
  selected: string | null;
  scrollY: number;
}

const EMPTY: ListState = {
  q: "",
  matched: null,
  parsed: null,
  sort: "founder",
  order: [],
  selected: null,
  scrollY: 0,
};

export function readListState(): ListState {
  if (typeof window === "undefined") return EMPTY;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return EMPTY;
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== "object" || parsed === null) return EMPTY;
    // Spread over EMPTY so a record written by an older build cannot produce
    // undefined fields that then blow up a `.map` on the other side.
    return { ...EMPTY, ...(parsed as Partial<ListState>) };
  } catch {
    return EMPTY;
  }
}

export function writeListState(patch: Partial<ListState>): void {
  if (typeof window === "undefined") return;
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify({ ...readListState(), ...patch }));
  } catch {
    // Private-mode quota failures must never break navigation. Losing the restored
    // scroll position is a smaller cost than a thrown error mid-demo.
  }
}
