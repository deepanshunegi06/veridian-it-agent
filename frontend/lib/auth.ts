/* Authentication contract for the Veridian IT Desk demo UI.
 *
 * Fixed backend contract (backend remains authoritative for authZ):
 * - POST /auth/login {email, password}
 *     -> {user: {id, email, name, role, is_active}, access_token, token_type}
 *     and sets the `veridian_access` cookie.
 * - GET /auth/me -> user
 * - POST /auth/logout -> {ok: true}
 * - Roles: employee, it_agent, admin.
 *
 * Transport rules:
 * - Every API/SSE request sends `credentials: "include"` so the HttpOnly
 *   `veridian_access` cookie (primary session) is attached automatically.
 * - The Bearer `access_token` is attached when available, but only as a
 *   fallback for this local split-origin setup (Next on :3000, FastAPI on
 *   :8000). The cookie remains primary; do not store passwords anywhere.
 * - 401s clear the stale Bearer fallback and, except for the login/logout
 *   endpoints themselves, trigger a single centralized redirect to
 *   `/login?next=<current>&expired=1` (see `@/lib/session-expired`). The
 *   original ApiError(401) is still thrown so callers keep their error
 *   branches; the login page owns the expired-session alert.
 */

import {
  markSessionActive,
  redirectToLoginForExpiredSession,
} from "./session-expired";

export const API_BASE =
  process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/** Session cookie set by the backend on login. */
export const AUTH_COOKIE = "veridian_access";

/** sessionStorage key for the Bearer fallback (local split-origin only). */
const TOKEN_STORAGE_KEY = "veridian_access_token";

export type Role = "employee" | "it_agent" | "admin";

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
  is_active: boolean;
}

export interface LoginResponse {
  user: User;
  access_token: string;
  token_type: string;
}

/** Error carrying the HTTP status so callers can branch (401, 404, 409…). */
export class ApiError extends Error {
  status: number;
  constructor(what: string, status: number, body: string) {
    super(
      `${what} failed: ${status}${body ? ` — ${body.slice(0, 300)}` : ""}`,
    );
    this.name = "ApiError";
    this.status = status;
  }
}

export function isUnauthorized(err: unknown): boolean {
  return err instanceof ApiError && err.status === 401;
}

/* ---- Token store: in-memory primary, sessionStorage fallback ---- */

let memoryToken: string | null = null;

function readStoredToken(): string | null {
  if (typeof window === "undefined") return null;
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    return null;
  }
}

/** Bearer token for local/manual fallback; null when unknown (cookie still applies). */
export function getAccessToken(): string | null {
  if (memoryToken) return memoryToken;
  const stored = readStoredToken();
  if (stored) memoryToken = stored;
  return stored;
}

export function setAccessToken(token: string | null): void {
  memoryToken = token;
  // A fresh Bearer token means a fresh sign-in: re-arm the expired-session
  // single-flight guard so a later expiry redirects again. Clearing the
  // token (401/logout) never re-arms — that would allow redirect loops.
  if (token) markSessionActive();
  if (typeof window === "undefined") return;
  try {
    if (token) window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
    else window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    /* storage unavailable (private mode, SSR) — cookie remains primary */
  }
}

/** `Authorization` header for the Bearer fallback, or {} when unavailable. */
export function getAuthHeaders(): Record<string, string> {
  const token = getAccessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/* ---- Auth endpoints ---- */

export async function login(email: string, password: string): Promise<User> {
  const res = await fetch(`${API_BASE}/auth/login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) {
    throw new ApiError(
      "POST /auth/login",
      res.status,
      await res.text().catch(() => ""),
    );
  }
  const data = (await res.json()) as LoginResponse;
  if (data?.access_token) setAccessToken(data.access_token);
  return data.user;
}

/** Demo roles available for one-click sign-in (dev/demo backends only). */
export type DemoRole = "employee" | "it_agent" | "admin";

/** Passwordless demo sign-in. The backend mints the session server-side for
 * the configured demo account of the given role, so no password ever ships
 * to the browser. Non-demo backends answer 404 (surfaced as ApiError). */
export async function demoLogin(role: DemoRole): Promise<User> {
  const res = await fetch(`${API_BASE}/auth/demo-login`, {
    method: "POST",
    credentials: "include",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ role }),
  });
  if (!res.ok) {
    throw new ApiError(
      "POST /auth/demo-login",
      res.status,
      await res.text().catch(() => ""),
    );
  }
  const data = (await res.json()) as LoginResponse;
  if (data?.access_token) setAccessToken(data.access_token);
  return data.user;
}

export async function fetchMe(): Promise<User> {
  const res = await fetch(`${API_BASE}/auth/me`, {
    credentials: "include",
    headers: { ...getAuthHeaders() },
    cache: "no-store",
  });
  if (!res.ok) {
    // Drop a stale Bearer fallback so retries don't resend a dead token.
    // The cookie itself is browser-managed; backend stays authoritative.
    // An expired session also navigates once to /login?next=…&expired=1
    // (no-op on /login itself, so the login page never loops).
    if (res.status === 401) {
      setAccessToken(null);
      redirectToLoginForExpiredSession();
    }
    throw new ApiError(
      "GET /auth/me",
      res.status,
      await res.text().catch(() => ""),
    );
  }
  return (await res.json()) as User;
}

export async function logout(): Promise<void> {
  try {
    const res = await fetch(`${API_BASE}/auth/logout`, {
      method: "POST",
      credentials: "include",
      headers: { ...getAuthHeaders() },
    });
    if (!res.ok) {
      throw new ApiError(
        "POST /auth/logout",
        res.status,
        await res.text().catch(() => ""),
      );
    }
  } finally {
    setAccessToken(null);
  }
}

/* ---- Role helpers (UX gating; backend remains authoritative) ---- */

/** Roles allowed to use the IT-agent inbox. */
export const INBOX_ROLES: Role[] = ["it_agent", "admin"];

/** Roles allowed to use the admin manage area. */
export const MANAGE_ROLES: Role[] = ["admin"];

export function canAccessInbox(user: User | null | undefined): boolean {
  return !!user && INBOX_ROLES.includes(user.role);
}

export function canAccessManage(user: User | null | undefined): boolean {
  return !!user && MANAGE_ROLES.includes(user.role);
}

/** Sensible post-login landing page per role. */
export function defaultLandingForRole(role: Role): string {
  switch (role) {
    case "admin":
      return "/manage";
    case "it_agent":
      return "/inbox";
    default:
      return "/help";
  }
}

/** Guard `?next=` targets against open redirects (same-origin paths only). */
export function isSafeNextPath(next: string | null | undefined): boolean {
  if (!next) return false;
  return (
    next.startsWith("/") &&
    !next.startsWith("//") &&
    !next.startsWith("/login")
  );
}
