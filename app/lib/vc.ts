import { API_BASE } from "./api";

/** End the current server-side session. Logout is intentionally idempotent. */
export async function logout(): Promise<void> {
  try {
    await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
      cache: "no-store",
      headers: { accept: "application/json" },
    });
  } catch {
    // The frame clears its local identity even when the backend is unavailable. The
    // next `/auth/me` check will report the actual session state after connectivity
    // returns, while the core product remains usable in the meantime.
  }
}
