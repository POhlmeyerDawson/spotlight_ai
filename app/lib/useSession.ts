"use client";

/**
 * One session, shared by every mounted component.
 *
 * The frame asks `/auth/me` and so do the personalisation pages. Without a shared cache
 * that is one request per component per navigation, and — worse — two components can
 * disagree about whether you are logged in, which is how a "sign out" button ends up
 * sitting next to a "sign in" link. So the answer lives in a module-level store with
 * subscribers: one in-flight request, one truth, and a `refresh()` that every listener
 * sees.
 *
 * The store starts as `null` = "not asked yet", which is deliberately distinct from
 * "anonymous". The frame renders neither the signed-in nor the signed-out affordance
 * until it knows, because flashing "sign in" at a logged-in user on every page load is
 * the tell that a session is being guessed at.
 *
 * It never throws and it never blocks the page: `getMe` resolves to the anonymous state
 * when the API is unreachable, so a dead backend costs personalisation and nothing else.
 */

import { useEffect, useState } from "react";
import { anonymous, getMe, type Me } from "./vc";

let cached: Me | null = null;
let inFlight: Promise<Me> | null = null;
const listeners = new Set<(m: Me | null) => void>();

function publish(next: Me | null) {
  cached = next;
  listeners.forEach((l) => l(next));
}

function load(): Promise<Me> {
  if (!inFlight) {
    inFlight = getMe().then((m) => {
      inFlight = null;
      publish(m);
      return m;
    });
  }
  return inFlight;
}

/** Re-reads the session and notifies every listener. Call after login, register or
 *  logout — and after a survey or upload, because those move `personalisation_enabled`. */
export function refreshSession(): Promise<Me> {
  inFlight = null;
  return load();
}

/**
 * Drops the cached session immediately, without waiting for the network. Used on logout
 * so the frame cannot keep showing an identity whose cookie is already gone.
 *
 * It publishes the ANONYMOUS state, not `null`. `null` means "not asked yet", and every
 * page renders a spinner for it — so clearing to `null` left a signed-out user watching
 * "loading your session…" forever, since nothing was going to ask again. Signing out is
 * a known state, and the honest thing to render is the signed-out page.
 */
export function clearSession() {
  inFlight = null;
  publish(anonymous("signed out — the core objective ranking is unaffected"));
}

export function useSession(): { me: Me | null; refresh: () => Promise<Me> } {
  const [me, setMe] = useState<Me | null>(cached);

  useEffect(() => {
    listeners.add(setMe);
    let live = true;
    // Deferred off the effect body: a synchronous setState here cascades a second
    // render before the browser has painted the first, for a value the initial state
    // already holds in the common case.
    void (async () => {
      await Promise.resolve();
      if (!live) return;
      if (cached) setMe(cached);
      else await load();
    })();
    return () => {
      live = false;
      listeners.delete(setMe);
    };
  }, []);

  return { me, refresh: refreshSession };
}
