/* Centralized expired-session redirect for the Veridian IT Desk UI.
 *
 * Owned by auth/session UX: every authenticated JSON/SSE helper calls
 * `redirectToLoginForExpiredSession()` when the backend answers 401, so the
 * workspace never keeps an inline "session expired" message as its primary
 * UX. The browser goes to `/login` with the current path/query preserved in
 * a safe `next` param plus `expired=1`, and the login page renders the exact
 * alert there.
 *
 * Guarantees:
 * - Client-only (no-op during SSR) and never throws.
 * - No redirect when already on `/login` (prevents loops).
 * - Single-flight: concurrent 401s trigger exactly one navigation.
 * - Open-redirect safe: `next` is same-origin path only, never `/login`.
 */

export const SESSION_EXPIRED_MESSAGE =
  "Your session expired. Please sign in again.";

export const SESSION_EXPIRED_PARAM = "expired";

/** Window event fired before navigating, so AuthProvider can clear state. */
export const SESSION_EXPIRED_EVENT = "veridian:session-expired";

let redirecting = false;

/** Test-only reset for the single-flight guard. */
export function __resetSessionExpiredRedirectForTests(): void {
  redirecting = false;
}

/** Re-arm after a fresh sign-in so a later expiry redirects again.
 * Called when a new Bearer token is stored; a full `/login` reload also
 * resets module state naturally. */
export function markSessionActive(): void {
  redirecting = false;
}

function isSafeNextLocal(value: string | null | undefined): boolean {
  if (!value) return false;
  return (
    value.startsWith("/") &&
    !value.startsWith("//") &&
    !value.startsWith("/login")
  );
}

/** Navigate to `/login?next=<current>&expired=1` (once, when expired). */
export function redirectToLoginForExpiredSession(): void {
  if (typeof window === "undefined") return;
  try {
    const pathname = window.location.pathname;
    if (pathname === "/login" || pathname.startsWith("/login/")) return;
    if (redirecting) return;
    redirecting = true;
    try {
      window.dispatchEvent(new CustomEvent(SESSION_EXPIRED_EVENT));
    } catch {
      /* listeners are best-effort */
    }
    const current = `${pathname}${window.location.search}`;
    const params = new URLSearchParams();
    if (isSafeNextLocal(current)) params.set("next", current);
    params.set(SESSION_EXPIRED_PARAM, "1");
    const dest = `/login?${params.toString()}`;
    // Full reload (not router.push): guarantees proxy re-runs against the
    // current cookie and drops stale in-memory workspace state.
    try {
      // eslint-disable-next-line @next/next/no-location-assign-relative-destination
      window.location.assign(dest);
    } catch {
      try {
        // eslint-disable-next-line @next/next/no-location-assign-relative-destination
        window.location.href = dest;
      } catch {
        /* navigation unavailable (tests) — caller still throws */
      }
    }
  } catch {
    /* never throw from the redirect helper */
  }
}
