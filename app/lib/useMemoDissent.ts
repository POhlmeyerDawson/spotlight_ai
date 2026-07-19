"use client";

/**
 * One memo/dissent load, shared by the two places that render it.
 *
 * The recommendation now leads the page (it is decision input #1) while the memo|dissent
 * split view stays where it is. Both need the same lock state, and the lock is the thing
 * that must not be duplicated: two independent fetches would mean two independent ideas
 * of whether the dissent had been served, and the first one to unlock would make the
 * other look wrong. So the state lives here, once, and both consumers read it.
 *
 * The lock itself is still the SERVER'S. This hook never sets `recommendation` locally;
 * it re-requests the memo with `dissent_viewed=true` and renders whatever comes back. If
 * the server keeps withholding, the page keeps showing the padlock.
 */

import { useCallback, useEffect, useState } from "react";
import { getDissent, getMemo, type Result } from "./api";
import type { Dissent, Memo } from "./types";

export interface MemoDissentState {
  memo: Result<Memo> | null;
  /** True when neither the backend nor a fixture has a memo for THIS company. */
  memoMissing: boolean;
  dissent: Result<Dissent> | null;
  dissentOpen: boolean;
  loading: boolean;
  error: string | null;
  openDissent: () => Promise<void>;
  /** Server-released recommendation text, or null while the lock holds. */
  locked: boolean;
}

export function useMemoDissent(companyId: string): MemoDissentState {
  const [memo, setMemo] = useState<Result<Memo> | null>(null);
  const [memoMissing, setMemoMissing] = useState(false);
  const [dissent, setDissent] = useState<Result<Dissent> | null>(null);
  const [dissentOpen, setDissentOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Mount-time load, always locked.
  //
  // There is deliberately no state reset at the top of this effect. The view that calls
  // this hook is keyed by company id, so a different company REMOUNTS it and every piece
  // of state starts fresh — which is what guarantees a previously-unlocked recommendation
  // can never carry over to another company. Resetting here as well would be redundant
  // and would cascade an extra render on every mount.
  useEffect(() => {
    let live = true;
    (async () => {
      const m = await getMemo(companyId, false);
      if (!live) return;
      if (m) setMemo(m);
      else setMemoMissing(true);
    })();
    return () => {
      live = false;
    };
  }, [companyId]);

  const openDissent = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const d = await getDissent(companyId);
      if ("kind" in d) {
        // Two different sentences, because they are two different claims. Saying "no
        // dissent exists" when the council simply could not run states something about
        // the company that no part of the system ever concluded.
        setError(
          d.kind === "absent"
            ? "No dissent exists for this company, so the recommendation cannot be unlocked. The lock is the server's, and this page does not work around it."
            : d.needsModel
              ? `The council could not run, so no dissent was produced: ${d.reason}. This is a gap in what we could compute, not a finding about this company — the recommendation stays locked and the axes above are unaffected.`
              : `The dissent could not be loaded: ${d.reason}. Nothing is being claimed about this company either way — the recommendation stays locked until the bear case can actually be served.`,
        );
        return;
      }
      setDissent(d);
      setDissentOpen(true);
      // Re-request the memo with dissent_viewed=true. The server decides, not us.
      const m = await getMemo(companyId, true);
      if (m) setMemo(m);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      // Always — a failed dissent must hand the button back, not grey it out forever.
      setLoading(false);
    }
  }, [companyId]);

  return {
    memo,
    memoMissing,
    dissent,
    dissentOpen,
    loading,
    error,
    openDissent,
    locked: memo === null || memo.data.recommendation === null,
  };
}
