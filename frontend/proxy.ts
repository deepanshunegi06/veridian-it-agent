/* Route protection for Veridian IT Desk (Next 16 `proxy` convention).
 *
 * Optimistic, cookie-presence gating only: the `veridian_access` HttpOnly
 * cookie set by POST /auth/login is checked, and the backend remains
 * authoritative for real authorization (roles, expiry, revocation).
 *
 * - Anonymous users visiting /help, /inbox, /manage (or subpaths) are sent
 *   to /login?next=<original path>.
 * - Authenticated users visiting /login are sent back to `?next=` when it is
 *   a safe same-origin path, else to /help — unless the visit carries
 *   `?expired=1`, which means the client just detected an expired session.
 *   The stale HttpOnly cookie can still be present at that point, so forcing
 *   a workspace redirect here would loop (workspace 401s -> /login -> back).
 *   Allowing `/login?expired=1` to render breaks the loop; the login page
 *   shows the expired-session alert and, once the session is truly valid
 *   again, normal client-side AuthProvider routing resumes.
 * - Role restrictions (/inbox: it_agent/admin, /manage: admin) are enforced
 *   page-level via <AuthGate>/canAccess* from `@/lib/auth` plus backend
 *   checks — deliberately not here, since proxy must not do session I/O.
 */

import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

const AUTH_COOKIE = "veridian_access";

const PROTECTED_PREFIXES = ["/help", "/inbox", "/manage"];

function isProtected(pathname: string): boolean {
  return PROTECTED_PREFIXES.some(
    (p) => pathname === p || pathname.startsWith(`${p}/`),
  );
}

function isSafeNext(value: string | null): boolean {
  if (!value) return false;
  return (
    value.startsWith("/") && !value.startsWith("//") && !value.startsWith("/login")
  );
}

export function proxy(request: NextRequest) {
  const { pathname, search } = request.nextUrl;
  const hasSession = Boolean(request.cookies.get(AUTH_COOKIE)?.value);

  if (!hasSession && isProtected(pathname)) {
    const url = request.nextUrl.clone();
    url.pathname = "/login";
    url.search = `?next=${encodeURIComponent(`${pathname}${search}`)}`;
    return NextResponse.redirect(url);
  }

  if (hasSession && pathname === "/login") {
    // Expired-session handoff: the client detected a 401 and navigated here
    // with ?expired=1 while the stale HttpOnly cookie may still be present.
    // Render the login alert instead of bouncing back to the workspace,
    // which would 401 again and loop. A truly valid session still leaves
    // via normal client-side routing after AuthProvider resolves.
    if (request.nextUrl.searchParams.get("expired") === "1") {
      return NextResponse.next();
    }
    const next = request.nextUrl.searchParams.get("next");
    const dest = next && isSafeNext(next) ? next : "/help";
    return NextResponse.redirect(new URL(dest, request.url));
  }

  return NextResponse.next();
}

export const config = {
  matcher: ["/help/:path*", "/inbox/:path*", "/manage/:path*", "/login"],
};
