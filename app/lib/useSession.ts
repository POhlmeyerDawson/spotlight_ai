"use client";

import { useEffect, useState } from "react";

import { API_BASE } from "./api";

const SESSION_CLEARED_EVENT = "vcbrain:session-cleared";

export interface SessionUser {
  user_id: string;
  email: string;
  created_at: string;
}

export interface SessionMe {
  authenticated: boolean;
  user: SessionUser | null;
  personalisation_enabled: boolean;
  reason: string;
}

const ANONYMOUS: SessionMe = {
  authenticated: false,
  user: null,
  personalisation_enabled: false,
  reason: "no session — the core objective ranking is unaffected",
};

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseSession(value: unknown): SessionMe | null {
  if (!isObject(value) || typeof value.authenticated !== "boolean") return null;

  const user = isObject(value.user)
    ? {
        user_id: String(value.user.user_id ?? ""),
        email: String(value.user.email ?? ""),
        created_at: String(value.user.created_at ?? ""),
      }
    : null;

  return {
    authenticated: value.authenticated,
    user,
    personalisation_enabled: value.personalisation_enabled === true,
    reason: typeof value.reason === "string" ? value.reason : "",
  };
}

/** Clear the frame's cached identity after the server logout completes. */
export function clearSession(): void {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new Event(SESSION_CLEARED_EVENT));
  }
}

/**
 * Read the httpOnly session cookie through the backend. The cookie is never exposed to
 * JavaScript; this hook only consumes the deliberately anonymous-safe `/auth/me` view.
 */
export function useSession(): { me: SessionMe | null } {
  const [me, setMe] = useState<SessionMe | null>(null);

  useEffect(() => {
    let cancelled = false;

    const onCleared = () => setMe(ANONYMOUS);
    window.addEventListener(SESSION_CLEARED_EVENT, onCleared);

    fetch(`${API_BASE}/auth/me`, {
      credentials: "include",
      cache: "no-store",
      headers: { accept: "application/json" },
    })
      .then(async (response) => {
        if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
        return parseSession(await response.json());
      })
      .then((session) => {
        if (!cancelled) setMe(session ?? ANONYMOUS);
      })
      .catch(() => {
        if (!cancelled) {
          setMe({
            ...ANONYMOUS,
            reason: "session check unavailable — showing the objective ranking only",
          });
        }
      });

    return () => {
      cancelled = true;
      window.removeEventListener(SESSION_CLEARED_EVENT, onCleared);
    };
  }, []);

  return { me };
}
